"""
Journalisation structurée + contexte de requête.

Chaque enregistrement porte `request_id`, `user_id`, `conversation_id` et
`trace_id` (Langfuse). Les logs par étape émis depuis `pipeline/core.py` en
héritent automatiquement : une ligne de log suffit à retrouver la trace Langfuse
correspondante, et inversement.

Les ContextVar traversent les threads dès lors que le worker est lancé via
`copy_context().run` — ce que fait `api/runner.py`.
"""
from __future__ import annotations

import json
import logging
import sys
import uuid
from contextvars import ContextVar
from typing import Optional

_request_id: ContextVar[Optional[str]] = ContextVar("request_id", default=None)
_user_id: ContextVar[Optional[str]] = ContextVar("user_id", default=None)
_conversation_id: ContextVar[Optional[str]] = ContextVar("conversation_id", default=None)
_trace_id: ContextVar[Optional[str]] = ContextVar("trace_id", default=None)

_NOISY_LIBS = ("urllib3", "httpcore", "httpx", "google", "openai", "h11",
               "LiteLLM", "hpack", "trafilatura", "charset_normalizer")

# Attributs standard de LogRecord, à exclure du dump des `extra`.
_STD_ATTRS = set(vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()) | {
    "message", "asctime", "taskName",
}


# ── Accès / mutation du contexte ─────────────────────────────────────────────
def current_request_id() -> Optional[str]:
    return _request_id.get()


def current_trace_id() -> Optional[str]:
    return _trace_id.get()


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def bind_request(request_id: str, user_id: Optional[str] = None) -> None:
    _request_id.set(request_id)
    if user_id:
        _user_id.set(user_id)


def bind_conversation(conversation_id: Optional[str]) -> None:
    _conversation_id.set(conversation_id)


def bind_trace(trace_id: Optional[str]) -> None:
    _trace_id.set(trace_id)


# ── Formatters ───────────────────────────────────────────────────────────────
class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get()
        record.user_id = _user_id.get()
        record.conversation_id = _conversation_id.get()
        record.trace_id = _trace_id.get()
        return True


class JsonFormatter(logging.Formatter):
    """Une ligne JSON par enregistrement — exploitable par n'importe quel collecteur."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("request_id", "user_id", "conversation_id", "trace_id"):
            value = getattr(record, key, None)
            if value:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key not in _STD_ATTRS and key not in payload:
                try:
                    json.dumps(value)
                    payload[key] = value
                except (TypeError, ValueError):
                    payload[key] = repr(value)
        return json.dumps(payload, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """Format lisible pour le développement local."""

    def format(self, record: logging.LogRecord) -> str:
        rid = getattr(record, "request_id", None)
        prefix = f"[{rid}] " if rid else ""
        base = super().format(record)
        return f"{prefix}{base}"


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_ContextFilter())
    handler.setFormatter(
        JsonFormatter() if fmt == "json"
        else TextFormatter("%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
                           datefmt="%H:%M:%S")
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    for lib in _NOISY_LIBS:
        logging.getLogger(lib).setLevel(logging.WARNING)
