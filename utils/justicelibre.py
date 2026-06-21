"""
Client MCP JusticeLibre — jurisprudence administrative et judiciaire française.
Endpoint : https://justicelibre.org/mcp  (gratuit, sans clé, sans compte)
Transport : Streamable HTTP / JSON-RPC 2.0
"""
import logging
import re
from typing import List, Dict, Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

JL_MCP_URL = "https://justicelibre.org/mcp"
JL_TIMEOUT = 15  # secondes par appel outil
JL_HEALTH_TIMEOUT = 5

# Nombre max d'axes transmis à CE + Cass
JL_MAX_AXES = 5
# Nombre max de concepts T0 transmis à search_admin (JADE CE+CAA)
JL_MAX_CONCEPTS = 3


class MCPClient:
    """Client JSON-RPC 2.0 minimaliste pour MCP Streamable HTTP."""

    PROTOCOL_VERSION = "2024-11-05"

    def __init__(self, url: str, timeout: int = JL_TIMEOUT):
        self.url = url
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        })
        self._id = 0
        self._mcp_session_id: Optional[str] = None

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _post(self, payload: dict) -> dict:
        headers = {}
        if self._mcp_session_id:
            headers["Mcp-Session-Id"] = self._mcp_session_id
        resp = self._session.post(self.url, json=payload, headers=headers, timeout=self.timeout)
        if "Mcp-Session-Id" in resp.headers and not self._mcp_session_id:
            self._mcp_session_id = resp.headers["Mcp-Session-Id"]
        if "text/event-stream" in resp.headers.get("Content-Type", ""):
            return self._parse_sse(resp.text)
        resp.raise_for_status()
        return resp.json()

    def _parse_sse(self, text: str) -> dict:
        import json
        for line in reversed(text.splitlines()):
            if line.startswith("data:"):
                try:
                    return json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue  # ligne tronquée — essaie la précédente
        return {"error": "no data in SSE", "raw": text[:200]}

    def initialize(self) -> dict:
        payload = {
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {
                "protocolVersion": self.PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "fisca-agent", "version": "1.0"},
            },
            "id": self._next_id(),
        }
        result = self._post(payload)
        try:
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except Exception:
            pass
        return result

    def call_tool(self, name: str, arguments: Optional[dict] = None) -> dict:
        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
            "id": self._next_id(),
        }
        result = self._post(payload)
        return result.get("result", result)


# ── Utilitaires ─────────────────────────────────────────────────────────────

def _extract_domain(url: str) -> str:
    """Extrait le domaine sans www."""
    try:
        return urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return ""


def _extract_text_from_content(content) -> str:
    """Extrait le texte d'un résultat MCP (content peut être list ou str)."""
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    if isinstance(content, dict):
        return content.get("text", str(content))
    return str(content) if content else ""


def _parse_jl_response(raw_result: dict) -> list:
    """
    Parse la réponse brute d'un outil JL (qui peut être du JSON embarqué dans
    un champ 'content' de type MCP, ou directement une liste).
    """
    import json

    # Cas 1 : résultat direct liste
    if isinstance(raw_result, list):
        return raw_result

    # Cas 2 : résultat MCP standard {content: [{type: text, text: "...json..."}]}
    content = raw_result.get("content", raw_result)
    text = _extract_text_from_content(content)
    if not text:
        return []

    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            # Certains outils renvoient {results: [...]} ou {decisions: [...]}
            for key in ("results", "decisions", "articles", "items", "data"):
                if isinstance(parsed.get(key), list):
                    return parsed[key]
            return [parsed]
    except Exception:
        pass

    return []


def _jade_url(jl_id: str) -> str:
    """Construit l'URL Légifrance pour une décision JADE (CETATEXT...) ou judiciaire (JURITEXT...)."""
    if not jl_id:
        return ""
    if jl_id.startswith(("CETA", "CAA", "JADE")):
        return f"https://www.legifrance.gouv.fr/ceta/id/{jl_id}"
    if jl_id.startswith("JURITEXT"):
        return f"https://www.legifrance.gouv.fr/juri/id/{jl_id}"
    return ""


