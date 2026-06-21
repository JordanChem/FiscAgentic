"""
Normalisation et matching des références d'articles / sources juridiques françaises.

Sert de brique déterministe à la métrique de couverture d'articles (eval/metrics.py) :
on veut savoir si une référence attendue (ex. "article 197 du CGI", "BOI-IR-LIQ-20-20",
"CE 13 juin 2016 n° 389134") apparaît — sous l'une quelconque de ses variantes
d'écriture — dans le texte de réponse OU dans les sources citées (URL/titre).

L'idée : réduire chaque référence à une **clé canonique** robuste aux variations
de formatage, puis comparer des ensembles de clés.
"""
from __future__ import annotations

import re
import unicodedata
from typing import List, Set

# ─── Normalisation de base ────────────────────────────────────────────────────
def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def _norm_text(s: str) -> str:
    """Minuscule, sans accents, espaces compactés."""
    s = _strip_accents(s.lower())
    s = re.sub(r"\s+", " ", s)
    return s.strip()


# Codes juridiques reconnus → abréviation canonique
_CODE_PATTERNS = [
    (r"\bcode general des impots\b|\bc\.?\s*g\.?\s*i\.?\b|\bcgi\b", "cgi"),
    (r"\blivre des procedures fiscales\b|\bl\.?\s*p\.?\s*f\.?\b|\blpf\b", "lpf"),
    (r"\bcode civil\b|\bc\.?\s*civ\.?\b", "cciv"),
    (r"\bcode de commerce\b|\bc\.?\s*com\.?\b", "ccom"),
    (r"\bcode general des collectivites territoriales\b|\bcgct\b", "cgct"),
]

# Juridictions → abréviation canonique (tolérant aux espaces/apostrophes)
_JURIS_PATTERNS = [
    (r"\bconseil d['\s]?etat\b|\bce\b", "ce"),
    (r"\bcour de cassation\b|\bcass\b", "cass"),
    (r"\bcour administrative d['\s]?appel\b|\bcaa\b", "caa"),
    (r"\btribunal administratif\b|\bta\b", "ta"),
    (r"\bcour de justice de l['\s]?union europeenne\b|\bcjue\b|\bcjce\b", "cjue"),
    (r"\bconseil constitutionnel\b|\bcons\.? const\.?\b", "ccel"),
]


def canonical_keys(reference: str) -> Set[str]:
    """Retourne l'ensemble des clés canoniques extraites d'une référence brute.

    Une même référence peut produire plusieurs clés (ex. un BOFiP + un numéro
    d'article cités ensemble). Vide si rien d'exploitable.
    """
    if not reference:
        return set()
    txt = _norm_text(reference)
    keys: Set[str] = set()

    # 1) Références BOFiP : BOI-XXX-YYY-10-20-30 (avec ou sans §)
    #    Structure : code en lettres (BOI-RSA-BASE…) PUIS segments numériques.
    #    L'ordre lettres→chiffres évite d'avaler la prose qui suit la référence
    #    (ex. "BOI-IF-TH-10-10 La loi…" ne capte pas "La").
    for m in re.finditer(r"\bboi(?:[-\s][a-z]{1,6}){1,6}(?:[-\s]\d{1,4}){0,8}", txt):
        boi = re.sub(r"\s+", "-", m.group(0).strip())
        boi = re.sub(r"-+", "-", boi)
        keys.add(boi)

    # 2) Numéros de décision de jurisprudence : n° 389134, C-123/45 (CJUE)
    juris_codes = []
    for jp, abbr in _JURIS_PATTERNS:
        if re.search(jp, txt):
            juris_codes.append(abbr)
    # numéro de pourvoi/requête — on émet TOUJOURS une clé générique "juris#NUM"
    # (le numéro à ≥4 chiffres est très discriminant) + une clé par juridiction détectée.
    for m in re.finditer(r"n[°o]\s*([0-9]{4,})", txt):
        num = m.group(1)
        keys.add(f"juris#{num}")
        for abbr in juris_codes:
            keys.add(f"{abbr}#{num}")
    # affaire CJUE format C-xxx/yy
    for m in re.finditer(r"\bc[-\s]?([0-9]{1,4})/([0-9]{2,4})", txt):
        keys.add(f"cjue#c{m.group(1)}/{m.group(2)}")

    # 3) Articles de code : "article 197 du cgi", "art l64 lpf", "787 c cgi", "150-0 b ter"
    codes_present = [abbr for pat, abbr in _CODE_PATTERNS if re.search(pat, txt)]
    # n° d'article : préfixe legislatif optionnel (L./R./A./D.), tirets, bis/ter…,
    # lettre de subdivision finale (ex. "787 C") — mais PAS suivie d'une autre lettre
    # (sinon on capterait le "c" de "cgi").
    num_pat = (
        r"([lradLRAD]\.?\s?)?"                     # préfixe legislatif éventuel
        r"(\d+(?:[-\s]?\d+)*"                       # numéro (avec tirets)
        r"(?:\s?[a-z](?![a-z])(?:\s?(?:bis|ter|quater|quinquies))?)?)"  # subdivision
    )
    art_re = re.compile(r"\bart(?:icle)?s?\.?\s*" + num_pat)
    # variante "bare" : préfixe legislatif obligatoire (ex. "L. 64", "R 207") sans le mot "article"
    bare_re = re.compile(r"\b([lrad])\.?\s?(\d+(?:[-\s]?\d+)*)\b")

    def _add_art(prefix, body):
        art = re.sub(r"[.\s]+", "", (prefix or "") + body)
        if codes_present:
            for code in codes_present:
                keys.add(f"{code}:{art}")
        else:
            keys.add(f"art:{art}")

    for m in art_re.finditer(txt):
        _add_art(m.group(1), m.group(2))
    for m in bare_re.finditer(txt):
        _add_art(m.group(1), m.group(2))

    # 4) À défaut de tout repère structuré : repli sur les mots significatifs
    #    (évite qu'une attente "purement textuelle" ne matche jamais).
    if not keys:
        tokens = [t for t in re.findall(r"[a-z0-9]{4,}", txt)]
        if tokens:
            keys.add("kw:" + "_".join(sorted(set(tokens))[:6]))

    return keys


def reference_found(expected_ref: str, haystacks: List[str]) -> bool:
    """True si la référence attendue est retrouvée dans l'un des textes fournis.

    `haystacks` = morceaux où chercher (texte de réponse, titres + URLs des sources…).
    Le matching est positif si au moins une clé canonique de la référence attendue
    apparaît dans les clés canoniques de l'un des haystacks, OU si sa clé est
    contenue textuellement (pour les BOFiP/articles écrits tels quels dans une URL).
    """
    exp_keys = canonical_keys(expected_ref)
    if not exp_keys:
        return False

    big_norm = _norm_text(" ".join(haystacks))
    hay_keys: Set[str] = set()
    for h in haystacks:
        hay_keys |= canonical_keys(h)

    for k in exp_keys:
        if k in hay_keys:
            return True
        # match textuel direct pour les clés "lisibles" (BOFiP surtout)
        if k.startswith("boi") and k.replace("-", "") in big_norm.replace("-", "").replace(" ", ""):
            return True
    return False


def coverage(expected_refs: List[str], haystacks: List[str]) -> dict:
    """Calcule le recall de couverture des références attendues.

    Returns: {"recall": float, "found": [...], "missing": [...]}
    """
    found, missing = [], []
    for ref in expected_refs:
        (found if reference_found(ref, haystacks) else missing).append(ref)
    total = len(expected_refs)
    recall = (len(found) / total) if total else 1.0
    return {"recall": recall, "found": found, "missing": missing}
