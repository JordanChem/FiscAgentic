"""
Fixtures de test de l'API.

Le pipeline réel n'est jamais appelé : on substitue un faux générateur
d'événements. Ce qui est testé ici, c'est la plomberie HTTP — authentification,
isolation entre utilisateurs, encodage SSE, capacité, annulation — pas la
qualité des réponses (c'est le rôle de `eval/`).
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional

import pytest

os.environ.setdefault("API_SHARED_SECRET", "secret-de-test")
os.environ.setdefault("ENVIRONMENT", "dev")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("GOOGLE_API_KEY", "goog-test")
os.environ.setdefault("SERPAPI_API_KEY", "serp-test")

API_KEY = "secret-de-test"
USER_A = {"X-API-Key": API_KEY, "X-User-Email": "a@fiscalonline.fr"}
USER_B = {"X-API-Key": API_KEY, "X-User-Email": "b@fiscalonline.fr"}


class FakeStore:
    """Substitut en mémoire de la table `conversations` de Supabase."""

    def __init__(self):
        self.rows: Dict[str, dict] = {}

    def save(self, conversation_id, messages, contexte, title=None, user_email=None):
        self.rows[conversation_id] = {
            "id": conversation_id,
            "title": title or (messages[0]["content"][:80] if messages else ""),
            "messages": messages,
            "contexte_conversation": contexte,
            "message_count": len(messages),
            "user_email": user_email,
        }
        return True

    def load(self, conversation_id, user_email=None):
        row = self.rows.get(conversation_id)
        if not row or (user_email and row.get("user_email") != user_email):
            return None
        return row

    def list(self, limit=20, user_email=None):
        return [r for r in self.rows.values() if r.get("user_email") == user_email][:limit]

    def delete(self, conversation_id, user_email=None):
        row = self.load(conversation_id, user_email)
        if row:
            del self.rows[conversation_id]
            return True
        return False


@pytest.fixture
def store(monkeypatch) -> FakeStore:
    fake = FakeStore()
    for module in ("services.chat_service", "api.routes.conversations"):
        monkeypatch.setattr(f"{module}.save_conversation", fake.save, raising=False)
        monkeypatch.setattr(f"{module}.load_conversation", fake.load, raising=False)
        monkeypatch.setattr(f"{module}.list_conversations", fake.list, raising=False)
        monkeypatch.setattr(f"{module}.delete_conversation", fake.delete, raising=False)
    return fake


@pytest.fixture
def fake_pipeline(monkeypatch):
    """Remplace `run_pipeline_stream` par un générateur déterministe."""
    from pipeline.core import PipelineResult
    from pipeline.events import ResultEvent, SourcesEvent, TextDelta, step_finished, step_started

    calls: List[dict] = []

    def _fake(question, **kwargs):
        calls.append({"question": question, **kwargs})
        cancel = kwargs.get("cancel")
        yield step_started("analyse")
        yield step_finished("analyse", 0.1)
        # Métadonnées dont les noms entrent en collision avec la signature de
        # l'encodeur SSE (`total`, `index`, `label`) : le pipeline est libre de
        # les nommer ainsi, l'encodeur doit tenir.
        yield step_started("scraping", urls=18)
        yield step_finished("scraping", 12.0, avec_contenu=15, total=18,
                            index=3, label="peu importe")
        yield SourcesEvent(sources=[{"title": "BOFiP", "url": "https://bofip.impots.gouv.fr/x",
                                     "source_domain": "bofip.impots.gouv.fr", "score": 0.95}])
        for fragment in ("## En résumé\n", "Le taux est de **20 %**.\n"):
            if cancel is not None and cancel.is_set():
                break
            yield TextDelta(fragment)
        yield ResultEvent(PipelineResult(
            question=question,
            answer_text="## En résumé\nLe taux est de **20 %**.\n",
            points_cles=["Vérifier l'ancienneté du logement"],
            analyste={"axes": ["tva"]},
            sources=[{"title": "BOFiP", "url": "https://bofip.impots.gouv.fr/x",
                      "source_domain": "bofip.impots.gouv.fr", "score": 0.95}],
            scraped_context=["contenu scrapé"],
            selected_agents=["AGENT_TVA_INDIRECTES"],
            trace_id="fisca-test-trace",
            total_cost_usd=0.0123,
        ))

    monkeypatch.setattr("services.chat_service.run_pipeline_stream", _fake)
    return calls


@pytest.fixture
def fake_followup(monkeypatch):
    """Remplace l'agent de suivi. `set_result` pilote sa réponse."""
    from pipeline.followup import FollowUpResult

    state = {"result": FollowUpResult(
        question="", answer_text="Réponse de suivi.", points_cles=["point suivi"],
        necessite_nouvelle_recherche=False, trace_id="fisca-suivi", wall_clock_s=1.0,
    )}

    def _fake(question, contexte, **kwargs):
        result = state["result"]
        result.question = question
        return result

    monkeypatch.setattr("services.chat_service.run_follow_up", _fake)

    def set_result(**kwargs):
        state["result"] = FollowUpResult(question="", **kwargs)

    return set_result


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    from api.deps import reset_rate_limiter
    from api.main import create_app
    from api.settings import reset_settings
    import api.runner as runner

    reset_settings()
    reset_rate_limiter()
    runner._pool = None
    runner._slots = None

    # raise_server_exceptions=False : on veut observer la réponse HTTP réellement
    # produite par le handler d'exception, pas voir l'exception remonter dans le test.
    with TestClient(create_app(), raise_server_exceptions=False) as test_client:
        yield test_client

    runner.shutdown_pool(wait=False)


def sse_frames(text: str) -> List[dict]:
    """Parse un corps SSE en liste de trames JSON (protocole v5)."""
    import json

    frames = []
    for line in text.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[len("data: "):]
        if payload == "[DONE]":
            frames.append({"type": "[DONE]"})
            continue
        frames.append(json.loads(payload))
    return frames


def frames_of_type(frames: List[dict], type_: str) -> List[dict]:
    return [f for f in frames if f.get("type") == type_]


def streamed_text(frames: List[dict]) -> str:
    return "".join(f.get("delta", "") for f in frames_of_type(frames, "text-delta"))


def meta_of(frames: List[dict]) -> Optional[dict]:
    metas = frames_of_type(frames, "data-meta")
    return metas[-1]["data"] if metas else None