def _clean_html(text: str) -> str:
    """Retire les balises HTML et corrige le mojibake UTF-8→Latin-1 (Ã© → é)."""
    import html
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    # Corrige le mojibake : bytes UTF-8 interprétés comme Latin-1
    try:
        text = text.encode("latin-1").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        pass
    return text.strip()


def _normalize_jl_result(raw: dict, query: str, position: int) -> dict:
    """Convertit un résultat JL au format attendu par le pipeline Fisca."""
    jl_id = raw.get("id", "")
    url = (
        raw.get("url")
        or _jade_url(jl_id)
        or ""
    )
    title = (
        raw.get("titre")
        or raw.get("title")
        or raw.get("numero")
        or jl_id
    )
    raw_snippet = (
        raw.get("extract")    # search_admin (JADE)
        or raw.get("resume")  # search_conseil_etat (ArianeWeb)
        or raw.get("snippet") # search_judiciaire_libre (Cass/CA)
        or raw.get("sommaire")
        or (raw.get("extraits", [""])[0] if raw.get("extraits") else "")
        or raw.get("texte", "")[:300]
    )
    content = (
        raw.get("texte_integral")
        or raw.get("texte")
        or ""
    )
    return {
        "query": query,
        "title": _clean_html(str(title)),
        "url": url,
        "snippet": _clean_html(str(raw_snippet))[:500],
        "source_domain": _extract_domain(url) or "legifrance.gouv.fr",
        "position": position,
        "content": content,
        "_jl_id": jl_id,
        "_jl_source": "justicelibre",
        "_jl_tool": "",
    }


def _sanitize_query(q: str) -> str:
    """
    Nettoie une requête avant envoi à JL :
    - Retire le préfixe label de l'analyste : "Requête 'Standard' : ..." ou
      "Requête Deep Dive motif redressement : ..." (avec ou sans guillemets autour du label)
    - Retire les guillemets doubles et simples (pas de phrase-search sur JADE)
    - Retire les parenthèses, crochets
    """
    # Strip "Requête <n'importe quoi> : " en début de chaîne
    q = re.sub(r"^Requ[eê]te\b[^:]*:\s*", "", q, flags=re.IGNORECASE)
    # Retire guillemets, parenthèses/crochets, virgules et signes spéciaux (FTS5 incompatibles)
    q = re.sub(r'[()[\]"\',°]', " ", q)
    return re.sub(r"\s{2,}", " ", q).strip()


# ── Fonctions principales ────────────────────────────────────────────────────

def is_jl_available(client: MCPClient) -> bool:
    """Health-check rapide : appelle about_justicelibre avec timeout court."""
    try:
        client.timeout = JL_HEALTH_TIMEOUT
        result = client.call_tool("about_justicelibre")
        client.timeout = JL_TIMEOUT
        content = _extract_text_from_content(result.get("content", ""))
        return bool(content) and "error" not in content.lower()[:50]
    except Exception as exc:
        logger.warning("JusticeLibre health-check failed: %s", exc)
        return False


def fetch_full_text(client: MCPClient, jl_id: str) -> str:
    """Récupère le texte intégral d'une décision par son ID JL."""
    if not jl_id:
        return ""
    try:
        raw = client.call_tool("get_decision_text", {"id": jl_id})
        return _extract_text_from_content(raw.get("content", ""))
    except Exception as exc:
        logger.debug("get_decision_text failed for %s: %s", jl_id, exc)
        return ""


