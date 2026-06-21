"""
Métriques d'évaluation de la qualité du pipeline (deepeval).

1. `ArticleCoverageMetric` — DÉTERMINISTE (sans LLM) : recall des articles attendus
   retrouvés dans la réponse + les sources citées (via eval/articles.py).
2. `make_element_coverage_metric` — GEval (LLM-juge) : la réponse couvre-t-elle les
   éléments attendus ?
3. `make_faithfulness_metric` — Faithfulness deepeval : la réponse n'invente-t-elle
   pas de faits/citations hors des sources scrapées ?

Le juge LLM passe par `LiteLLMJudge`, qui réutilise `utils.llm.llm_call` → le juge est
donc lui aussi provider-agnostique (Gemini/OpenAI/Anthropic) et tracé/chiffré comme le
reste du pipeline.
"""
from __future__ import annotations

from typing import List, Optional

from deepeval.metrics import BaseMetric, GEval, FaithfulnessMetric
from deepeval.models import DeepEvalBaseLLM
from deepeval.test_case import LLMTestCaseParams

from eval.articles import coverage
from utils.json_utils import lire_json_beton
from utils.llm import llm_call


# ─── Couverture des articles par juge LLM (pas de regex : les noms peuvent tromper) ─
def _llm_article_coverage(question: str, expected_articles: List[str],
                          source_blobs: List[str], judge_model: str,
                          api_key: Optional[str] = None) -> dict:
    """Juge LLM : parmi les `expected_articles` attendus, lesquels sont présents dans la
    LISTE DES SOURCES conservées (titres/extraits/URLs) ?

    Le juge reçoit EXACTEMENT : la question, les articles attendus, la liste des sources.
    (Pas de regex : un numéro peut être trompeur ; le juge gère LEGIARTI, variantes BOFiP,
    intitulés équivalents.) Returns {"recall", "found", "missing"}.
    """
    refs = "\n".join(f"- {r}" for r in expected_articles)
    sources_text = "\n".join(f"- {b}" for b in source_blobs if b and b.strip()) or "(aucune source)"
    prompt = (
        "Tu vérifies si des références juridiques fiscales françaises ATTENDUES sont "
        "présentes dans la LISTE DES SOURCES conservée par le pipeline pour répondre à "
        "une question.\n\n"
        "Pour CHAQUE référence attendue, indique si une source de la liste y correspond — "
        "même si la formulation diffère : numéro d'article vs identifiant d'URL (ex. "
        "Legifrance LEGIARTI…), variante de référence BOFiP (BOI-…), intitulé équivalent, "
        "abréviation. Ne considère présente QUE si une source correspond vraiment à CETTE "
        "référence (pas une simple proximité thématique).\n\n"
        f"QUESTION :\n{question}\n\n"
        f"RÉFÉRENCES ATTENDUES :\n{refs}\n\n"
        f"SOURCES CONSERVÉES :\n{sources_text}\n\n"
        'Réponds en JSON strict : {"trouvees": ["...référence attendue présente..."], '
        '"manquantes": ["...référence absente..."]}. Recopie les références telles quelles.'
    )
    res = llm_call(judge_model, prompt=prompt, json_mode=True,
                   api_key=api_key, agent_name="judge_articles")
    data = lire_json_beton(res.text)
    exp_set = set(expected_articles)
    found = [r for r in (data.get("trouvees", []) if isinstance(data, dict) else []) if r in exp_set]
    found = list(dict.fromkeys(found))  # dédoublonne en gardant l'ordre
    missing = [a for a in expected_articles if a not in set(found)]
    recall = (len(found) / len(expected_articles)) if expected_articles else 1.0
    return {"recall": recall, "found": found, "missing": missing}


def article_coverage(question: str, expected_articles: List[str], source_blobs: List[str],
                     judge_model: Optional[str] = None,
                     api_key: Optional[str] = None) -> dict:
    """Recall des articles attendus dans les SOURCES conservées.

    - `judge_model` fourni → juge LLM pur (recommandé) ;
    - sinon → repli regex déterministe (mode hors-ligne / sans juge).

    Returns: {"recall", "found", "missing"}.
    """
    if not expected_articles:
        return {"recall": 1.0, "found": [], "missing": []}
    if judge_model:
        try:
            return _llm_article_coverage(question, expected_articles, source_blobs, judge_model, api_key)
        except Exception:
            pass  # repli regex en cas d'échec du juge
    return coverage(expected_articles, source_blobs)


