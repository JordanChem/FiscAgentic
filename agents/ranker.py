"""
Agent Ranker : Classe et score les résultats de recherche
"""
import openai
import datetime
import logging
from typing import List, Dict

logger = logging.getLogger(__name__)


def agent_ranker(
    question: str,
    structured_results: List[Dict],
    analyst_results: str,
    specialists_results,
    openai_api_key: str,
    model: str = "gpt-4o"
) -> List[Dict]:
    """
    Utilise OpenAI GPT-4o pour réordonner et filtrer les résultats légaux/fiscaux via un prompt expert.

    Args:
        question: Question de l'utilisateur.
        structured_results: Liste de résultats structurés (de search_official_sources, format dict avec title, url, snippet...).
        analyst_results: Résultats de l'agent analyste.
        specialists_results: Résultats des agents spécialisés.
        openai_api_key: Clé API OpenAI.
        model: Modèle OpenAI (par défaut gpt-4o).

    Returns:
        La liste renvoyée par GPT (JSON["results"]), chaque dict étant enrichi avec toutes les infos de structured_results correspondantes (title, url, snippet, etc).
    """

    # Prépare les candidats conformément au format attendu pour le prompt (ajoute id...)
    candidates = []
    for idx, r in enumerate(structured_results):
        candidate = {
            "id": f"r{idx+1}",
            "title": r.get("title", ""),
            "snippet": r.get("snippet", ""),
            "url": r.get("url", ""),
        }
        # Déduit le type de source simplifié pour aider le LLM (si possible)
        domain = r.get("source_domain", "")
        if "legifrance" in domain:
            candidate["source_type"] = "CGI"
        elif "bofip" in domain:
            candidate["source_type"] = "BOFIP"
        elif "conseil-etat" in domain:
            candidate["source_type"] = "Jurisprudence CE"
        elif "courdecassation" in domain:
            candidate["source_type"] = "Jurisprudence Cass"
        elif "conseil-constitutionnel" in domain:
            candidate["source_type"] = "Constitutionnel"
        elif "assemblee-nationale" in domain:
            candidate["source_type"] = "Assemblée"
        elif "senat" in domain:
            candidate["source_type"] = "Sénat"
        elif "europa.eu" in domain:
            candidate["source_type"] = "CJUE"
        else:
            candidate["source_type"] = "Autre"
        candidates.append(candidate)

    current_date = datetime.datetime.now().strftime("%d/%m/%Y")

    system_prompt = (
        "Tu es une IA experte en fiscalité française, spécialisée dans le classement et la validation de sources juridiques.\n\n"
        f"La date du jour est : {current_date}.\n\n"
        "🎯 TA MISSION\n"
        "Tu dois trier et scorer les résultats de recherche web (SerpAPI) en fonction de deux référentiels :\n"
        "1) La QUESTION de l'utilisateur.\n"
        "2) L'ANALYSE PRÉLIMINAIRE de l'expert (le diagnostic technique).\n\n"

        "📌 RÉFÉRENTIEL PRIORITAIRE (L'ANALYSE DE L'EXPERT)\n"
        "On te fournit un diagnostic stratégique (concepts T0, concepts miroirs T+1, points de vigilance) et les résultats d'agents spécialisés.\n"
        f"DIAGNOSTIC : {analyst_results}\n\n"

        f"SPECIALISTES : {specialists_results}\n\n"

       "RÈGLES DE SCORING (L'ALGORITHME DE TRI)\n"
        "1️⃣ BOOST 'CONCEPTS MIROIRS' (T+1) : Si un résultat traite du régime futur identifié par l'analyste, il doit recevoir un score très élevé (>= 0.85).\n"
        "2️⃣ BOOST 'VIGILANCE LÉGISTE' : Si un résultat correspond à un article de renvoi ou un seuil critique cité dans l'analyse, il est prioritaire.\n"
        "3️⃣ FILTRE 'SÉCURITÉ JURIDIQUE' : Favorise les sources officielles (Legifrance, BOFiP) qui confirment ou infirment les hypothèses de l'analyste.\n"
        "4️⃣ BOOST 'HIÉRARCHIE SUPRÊME' : Si la source est une norme supra-nationale (Directive, Règlement) ou une décision CJUE/CEDH traitant du concept, score = 0.95.\n\n"

        "🧩 CRITÈRES D'ÉVALUATION (KEEP / DROP)\n"
        "- si la source est présente dans le Diagnostic ET dans l'avis des Specialistes, met un score = 1 et keep=true \n"
        "- keep = true si :\n"
        "  • La source traite directement d'un concept T0 ou T+1 de l'analyse.\n"
        "  • La source appartient à la même FAMILLE d'impôt que le diagnostic (ex: Flux/TVA vs Revenu/IR).\n"
        "  • La source est 'structurante' : elle définit l'assiette, le fait générateur ou la base d'imposition.\n"
        "  • La source est récente et est pertinente.\n"
        "  • La source est une décision de jurisprudence (CE/Cass) citée comme structurante.\n"
        "  • La source précise un seuil chiffré mentionné dans l'analyse.\n"
        "  • La source est un CJUE (site europa.eu) ET le titre ressemble à une référence .\n"
        "- keep = false si :\n"
        "  • La source est une version abrogée d'un texte alors qu'une version plus récente est présente.\n"
        "  • La source est trop générique (ex: accueil du site Legifrance, ou Liste des résultats).\n"
        "  • La source traite d'une thématique fiscale exclue par le diagnostic de l'analyste.\n"
        "  • DISQUALIFICATION PAR L'ASSIETTE : La source traite d'un impôt dont l'assiette est différente du diagnostic (ex: traiter du gain net de cession pour une question de prix de vente HT).\n"
        "  • VERSION OBSOLÈTE : Une version postérieure du texte ou de la doctrine est disponible.\n"
        "  • BRUIT DE NAVIGATION : La source est une notice, un sommaire ou une recherche comme une liste des résultats.\n\n"

        "⚠️ OBLIGATION DE TRAITEMENT\n"
        "- Tu DOIS traiter TOUS les candidats fournis, sans exception.\n"
        "- Le score doit refléter la 'Valeur Ajoutée' par rapport au diagnostic technique.\n\n"

        f"IMPORTANT: Ta réponse DOIT contenir exactement {len(candidates)} objets dans la liste 'results'. Si tu en oublies un seul, le système plantera."

        "📦 FORMAT DE SORTIE STRICT (JSON)\n"
        "{\n"
        '  "results": [\n'
        "    {\n"
        '      "id": "<id>",\n'
        '      "keep": true,\n'
        '      "score": 0.95,\n'
        '      "reason": "Explication fiscale concise : pourquoi cette source valide un point du diagnostic."\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "AUCUN texte hors JSON."
    )

    user_content = {
        "question": question,
        "candidates": candidates
    }

    # Appel OpenAI API
    client = openai.OpenAI(api_key=openai_api_key)
    chat_messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": str(user_content)} # dump comme string simple, structure lisible
    ]
    response = client.chat.completions.create(
        model=model,
        messages=chat_messages,
        temperature=0,
        response_format={"type": "json_object"},
        max_tokens=16384,
    )
    # Extraction du JSON "strict"
    import json
    from utils.json_utils import lire_json_beton
    completion = response.choices[0].message.content
    try:
        ranking = json.loads(completion)
    except Exception as exc:
        logger.warning(f"Erreur décodage JSON ranker (tentative lire_json_beton): {exc}")
        ranking = lire_json_beton(completion)

    # Agrégation dans l'autre sens : la sortie JSON["results"] de GPT-4o reçoit TOUTES les données du structured_result correspondant.
    id_to_structured = {f"r{idx+1}": r.copy() for idx, r in enumerate(structured_results)}
    aggregated = []
    for res in ranking.get("results", []):
        result_id = res.get("id")
        src_info = id_to_structured.get(result_id, {})
        enriched = src_info.copy()
        enriched.update(res)
        aggregated.append(enriched)

    # Tri par score décroissant
    aggregated_sorted = sorted(aggregated, key=lambda x: x.get("score", 0), reverse=True)

    return aggregated_sorted
