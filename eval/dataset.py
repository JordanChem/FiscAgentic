"""
Chargement du golden dataset (questions + éléments attendus + articles attendus).

Le fichier source de l'utilisateur peut être en CSV / Excel / JSON / JSONL, avec des
noms de colonnes variables. On le mappe vers un schéma canonique interne `GoldenCase`,
puis (optionnellement) vers des objets deepeval (`Golden` / `LLMTestCase`).

➜ POUR BRANCHER VOTRE FICHIER : éditez `DEFAULT_COLUMN_MAP` ci-dessous (ou passez
   `column_map=...` à `load_golden`) pour faire correspondre VOS noms de colonnes.
   Un modèle vide est fourni : eval/golden_dataset.example.csv
"""
from __future__ import annotations

import ast
import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# ─── Schéma canonique interne ─────────────────────────────────────────────────
@dataclass
class GoldenCase:
    id: str
    question: str
    expected_elements: List[str] = field(default_factory=list)
    expected_articles: List[str] = field(default_factory=list)
    domaine: Optional[str] = None
    difficulte: Optional[str] = None
    is_follow_up: bool = False
    notes: Optional[str] = None


# Correspondance "champ canonique → noms de colonnes possibles dans VOTRE fichier".
# Le premier nom trouvé (insensible à la casse/accents) est utilisé.
# Calé sur golden_dataset_3.xlsx : Difficulté | Thème | Question Utilisateur |
# Sources attendues | Contenus attendus de la réponse | Sources | Qualité.
DEFAULT_COLUMN_MAP: Dict[str, List[str]] = {
    "id":                ["id", "identifiant", "ref", "numero", "n°"],
    "question":          ["question utilisateur", "question", "questions", "query", "prompt", "intitule"],
    "expected_elements": ["contenus attendus de la reponse", "contenus attendus",
                          "expected_elements", "elements_attendus", "elements attendus",
                          "reponse attendue", "points_attendus", "attendu"],
    "expected_articles": ["sources attendues", "expected_articles", "articles_attendus",
                          "articles attendus", "articles", "references"],
    "domaine":           ["theme", "domaine", "domain", "categorie", "category"],
    "difficulte":        ["difficulte", "niveau", "difficulty"],
    "is_follow_up":      ["is_follow_up", "follow_up", "suivi"],
    "notes":             ["notes", "commentaire", "comment", "remarque"],
}

# Séparateurs acceptés pour transformer une cellule texte en liste.
_LIST_SEP_RE = re.compile(r"[\n;|•]+|(?:^|\s)-\s")
# Préfixe d'énumération à retirer en tête d'item : "1. ", "2) ", "3 - "…
_ENUM_PREFIX_RE = re.compile(r"^\s*\d+\s*[.)\-]\s*")


def _split_list(value) -> List[str]:
    """Transforme une cellule (str / liste / JSON) en liste de chaînes nettoyées."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    s = str(value).strip()
    if not s or s.lower() in ("nan", "none", "null"):
        return []
    # liste JSON / littéral Python ?
    if s[0] in "[(":
        try:
            parsed = ast.literal_eval(s)
            if isinstance(parsed, (list, tuple)):
                return [str(v).strip() for v in parsed if str(v).strip()]
        except Exception:
            pass
    parts = _LIST_SEP_RE.split(s)
    out = []
    for p in parts:
        if not p:
            continue
        item = _ENUM_PREFIX_RE.sub("", p).strip(" -–\t")
        if item:
            out.append(item)
    return out


def _norm_col(name: str) -> str:
    import unicodedata
    n = "".join(c for c in unicodedata.normalize("NFD", str(name).lower())
                if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", n).strip()


def _resolve_columns(available: List[str], column_map: Dict[str, List[str]]) -> Dict[str, Optional[str]]:
    """Associe chaque champ canonique à une colonne réelle du fichier (ou None)."""
    norm_to_real = {_norm_col(c): c for c in available}
    resolved: Dict[str, Optional[str]] = {}
    for field_name, candidates in column_map.items():
        real = None
        for cand in candidates:
            real = norm_to_real.get(_norm_col(cand))
            if real:
                break
        resolved[field_name] = real
    return resolved


def _read_rows(path: str) -> List[dict]:
    """Lit le fichier en liste de dicts, quel que soit le format."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".csv", ".tsv"):
        import pandas as pd
        sep = "\t" if ext == ".tsv" else None
        df = pd.read_csv(path, sep=sep, engine="python")
        return df.to_dict(orient="records")
    if ext in (".xlsx", ".xls"):
        import pandas as pd
        df = pd.read_excel(path)
        return df.to_dict(orient="records")
    if ext == ".jsonl":
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
    if ext == ".json":
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else data.get("data", data.get("questions", []))
    raise ValueError(f"Format non supporté : {ext} (csv/tsv/xlsx/xls/json/jsonl)")


