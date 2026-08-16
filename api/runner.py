"""
Exécution du pipeline sous FastAPI : pont entre un générateur **bloquant** et la
boucle asyncio.

⚠️ Pourquoi ce détour plutôt que `StreamingResponse(<générateur sync>)` ?

Starlette enveloppe un générateur synchrone dans `iterate_in_threadpool`, qui
appelle `anyio.to_thread.run_sync(next, it)` **une fois par élément**. anyio
copie le contexte à chaque appel et exécute le worker via `context.run(...)` :
les mutations de `ContextVar` faites dans le générateur ne remontent donc pas,
et l'itération suivante repart d'une copie fraîche.

Conséquence concrète : le `_run_ctx.set()` de `utils.llm.llm_trace` serait perdu
au premier `yield`. Tous les `trace_step`, l'agrégation de coût et
`finalize_trace` deviendraient des no-op **silencieux** — coût et tokens à zéro,
trace Langfuse vide, sans la moindre erreur. C'est exactement le genre de panne
qu'on ne remarque qu'au moment de la facture.

Solution : le pipeline tourne intégralement dans **un seul** thread dédié (donc
un contexte stable), et les événements traversent vers la boucle par
`loop.call_soon_threadsafe`. Les `copy_context().run` internes du pipeline
(spécialistes, recherche, scraping, FiscalOnline) continuent de fonctionner tels
quels.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import AsyncIterator, Callable, Iterator, Optional

from api.errors import capacity_exceeded
from api.settings import get_settings
from pipeline.errors import PipelineCancelled, PipelineDeadlineExceeded
from pipeline.events import PipelineEvent

logger = logging.getLogger(__name__)

_DONE = object()

# Pool DÉDIÉ, et non celui de Starlette : ce dernier partage un limiteur de 40
# jetons avec tous les endpoints `def`. Un pipeline de 5 minutes par jeton
# affamerait les routes CRUD.
_pool: Optional[ThreadPoolExecutor] = None
_slots: Optional[asyncio.Semaphore] = None
_slots_lock = threading.Lock()


def get_pool() -> ThreadPoolExecutor:
    global _pool
    with _slots_lock:
        if _pool is None:
            settings = get_settings()
            _pool = ThreadPoolExecutor(
                max_workers=settings.max_concurrent_pipelines,
                thread_name_prefix="pipeline",
            )
    return _pool


def get_slots() -> asyncio.Semaphore:
    global _slots
    if _slots is None:
        _slots = asyncio.Semaphore(get_settings().max_concurrent_pipelines)
    return _slots


def free_slots() -> int:
    sem = get_slots()
    return getattr(sem, "_value", 0)


def shutdown_pool(wait: bool = True) -> None:
    """Arrêt gracieux : laisse les pipelines en cours finaliser leur trace."""
    global _pool
    if _pool is not None:
        _pool.shutdown(wait=wait, cancel_futures=not wait)
        _pool = None


class PipelineSlot:
    """Réserve une place d'exécution ; rend un 429 propre si la file est pleine.

    Acquis **avant** l'envoi des en-têtes SSE : à ce stade on peut encore
    répondre en JSON, ce qui ne serait plus possible une fois le flux ouvert.
    """

    def __init__(self):
        self._acquired = False

    async def __aenter__(self) -> "PipelineSlot":
        settings = get_settings()
        try:
            await asyncio.wait_for(get_slots().acquire(), settings.slot_acquire_timeout_s)
        except asyncio.TimeoutError:
            logger.warning("Capacité saturée : %d pipelines simultanés",
                           settings.max_concurrent_pipelines)
            raise capacity_exceeded()
        self._acquired = True
        return self

    async def __aexit__(self, *_exc) -> None:
        self.release()

    def release(self) -> None:
        if self._acquired:
            self._acquired = False
            get_slots().release()


async def stream_events(
    make_iterator: Callable[[threading.Event], Iterator[PipelineEvent]],
) -> AsyncIterator[PipelineEvent]:
    """Exécute un générateur bloquant dans un thread et relaie ses événements.

    Args:
        make_iterator: fabrique appelée dans le thread worker, recevant le
            `threading.Event` d'annulation à passer au pipeline.

    L'annulation de la tâche asyncio (déconnexion du client) positionne l'event :
    le pipeline s'arrête au prochain point de contrôle, finalise sa trace, et le
    thread est rejoint avant de rendre la main.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=512)
    cancel = threading.Event()

    def _worker() -> None:
        iterator = make_iterator(cancel)
        try:
            for event in iterator:
                loop.call_soon_threadsafe(queue.put_nowait, event)
        except (PipelineCancelled, PipelineDeadlineExceeded) as exc:
            logger.info("Pipeline interrompu : %s", exc)
            loop.call_soon_threadsafe(queue.put_nowait, exc)
        except BaseException as exc:  # noqa: BLE001 — relayé tel quel à l'appelant
            logger.exception("Pipeline — échec dans le thread worker")
            loop.call_soon_threadsafe(queue.put_nowait, exc)
        finally:
            # close() lève GeneratorExit dans le générateur s'il est encore
            # suspendu → finalize_trace s'exécute même sur abandon.
            try:
                iterator.close()
            except Exception:  # pragma: no cover
                logger.debug("Fermeture du générateur de pipeline en échec", exc_info=True)
            loop.call_soon_threadsafe(queue.put_nowait, _DONE)

    # copy_context() capturé sur la boucle : request_id / user_id suivent le
    # worker et apparaissent dans les logs émis par le pipeline.
    ctx = contextvars.copy_context()
    future = loop.run_in_executor(get_pool(), lambda: ctx.run(_worker))

    heartbeat = get_settings().sse_heartbeat_s
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), heartbeat)
            except asyncio.TimeoutError:
                yield None          # signal de keep-alive pour l'encodeur SSE
                continue
            if event is _DONE:
                return
            if isinstance(event, BaseException):
                raise event
            yield event
    finally:
        cancel.set()
        # shield : sans lui, l'annulation se propagerait au join et on
        # abandonnerait un thread qui détient encore une place d'exécution.
        try:
            await asyncio.shield(future)
        except (PipelineCancelled, PipelineDeadlineExceeded):
            pass
        except Exception:  # pragma: no cover
            logger.debug("Jointure du thread de pipeline en échec", exc_info=True)
