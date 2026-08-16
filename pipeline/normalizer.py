"""
Normalisation du flux de l'agent rédactionnel.

Le prompt rédactionnel impose une sortie JSON :

    {"question": "…", "reponse_redigee": "<markdown>", "points_cles": ["…"]}

En streaming (`agent_redactionnel_stream`), `json_mode` n'est pas activé : le
modèle peut donc rendre l'objet nu **ou** enveloppé dans un bloc ```json.
Diffuser ces chunks tels quels afficherait le JSON en train de s'écrire.

`RedactionNormalizer` consomme les chunks bruts et rend du markdown propre :

    norm = RedactionNormalizer()
    for chunk in agent_redactionnel_stream(...):
        delta = norm.feed(chunk)
        if delta:
            yield delta
    texte, points = norm.finish()

Il détecte le mode sur les premiers caractères :
  * JSON       → extraction incrémentale de la valeur de `reponse_redigee` ;
  * markdown   → passthrough (filet de sécurité si le modèle sort du format,
                 et bascule possible du prompt vers du markdown pur plus tard).
"""
from __future__ import annotations

import json
import logging
import re
from typing import List, Optional, Tuple

from utils.json_utils import clean_json_codefence, lire_json_beton

logger = logging.getLogger(__name__)

# Séquences d'échappement JSON à un caractère.
_SIMPLE_ESCAPES = {
    "n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f",
    '"': '"', "\\": "\\", "/": "/",
}

# Nombre de caractères observés avant de trancher entre JSON et markdown.
_SNIFF_CHARS = 48


class JsonStringFieldExtractor:
    """Extrait au fil de l'eau la valeur (string) d'un champ d'un flux JSON.

    Machine à états SEEK → VALUE → DONE. Tolère :
      * un fence ```json en tête et n'importe quel préambule ;
      * les champs qui précèdent celui recherché (ici `question`) ;
      * le nom de la clé coupé entre deux chunks (buffer glissant `_pre`) ;
      * une séquence d'échappement coupée entre deux chunks, y compris un
        `\\uXXXX` réparti sur jusqu'à six chunks (buffer `_esc`).
    """

    def __init__(self, field: str = "reponse_redigee"):
        self._field = field
        self._key = f'"{field}"'
        self._state = "SEEK"
        self._pre = ""          # buffer de recherche de la clé
        self._esc = ""          # échappement partiel reporté au chunk suivant
        self._out: List[str] = []
        self.tail = ""          # tout ce qui suit la valeur (→ points_cles)

    # ── API publique ─────────────────────────────────────────────────────────
    @property
    def text(self) -> str:
        return "".join(self._out)

    @property
    def done(self) -> bool:
        return self._state == "DONE"

    @property
    def started(self) -> bool:
        return self._state != "SEEK"

    def feed(self, chunk: str) -> str:
        """Consomme un chunk brut et retourne le markdown décodé à émettre."""
        if not chunk:
            return ""
        if self._state == "DONE":
            self.tail += chunk
            return ""
        if self._state == "SEEK":
            chunk = self._seek(chunk)
            if chunk is None:
                return ""
        return self._consume_value(chunk)

    # ── Interne ──────────────────────────────────────────────────────────────
    def _seek(self, chunk: str) -> Optional[str]:
        """Cherche `"champ" :  "` ; retourne le reste du chunk une fois trouvé."""
        self._pre += chunk
        idx = self._pre.find(self._key)
        if idx < 0:
            # La clé peut être à cheval sur deux chunks : on garde de quoi la
            # reconstituer sans laisser le buffer croître indéfiniment.
            keep = len(self._key)
            if len(self._pre) > keep:
                self._pre = self._pre[-keep:]
            return None

        after = self._pre[idx + len(self._key):]
        quote = after.find('"')          # guillemet ouvrant de la valeur
        if quote < 0:
            return None                  # `: ` pas encore arrivé, on réessaiera
        if ":" not in after[:quote]:
            # `"reponse_redigee"` apparaissait dans une *valeur*, pas comme clé.
            self._pre = after[quote + 1:]
            return None

        self._state = "VALUE"
        rest = after[quote + 1:]
        self._pre = ""
        return rest

    def _consume_value(self, chunk: str) -> str:
        buf = self._esc + chunk
        self._esc = ""
        out: List[str] = []
        i = 0
        n = len(buf)

        while i < n:
            c = buf[i]

            if c == "\\":
                if i + 1 >= n:                       # `\` en fin de chunk
                    self._esc = buf[i:]
                    break
                nxt = buf[i + 1]
                if nxt == "u":
                    if i + 6 > n:                    # `\uXX…` incomplet
                        self._esc = buf[i:]
                        break
                    hex4 = buf[i + 2:i + 6]
                    try:
                        out.append(chr(int(hex4, 16)))
                    except ValueError:               # échappement malformé
                        out.append(buf[i:i + 6])
                    i += 6
                    continue
                out.append(_SIMPLE_ESCAPES.get(nxt, nxt))
                i += 2
                continue

            if c == '"':                             # fin de la valeur
                self._state = "DONE"
                self.tail = buf[i + 1:]
                break

            out.append(c)
            i += 1

        decoded = "".join(out)
        self._out.append(decoded)
        return decoded