def load_golden(path: str, column_map: Optional[Dict[str, List[str]]] = None) -> List[GoldenCase]:
    """Charge le golden dataset depuis `path` et renvoie une liste de GoldenCase."""
    column_map = column_map or DEFAULT_COLUMN_MAP
    rows = _read_rows(path)
    if not rows:
        return []
    resolved = _resolve_columns(list(rows[0].keys()), column_map)
    if not resolved.get("question"):
        raise ValueError(
            f"Colonne 'question' introuvable. Colonnes du fichier : {list(rows[0].keys())}\n"
            "→ Ajustez DEFAULT_COLUMN_MAP dans eval/dataset.py."
        )

    cases: List[GoldenCase] = []
    for i, row in enumerate(rows):
        def cell(field_name):
            col = resolved.get(field_name)
            return row.get(col) if col else None

        question = str(cell("question") or "").strip()
        if not question:
            continue
        raw_followup = cell("is_follow_up")
        cases.append(GoldenCase(
            id=str(cell("id") or f"q{i+1}"),
            question=question,
            expected_elements=_split_list(cell("expected_elements")),
            expected_articles=_split_list(cell("expected_articles")),
            domaine=(str(cell("domaine")).strip() if cell("domaine") else None),
            difficulte=(str(cell("difficulte")).strip() if cell("difficulte") else None),
            is_follow_up=str(raw_followup).strip().lower() in ("true", "1", "oui", "yes") if raw_followup else False,
            notes=(str(cell("notes")).strip() if cell("notes") else None),
        ))
    return cases


def stratified_sample(cases: List[GoldenCase], n: int, key: str = "difficulte") -> List[GoldenCase]:
    """Sous-échantillon de `n` cas équilibré selon `key` (par défaut la difficulté).

    DÉTERMINISTE : renvoie toujours le même sous-ensemble pour un même (dataset, n) —
    indispensable pour comparer plusieurs configs de modèles sur LES MÊMES questions.

    - répartition proportionnelle à la taille de chaque strate (méthode du plus fort reste) ;
    - sélection à pas régulier dans chaque strate (triée par id), sans aléatoire.
    """
    import math
    from collections import defaultdict

    if n >= len(cases) or n <= 0:
        return cases

    groups: dict = defaultdict(list)
    for c in cases:
        groups[(getattr(c, key, None) or "inconnu")].append(c)

    total = len(cases)
    raw = {k: len(v) * n / total for k, v in groups.items()}
    alloc = {k: int(math.floor(r)) for k, r in raw.items()}
    # distribue le reste aux strates avec la plus grande partie fractionnaire
    for k in sorted(groups, key=lambda k: raw[k] - alloc[k], reverse=True)[: n - sum(alloc.values())]:
        alloc[k] += 1

    selected_ids = set()
    for k in sorted(groups):  # ordre stable des strates
        grp = sorted(groups[k], key=lambda c: c.id)
        m = alloc.get(k, 0)
        if m <= 0:
            continue
        if m >= len(grp):
            selected_ids.update(c.id for c in grp)
            continue
        idxs = sorted({int((i + 0.5) * len(grp) / m) for i in range(m)})
        i = 0
        while len(idxs) < m and i < len(grp):  # comble si collisions
            if i not in idxs:
                idxs.append(i)
            i += 1
        selected_ids.update(grp[j].id for j in sorted(idxs)[:m])

    return [c for c in cases if c.id in selected_ids]  # conserve l'ordre d'origine


def to_deepeval_goldens(cases: List[GoldenCase]):
    """Convertit en `deepeval.dataset.Golden` (input = question, attendus en metadata)."""
    from deepeval.dataset import Golden
    goldens = []
    for c in cases:
        goldens.append(Golden(
            input=c.question,
            additional_metadata={
                "id": c.id,
                "expected_elements": c.expected_elements,
                "expected_articles": c.expected_articles,
                "domaine": c.domaine,
            },
        ))
    return goldens