# ─── Juge LLM provider-agnostique (réutilise notre couche litellm) ────────────
class LiteLLMJudge(DeepEvalBaseLLM):
    """Modèle-juge pour deepeval, branché sur utils.llm.llm_call."""

    def __init__(self, model: str = "gpt-4o", api_key: Optional[str] = None):
        self.model = model
        self.api_key = api_key

    def load_model(self):
        return self

    def get_model_name(self) -> str:
        return self.model

    def generate(self, prompt: str, schema=None, *args, **kwargs):
        # deepeval passe parfois un schéma pydantic pour forcer une sortie structurée.
        if schema is not None:
            res = llm_call(self.model, prompt=prompt, json_mode=True,
                           api_key=self.api_key, agent_name="judge")
            try:
                return schema.model_validate_json(res.text)
            except Exception:
                data = lire_json_beton(res.text)
                return schema(**data)
        res = llm_call(self.model, prompt=prompt, api_key=self.api_key, agent_name="judge")
        return res.text

    async def a_generate(self, prompt: str, schema=None, *args, **kwargs):
        return self.generate(prompt, schema, *args, **kwargs)


# ─── 1. Couverture d'articles (déterministe) ──────────────────────────────────
class ArticleCoverageMetric(BaseMetric):
    """Recall des `expected_articles` dans la LISTE DES SOURCES retournées par le pipeline.

    ⚠️ On regarde les **sources conservées après ranking** (titres + extraits + URLs),
    PAS le texte de la réponse rédigée : on évalue la recherche, pas la rédaction.
    Lit : `test_case.input` (question), `additional_metadata['expected_articles']`,
    `additional_metadata['kept_articles']` (titre + URL des sources conservées).

    Si `judge_model` est fourni → **juge LLM pur** (pas de regex : les noms peuvent
    tromper). Sinon → repli regex déterministe (hors-ligne).
    """

    def __init__(self, threshold: float = 0.7, judge_model: Optional[str] = None,
                 api_key: Optional[str] = None):
        self.threshold = threshold
        self.judge_model = judge_model
        self.api_key = api_key
        self.async_mode = False
        self.include_reason = True
        self.strict_mode = False
        self.evaluation_model = f"LLM ({judge_model})" if judge_model else "déterministe (regex)"
        self.score = 0.0
        self.reason = ""
        self.success = False
        self.error = None

    def measure(self, test_case, *args, **kwargs) -> float:
        meta = getattr(test_case, "additional_metadata", None) or {}
        expected = meta.get("expected_articles", []) or []
        source_blobs = list(meta.get("kept_articles", []) or [])

        cov = article_coverage(test_case.input, expected, source_blobs,
                               judge_model=self.judge_model, api_key=self.api_key)
        self.score = cov["recall"]
        self.success = self.score >= self.threshold
        if expected:
            self.reason = (f"{len(cov['found'])}/{len(expected)} articles attendus présents "
                           f"dans les sources. Manquants : {cov['missing'] or 'aucun'}.")
        else:
            self.reason = "Aucun article attendu pour ce cas."
        return self.score

    async def a_measure(self, test_case, *args, **kwargs) -> float:
        return self.measure(test_case, *args, **kwargs)

    def is_successful(self) -> bool:
        return self.success

    @property
    def __name__(self):
        return "Couverture des articles"


# ─── 2. Couverture des éléments attendus (GEval, LLM-juge) ────────────────────
def make_element_coverage_metric(judge_model: str = "gpt-4o", threshold: float = 0.7,
                                 api_key: Optional[str] = None) -> GEval:
    """GEval : la réponse couvre-t-elle (correctement) les éléments attendus ?

    Les éléments attendus doivent être placés dans `test_case.expected_output`
    (voir eval/run_eval.py).
    """
    return GEval(
        name="Couverture des éléments attendus",
        criteria=(
            "Évalue dans quelle mesure la RÉPONSE (actual_output) couvre, de façon "
            "fiscalement correcte, CHACUN des éléments attendus listés dans "
            "'expected_output'. Score 1 si tous les éléments attendus sont présents et "
            "exacts ; score proportionnellement réduit pour chaque élément manquant, "
            "incomplet ou fiscalement erroné. Ne pénalise PAS la présence d'informations "
            "supplémentaires correctes."
        ),
        evaluation_params=[
            LLMTestCaseParams.INPUT,
            LLMTestCaseParams.ACTUAL_OUTPUT,
            LLMTestCaseParams.EXPECTED_OUTPUT,
        ],
        model=LiteLLMJudge(judge_model, api_key=api_key),
        threshold=threshold,
    )


# ─── 3. Fidélité aux sources (anti-hallucination) ─────────────────────────────
def make_faithfulness_metric(judge_model: str = "gpt-4o", threshold: float = 0.7,
                             api_key: Optional[str] = None) -> FaithfulnessMetric:
    """Faithfulness : la réponse est-elle fidèle au `retrieval_context` (sources scrapées) ?"""
    return FaithfulnessMetric(
        threshold=threshold,
        model=LiteLLMJudge(judge_model, api_key=api_key),
        include_reason=True,
    )
