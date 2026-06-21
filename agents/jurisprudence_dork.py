"""
Agent Jurisprudence Dork : Génère des requêtes Google Dork ciblées sur la Cour de cassation
"""
from utils.llm import llm_call


def generate_jurisprudence_dork(user_question, result_analyste, api_key, model_name="gemini-3-flash-preview"):
    """
    Transforme une question utilisateur en requêtes Google Dork
    ciblées sur la Cour de cassation.

    Args:
        user_question: Question de l'utilisateur
        result_analyste: Résultats de l'agent analyste
        api_key: Clé API Google
        model_name: Nom du modèle à utiliser. Par défaut "gemini-3-flash-preview".

    Returns:
        str: Liste de requêtes Google Dork sous forme de texte
    """

    user_prompt = f"""

     "Tu es un documentaliste juridique de la Cour de cassation. "
        "Ta mission est de créer des requêtes Google Dork ultra-précise. "
        "Règles strictes : \n"
        "1. Format : site:courdecassation.fr jurisprudence + \"[Article le plus pertinent]\" + \"[Faits ou concepts clés]\"\n"
        "2. Pas de listes, pas de texte, pas d'explication.\n"
        "3. Ne met pas de \\ ou de \" "

    Analyse la question suivante : "{user_question}" \n\n

    Tu as également les résultats d'un agent analyste pour t'aider : {result_analyste} \n\n

    EXEMPLES :
    Question: "Inaptitude physique armée"
    Query: site:courdecassation.fr jurisprudence + "R 207" + "Code du service national"

    Question: "Indemnité de licenciement sans cause réelle et sérieuse"
    Query: site:courdecassation.fr jurisprudence + "L. 1235-3" + "Code du travail"

    Question: "Annulation d'un mariage pour erreur sur les qualités essentielles"
    Query: site:courdecassation.fr jurisprudence + "Article 180" + "Code civil"

    📦 FORMAT DE SORTIE STRICT
    Tu dois retourner UNIQUEMENT une LISTE PYTHON VALIDE de chaînes :

    [
    "...",
    "...",
    "..."
    ]
    """

    try:
        res = llm_call(model_name, prompt=user_prompt, api_key=api_key, agent_name="jurisprudence")
        return res.text

    except Exception as e:
        return f"Erreur lors de la génération : {e}"