class RedactionNormalizer:
    """Transforme le flux brut du rédactionnel en markdown, quel que soit son format."""

    MODE_UNKNOWN = "unknown"
    MODE_JSON = "json"
    MODE_MARKDOWN = "markdown"

    def __init__(self, field: str = "reponse_redigee"):
        self._field = field
        self.mode = self.MODE_UNKNOWN
        self._sniff = ""
        self._raw: List[str] = []
        self._extractor: Optional[JsonStringFieldExtractor] = None
        self._md: List[str] = []

    @property
    def raw(self) -> str:
        """Flux brut complet reçu du modèle (pour repli / debug)."""
        return "".join(self._raw)

    def feed(self, chunk: str) -> str:
        """Consomme un chunk brut, retourne le markdown à émettre (peut être vide)."""
        if not chunk:
            return ""
        self._raw.append(chunk)

        if self.mode == self.MODE_UNKNOWN:
            self._sniff += chunk
            if len(self._sniff) < _SNIFF_CHARS and not self._looks_decidable(self._sniff):
                return ""                       # on attend d'en voir assez
            buffered, self._sniff = self._sniff, ""
            self.mode = self._detect(buffered)
            if self.mode == self.MODE_JSON:
                self._extractor = JsonStringFieldExtractor(self._field)
            return self._dispatch(buffered)

        return self._dispatch(chunk)

    def finish(self) -> Tuple[str, List[str]]:
        """Clôt le flux et retourne (texte markdown, points_cles).

        Applique les replis si l'extraction incrémentale n'a rien produit
        (modèle sorti du format, JSON tronqué…), avec la même cascade que le
        chemin non-streamé : parse robuste, puis texte brut nettoyé.
        """
        if self.mode == self.MODE_UNKNOWN and self._sniff:
            # Réponse plus courte que la fenêtre de détection.
            self.mode = self._detect(self._sniff)
            if self.mode == self.MODE_JSON:
                self._extractor = JsonStringFieldExtractor(self._field)
            self._dispatch(self._sniff)
            self._sniff = ""

        raw = self.raw
        text = "".join(self._md) if self.mode == self.MODE_MARKDOWN else (
            self._extractor.text if self._extractor else ""
        )
        points = self._points_cles()

        if not text.strip():
            parsed = lire_json_beton(raw)
            text = (parsed.get(self._field) or parsed.get("reponse") or "").strip()
            if not points:
                points = parsed.get("points_cles") or []
            if not text:
                logger.warning(
                    "Rédactionnel — extraction vide (raw=%d chars), repli texte brut. Aperçu: %r",
                    len(raw), raw[:200],
                )
                text = clean_json_codefence(raw).strip()

        return text, points

    # ── Interne ──────────────────────────────────────────────────────────────
    def _dispatch(self, chunk: str) -> str:
        if self.mode == self.MODE_JSON:
            return self._extractor.feed(chunk)
        cleaned = self._strip_markdown_markers(chunk)
        self._md.append(cleaned)
        return cleaned

    @staticmethod
    def _looks_decidable(sniff: str) -> bool:
        """Vrai dès qu'on peut trancher sans attendre la fenêtre complète."""
        stripped = sniff.lstrip()
        return bool(stripped) and not stripped.startswith("`") and stripped[0] != "{"

    def _detect(self, head: str) -> str:
        stripped = head.lstrip()
        if stripped.startswith("{") or self._key_in(head):
            return self.MODE_JSON
        if re.match(r"^```(?:json)?\s*\{", stripped):
            return self.MODE_JSON
        if stripped.startswith("```json"):
            return self.MODE_JSON
        return self.MODE_MARKDOWN

    def _key_in(self, head: str) -> bool:
        return f'"{self._field}"' in head

    @staticmethod
    def _strip_markdown_markers(chunk: str) -> str:
        """Retire un éventuel fence ```markdown en mode passthrough."""
        return re.sub(r"^```(?:markdown|md)?\s*\n?|\n?```\s*$", "", chunk)

    def _points_cles(self) -> List[str]:
        """Récupère `points_cles` : depuis la queue du JSON, ou un bloc balisé."""
        if self.mode == self.MODE_JSON and self._extractor is not None:
            tail = self._extractor.tail
            if tail:
                match = re.search(r'"points_cles"\s*:\s*(\[.*?\])', tail, re.S)
                if match:
                    try:
                        parsed = json.loads(match.group(1))
                        if isinstance(parsed, list):
                            return [str(p) for p in parsed]
                    except json.JSONDecodeError:
                        pass
            return []

        text = "".join(self._md)
        match = re.search(r"<points_cles>(.*?)</points_cles>", text, re.S)
        if not match:
            return []
        return [
            line.lstrip("-*• ").strip()
            for line in match.group(1).splitlines()
            if line.strip()
        ]