def search_justicelibre(analyst_json: dict, client: MCPClient) -> List[Dict]:
    """
    Cherche sur JusticeLibre à partir de la sortie de l'agent analyste.

    - axes_de_recherche_serp (max JL_MAX_AXES) → search_conseil_etat + search_judiciaire_libre
    - concepts_clefs_T0 (max JL_MAX_CONCEPTS_FANOUT) → fan-out TA/CAA

    Dédoublonne par URL avant retour.
    """
    tasks = []  # (tool_name, arguments, label)

    # 1. Axes → CE (ArianeWeb) + Cass
    axes = analyst_json.get("axes_de_recherche_serp", [])
    if isinstance(axes, list):
        for axe in axes[:JL_MAX_AXES]:
            if not isinstance(axe, str) or not axe.strip():
                continue
            q = _sanitize_query(axe.strip())
            if not q:
                continue
            tasks.append(("search_conseil_etat",    {"query": q, "page": 1, "page_size": 5}, q))
            tasks.append(("search_judiciaire_libre", {"query": q, "page": 1, "page_size": 3}, q))

    # 2. Concepts T0 → search_admin (JADE = CE + CAA, recherche textuelle)
    concepts = analyst_json.get("concepts_clefs_T0", [])
    if isinstance(concepts, list):
        for concept in concepts[:JL_MAX_CONCEPTS]:
            if not isinstance(concept, str) or not concept.strip():
                continue
            q = _sanitize_query(concept.strip())
            if q:
                tasks.append(("search_admin", {"query": q, "page": 1, "page_size": 5}, q))

    if not tasks:
        logger.warning("[JusticeLibre] analyst_json vide ou sans axes/concepts — aucune task générée")
        return []

    n_axes     = min(len([a for a in axes     if isinstance(a, str) and a.strip()]), JL_MAX_AXES)
    n_concepts = min(len([c for c in concepts if isinstance(c, str) and c.strip()]), JL_MAX_CONCEPTS)
    logger.info(
        "[JusticeLibre] %d tasks (%d axes CE/Cass × 2 + %d concepts search_admin)",
        len(tasks), n_axes, n_concepts,
    )
    for i, (tool_name, args, label) in enumerate(tasks):
        logger.info("  [JL task %d/%d] %-35s | query: %s", i + 1, len(tasks), tool_name, label[:80])

    results: List[Dict] = []
    seen_urls: set = set()

    for i, (tool_name, args, label) in enumerate(tasks):
        logger.info("  [JL %d/%d] → %s  (query=%r)", i + 1, len(tasks), tool_name, args.get("query", "")[:60])
        try:
            raw = client.call_tool(tool_name, args)
            items = _parse_jl_response(raw)
            if not items:
                # Debug : afficher les clés de la réponse pour diagnostiquer le parsing
                import json as _json
                content_text = _extract_text_from_content(raw.get("content", ""))
                if content_text:
                    try:
                        parsed_debug = _json.loads(content_text)
                        top_keys = list(parsed_debug.keys()) if isinstance(parsed_debug, dict) else type(parsed_debug).__name__
                        logger.debug("  [JL %d/%d] raw keys=%s (isError=%s)", i + 1, len(tasks), top_keys, raw.get("isError"))
                    except Exception:
                        logger.debug("  [JL %d/%d] raw non-JSON: %s", i + 1, len(tasks), content_text[:120])
                else:
                    logger.debug("  [JL %d/%d] raw vide, isError=%s", i + 1, len(tasks), raw.get("isError"))
            n_new = 0
            for pos, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                norm = _normalize_jl_result(item, label, pos + 1)
                norm["_jl_tool"] = tool_name
                if norm["url"] and norm["url"] not in seen_urls:
                    seen_urls.add(norm["url"])
                    if not norm["content"] and norm["_jl_id"]:
                        norm["content"] = fetch_full_text(client, norm["_jl_id"])
                    results.append(norm)
                    n_new += 1
                elif not norm["url"] and pos == 0:
                    logger.debug("  [JL %d/%d] item[0] sans URL — clés: %s", i + 1, len(tasks), list(item.keys()))
            logger.info("  [JL %d/%d] ← %d résultats (%d nouveaux)", i + 1, len(tasks), len(items), n_new)
        except Exception as exc:
            logger.warning("  [JL %d/%d] ✗ %s — %s", i + 1, len(tasks), tool_name, exc)

    logger.info("[JusticeLibre] %d résultats au total (%d tasks)", len(results), len(tasks))
    return results
