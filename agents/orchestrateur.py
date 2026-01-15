"""
Agent Orchestrateur : Route la question vers les agents spécialisés appropriés
"""
import os
import openai


def agent_orchestrateur(user_question, analyst_results, api_key=None, model_name="gpt-5.2-2025-12-11"):
    """
    Appelle GPT-4o d'OpenAI pour router une question fiscale vers les bons agents spécialisés,
    selon le prompt détaillé fourni.
    Retourne la réponse JSON stricte du modèle.
    
    Args:
        user_question: Question de l'utilisateur
        analyst_results: Résultats de l'agent analyste
        api_key: Clé API OpenAI
        model_name: Nom du modèle à utiliser. Par défaut "gpt-5.2-2025-12-11".
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
    2) Traduire ces besoins en scores (0 à 1) pour CHACUN des 6 agents spécialisés.
    3) Retourner 1 à 3 agents à appeler en priorité pour traduire ces concepts en sources juridiques réelles.

    ⚙️ LES 6 AGENTS SPÉCIALISÉS
    1️⃣ AGENT_PARTICULIERS_REVENUS : IR, revenus catégoriels, fiscalité personnelle, PV de titres (particuliers).
    2️⃣ AGENT_TVA_INDIRECTES : TVA (intracom, autoliquidation), taxes indirectes.
    3️⃣ AGENT_ENTREPRISES_IS : IS courant, résultat fiscal, intégration fiscale, dividendes.
    4️⃣ AGENT_PATRIMOINE_TRANSMISSION : Successions, donations, IFI, démembrement, ISF, PV mobilières (patrimonial).
    5️⃣ AGENT_STRUCTURES_MONTAGES : Abus de droit, restructurations (fusions/LBO), montages complexes.
    6️⃣ AGENT_INTERNATIONAL : Résidence fiscale, Exit Tax, Conventions, Établissement stable, flux transfrontaliers.

    🧠 RÈGLES DE SCORING ET SÉLECTION
    - Centralité : L'agent possède-t-il la compétence sur les articles ou concepts identifiés par l'Analyste ?
    - Complémentarité : Si l'Analyste projette une situation future (T+1), sélectionne l'agent compétent pour cette situation (ex: International pour un départ, mais aussi Particuliers pour l'impact sur l'IR).
    - Sélection : `selected_agents` doit contenir entre 1 et 3 agents (score ≥ 0.6 prioritaire).

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
        "AGENT_INTERNATIONAL": 0.0
    }},
    "selected_agents": [
        "NOM_D_AGENT_1",
        "NOM_D_AGENT_2"
    ]
    }}

    ---
    QUESTION UTILISATEUR :
    {user_question}

    ANALYSE PRÉLIMINAIRE DE L'ANALYSTE :
    {analyst_results}
    """
    # Correction pour OpenAI v1: créer le client explicitement AVEC la clé API
    client = openai.OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": "Tu es une IA d'orchestration experte qui dirige chaque question fiscale vers les bons agents spécialisés selon le prompt ci-après."},
            {"role": "user", "content": prompt},
        ],
        temperature=0
    )
    # Récupération du JSON pur (pas d'explications Ni de texte hors-JSON dans ce prompt)
    return response.choices[0].message.content
