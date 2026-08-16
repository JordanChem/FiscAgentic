"""
Encodage SSE au format Vercel AI SDK.

Deux protocoles, sélectionnés par `AI_SDK_PROTOCOL` :

* **v5 — UI Message Stream** (défaut) : SSE, une trame `data: {json}` par part
  typée (`start`, `text-delta`, `data-*`, `finish`), terminée par `data: [DONE]`.
  En-tête d'identification : `x-vercel-ai-ui-message-stream: v1`.
* **v4 — Data Stream** : lignes préfixées (`0:"texte"`, `2:[{…}]`, `d:{…}`).
  En-tête : `x-vercel-ai-data-stream: v1`.

Les deux partagent le même modèle d'événements interne, si bien que basculer de
l'un à l'autre est un changement de configuration — utile tant que la version
d'AI SDK utilisée par le front n'est pas confirmée.

Les parts de progression sont marquées `transient: true` : le front les reçoit
via `onData` sans qu'elles soient persistées dans le message. Et elles réutilisent
le même `id`, pour que le SDK réconcilie **une** part de progression au lieu d'en
empiler onze.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional

PROGRESS_PART_ID = "progress"
TEXT_PART_ID = "t0"

# `x-accel-buffering: no` est indispensable : sans lui nginx bufferise la
# réponse entière et le streaming disparaît au profit d'un bloc final.
BASE_HEADERS = {
    "cache-control": "no-cache, no-transform",
    "connection": "keep-alive",
    "x-accel-buffering": "no",
}


def sse_headers(protocol: str = "v5") -> Dict[str, str]:
    headers = dict(BASE_HEADERS)
    if protocol == "v5":
        headers["x-vercel-ai-ui-message-stream"] = "v1"
    else:
        headers["x-vercel-ai-data-stream"] = "v1"
    return headers


def _dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class SSEEncoder:
    """Traduit les événements applicatifs en trames du protocole choisi."""

    def __init__(self, protocol: str = "v5"):
        self.protocol = protocol if protocol in ("v4", "v5") else "v5"

    # ── Cycle de vie ─────────────────────────────────────────────────────────
    def start(self, message_id: str) -> str:
        if self.protocol == "v5":
            return self._frame({"type": "start", "messageId": message_id})
        return ""

    def text_start(self) -> str:
        if self.protocol == "v5":
            return self._frame({"type": "text-start", "id": TEXT_PART_ID})
        return ""

    def text_delta(self, delta: str) -> str:
        if not delta:
            return ""
        if self.protocol == "v5":
            return self._frame({"type": "text-delta", "id": TEXT_PART_ID, "delta": delta})
        return f"0:{_dumps(delta)}\n"

    def text_end(self) -> str:
        if self.protocol == "v5":
            return self._frame({"type": "text-end", "id": TEXT_PART_ID})
        return ""

    def finish(self) -> str:
        if self.protocol == "v5":
            return self._frame({"type": "finish"}) + "data: [DONE]\n\n"
        return f'd:{_dumps({"finishReason": "stop"})}\n'

    # ── Données applicatives ─────────────────────────────────────────────────
    def progress(self, step: str, label: str, status: str, progress: int,
                 index: int = 0, total: int = 0, elapsed_s: Optional[float] = None,
                 meta: Optional[Dict[str, Any]] = None) -> str:
        """Émet l'avancement d'une étape.

        `meta` est passé **explicitement**, jamais splaté : les métadonnées d'étape
        sont libres côté pipeline et une clé comme `total` (nombre d'URL scrapées)
        entrerait sinon en collision avec un paramètre de cette signature.
        """
        data = {"step": step, "label": label, "status": status, "progress": progress,
                "index": index, "total": total}
        if elapsed_s is not None:
            data["elapsed_s"] = elapsed_s
        if meta:
            data["meta"] = meta
        return self._data_part("progress", data, transient=True, part_id=PROGRESS_PART_ID)

    def sources(self, sources: List[Dict[str, Any]]) -> str:
        return self._data_part("sources", {"sources": sources})

    def points_cles(self, points: Iterable[str]) -> str:
        return self._data_part("points_cles", {"points": list(points)})

    def meta(self, **payload) -> str:
        return self._data_part("meta", payload)

    def error(self, code: str, message: str) -> str:
        if self.protocol == "v5":
            return self._frame({"type": "error", "errorText": message,
                                "errorCode": code})
        return f"3:{_dumps(message)}\n"

    def heartbeat(self) -> str:
        """Commentaire SSE : maintient la connexion pendant les étapes muettes
        (recherche, scraping) que nginx/CDN couperaient au bout de 60 s."""
        return ": ping\n\n"

    # ── Interne ──────────────────────────────────────────────────────────────
    def _data_part(self, name: str, data: Dict[str, Any], transient: bool = False,
                   part_id: Optional[str] = None) -> str:
        if self.protocol == "v5":
            frame: Dict[str, Any] = {"type": f"data-{name}", "data": data}
            if part_id:
                frame["id"] = part_id
            if transient:
                frame["transient"] = True
            return self._frame(frame)
        # v4 : « data » générique, le front discrimine sur la clé.
        return f"2:{_dumps([{name: data}])}\n"

    @staticmethod
    def _frame(payload: Dict[str, Any]) -> str:
        return f"data: {_dumps(payload)}\n\n"
