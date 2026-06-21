"""
Agent Orchestrateur : Route la question vers les agents spécialisés appropriés
"""
import os
import logging
from utils.llm import llm_call

logger = logging.getLogger(__name__)


def agent_orchestrateur(user_question, analyst_results, api_key=None, model_name="gpt-4o"):
    """
    Appelle GPT d'OpenAI pour router une question fiscale vers les bons agents spécialisés,
    selon le prompt détaillé fourni.
    Retourne la réponse JSON stricte du modèle.

    Args:
        user_question: Question de l'utilisateur
        analyst_results: Résultats de l'agent analyste
        api_key: Clé API OpenAI
        model_name: Nom du modèle à utiliser. Par défaut "gpt-4o".
    """
    # Priorité à l'argument api_key ; sinon cherche dans l'env ; sinon raise explicite !
    api_key = api_key or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError(
            "L'API key OpenAI n'est pas définie !\n"
            "Définissez-la en passant api_key en paramètre ou en définissant la variable d'environnement OPENAI_API_KEY."
        )
    prompt = f"""Tu es une IA experte en fiscalité française ET en triage de questions vers des experts métier.

    🎯 TA MISSION
    On te donne une question d'utilisateur ET une ANALYSE PRÉLIMINAIRE technique (concepts, seuils, projections).

    Tu dois :
    1) Analyser les besoins techniques identifiés dans l'analyse préliminaire.
    2) Traduire ces besoins en scores (0 à 1) pour CHACUN des 11 agents spécialisés.
    3) Retourner 1 à 4 agents à appeler en priorité pour traduire ces concepts en sources juridiques réelles.

    ⚙️ LES 11 AGENTS SPÉCIALISÉS
    1️⃣ AGENT_PARTICULIERS_REVENUS : IR, revenus catégoriels, fiscalité personnelle, PV de titres (particuliers).
    2️⃣ AGENT_TVA_INDIRECTES : TVA (intracom, autoliquidation), taxes indirectes.
    3️⃣ AGENT_ENTREPRISES_IS : IS courant, résultat fiscal, intégration fiscale, dividendes.
    4️⃣ AGENT_PATRIMOINE_TRANSMISSION : Successions, donations, IFI, démembrement, ISF, PV mobilières (patrimonial).
    5️⃣ AGENT_STRUCTURES_MONTAGES : Abus de droit, restructurations (fusions/LBO), montages complexes.
    6️⃣ AGENT_INTERNATIONAL : Résidence fiscale, Exit Tax, Conventions, Établissement stable, flux transfrontaliers.
    7️⃣ AGENT_DROIT_EUROPEEN : Conformité UE, CJUE, TFUE, libertés fondamentales, directives fiscales européennes, arrêts de principe.
    8️⃣ AGENT_IMMOBILIER_URBANISME : TVA sur marge, terrains à bâtir, marchands de biens, lotisseurs, 150-0 B ter, DMTO.
    9️⃣ AGENT_PROCEDURE_CONTENTIEUX : Charge de la preuve, présomptions (art. 751, 752 CGI), prescription, contrôle fiscal, contentieux.
    🔟 AGENT_TAXES_LOCALES : Taxe d'habitation, taxe foncière, CFE, autres taxes locales (TH, TF, CFE…).
    1️⃣1️⃣ AGENT_PRELEVEMENTS_SOCIAUX : Prélèvements sociaux (CSG, CRDS, PS), en particulier pour les non-résidents & coordination UE.

    🧠 RÈGLES DE SCORING ET SÉLECTION
    - Centralité : L'agent possède-t-il la compétence sur les articles ou concepts identifiés par l'Analyste ?
    - Complémentarité : Si l'Analyste projette une situation future (T+1), sélectionne l'agent compétent pour cette situation (ex: International pour un départ, mais aussi Particuliers pour l'impact sur l'IR).
    - Sélection : `selected_agents` doit contenir entre 1 et 4 agents (score ≥ 0.6 prioritaire).

    🔒 RÈGLE DE DOUBLE DÉTENTION (OBLIGATOIRE)
    Si la question ou l'analyse mentionne :
    - Un PAYS ÉTRANGER (hors France) ou une situation TRANSFRONTALIÈRE
    - OU une notion de "CONFORMITÉ", "droit de l'UE", "discrimination", "libre circulation", "CJUE", "TFUE"
    Alors tu DOIS inclure AGENT_DROIT_EUROPEEN dans `selected_agents` EN COMPLÉMENT de l'agent thématique principal.

    ⚠️ ATTENTION : Si la question ne mentionne PAS explicitement un transfert, une cession, ou un changement de statut, NE PAS ajouter de dimension internationale.

    ❌ INTERDICTIONS
    - Tu ne dois PAS produire d'analyse fiscale ni de sources.
    - Tu ne dois PAS mentionner ou lister de "dimensions".
    - Tu ne produis que le routage et les scores en JSON.

    📦 FORMAT DE SORTIE STRICT (OBLIGATOIRE)
    Tu dois répondre EXCLUSIVEMENT en JSON valide, sans aucun texte autour :

    {{
    "scores": {{
        "AGENT_PARTICULIERS_REVENUS": 0.0,
        "AGENT_TVA_INDIRECTES": 0.0,
        "AGENT_ENTREPRISES_IS": 0.0,
        "AGENT_PATRIMOINE_TRANSMISSION": 0.0,
        "AGENT_STRUCTURES_MONTAGES": 0.0,
        "AGENT_INTERNATIONAL": 0.0,
        "AGENT_DROIT_EUROPEEN": 0.0,
        "AGENT_IMMOBILIER_URBANISME": 0.0,
        "AGENT_PROCEDURE_CONTENTIEUX": 0.0,
        "AGENT_TAXES_LOCALES": 0.0,
        "AGENT_PRELEVEMENTS_SOCIAUX": 0.0
    }},
    "selected_agents": [
        "NOM_D_AGENT_1",
        "NOM_D_AGENT_2"
    ],
    "double_detention_triggered": false
    }}

    Note: `double_detention_triggered` doit être `true` si la règle de Double Détention a été appliquée.

    ---
    QUESTION UTILISATEUR :
    {user_question}

    ANALYSE PRÉLIMINAIRE DE L'ANALYSTE :
    {analyst_results}
    """
    res = llm_call(
        model_name,
        system="Tu es une IA d'orchestration experte qui dirige chaque question fiscale vers les bons agents spécialisés selon le prompt ci-après.",
        prompt=prompt,
        api_key=api_key,
        agent_name="orchestrateur",
    )
    return res.text
