"""
Agent Analyste : Analyse la question fiscale et identifie les concepts clés
"""
import google.generativeai as genai


def agent_analyste(user_question, api_key=None, model_name="gemini-3-flash-preview"):
    """
    Appelle Gemini 3 (Google Generative AI) avec un prompt détaillé pour obtenir une analyse fiscale structurée
    des situations complexes incluant la période de départ, la continuité fiscale, et la projection en tant que non-résident.

    Args:
        user_question (str): La situation factuelle décrite par l'utilisateur.
        api_key (str, optional): Clé API Gemini. Si non fourni, cherche dans la variable d'environnement GEMINI_API_KEY.
        model_name (str): Nom du modèle à utiliser. Par défaut "gemini-3-flash-preview".

    Returns:
        str: Réponse de Gemini attendue strictement au format JSON selon le schéma décrit.
    """

    prompt = (
        "Rôle : Tu es un Architecte Fiscal spécialisé dans l'analyse de structures complexes. "
        "Ta mission est de décomposer une situation de fait en un écosystème de normes juridiques. "
        "Tu ne dois pas seulement identifier la règle au jour T (le départ), mais aussi les règles qui s'appliqueront au jour T+1 (détention/cession en tant que non-résident).\n\n"

        "Instructions de travail :\n"
        "Analyse de la Matière Factuelle : Identifie les seuils (détention, valeur), la nature des actifs et la géographie (UE vs Hors UE).\n"
        "Mapping de la \"Loi Écran\" (Le jour du départ) : Identifie le régime de sortie immédiat \n"
        "Analyse de la \"Continuité Fiscale\" (Le futur) : Projette la situation de l'utilisateur après son transfert. "
        "Une fois non-résident, quel article du CGI régit la fiscalité de ses actifs restés en France ? "
        "Identification des \"Vecteurs de Renvoi\" : Liste les concepts qui font souvent l'objet de renvois législatifs (ex: sursis, report, prélèvements sociaux, crédits d'impôt).\n"
        "Stratégie de Requête \"Full-Spectrum\" : Génère des requêtes SerpAI qui croisent les articles de sortie avec les articles de détention non-résident.\n\n"

        "Structure de sortie attendue (JSON uniquement) :\n\n"
        "{\n"
        "  \"analyse_factuelle\": {\n"
        "    \"qualifications\": [\"Ex: Participation substantielle\", \"Résident UE\"],\n"
        "    \"seuils_critiques\": [\"Analyse des montants et % cités\"]\n"
        "  },\n"
        "  \"concepts_clefs_T0\": [\"Régimes applicables au moment du départ\"],\n"
        "  \"concepts_miroirs_Tplus1\": [\"Régimes applicables une fois le transfert effectué \"],\n"
        "  \"mecanismes_de_coordination\": [\"Sursis, report, articulation conventionnelle\"],\n"
        "  \"axes_de_recherche_serp\": [\n"
        "    \"Requêtes combinées\",\n"
        "    \"Requêtes de doctrine (BOFIP) sur les conséquences de la cession post-transfert\",\n"
        "    \"Requêtes sur les obligations déclaratives de maintien\"\n"
        "  ],\n"
        "  \"points_de_vigilance_legiste\": [\"Lister les articles connexes dont la lecture est impérative pour comprendre le mécanisme global\"]\n"
        "}\n\n"
        "Aucun texte en dehors du JSON n'est autorisé.\n"
        "---\n"
        f"SITUATION UTILISATEUR :\n{user_question}\n"
    )

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(model_name=model_name)
    response = model.generate_content(prompt)
    return response.text
