"""
Récupération des articles internes FiscalOnline.

Pipeline :
1. fetch_tags()                         – récupère tous les tags disponibles
2. agent_relevent_fiscalonline_tag()    – sélectionne les tags pertinents (GPT-4o)
3. fetch_articles_by()                  – récupère les articles ayant ces tags
4. agent_ranker_fiscalonline()          – filtre et classe les articles (GPT-4o)
5. Mise en forme au format doc_enriched (title, url, content)

Usage :
    from utils.fiscalonline import main_fiscalonline
    doc_fiscalonline = main_fiscalonline(user_question, result_analyste, api_key=openai_key)
    doc_enriched = doc_fiscalonline + doc_enriched
"""

import ast
import html
import logging
import os

import openai
import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

from utils.json_utils import clean_json_codefence

logger = logging.getLogger(__name__)

BASE_URL = "https://api-preprod-fiscalonline.stoati.fr"


def _get_fiscalonline_token() -> str:
    """Récupère le token FiscalOnline depuis l'env ou les secrets Streamlit."""
    token = os.getenv("FISCALONLINE_TOKEN") or st.secrets.get("FISCALONLINE_TOKEN", "")
    if not token:
        raise ValueError(
            "Le token FiscalOnline n'est pas défini !\n"
            "Définissez FISCALONLINE_TOKEN dans votre fichier .env ou .streamlit/secrets.toml."
        )
    return token


def _headers() -> dict:
    return {
        "Accept": "application/json",
        "Authorization": "Bearer " + _get_fiscalonline_token(),
    }


# ---------------------------------------------------------------------------
# Étape 1 – Récupération des tags
# ---------------------------------------------------------------------------

def fetch_tags() -> dict:
    """
    Récupère tous les tags depuis l'API FiscalOnline.
    Retourne un dict {id: name}.
    """
    url = f"{BASE_URL}/admin/tags?limit=2000"
    try:
        resp = requests.get(url, headers=_headers(), timeout=15)
        resp.raise_for_status()
        tags = resp.json().get("data", [])
    except requests.RequestException as e:
        logger.error("Erreur lors de la récupération des tags FiscalOnline : %s", e)
        return {}

    return {tag["id"]: tag["name"] for tag in tags}


# ---------------------------------------------------------------------------
# Étape 2 – Sélection des tags pertinents
# ---------------------------------------------------------------------------

def agent_relevent_fiscalonline_tag(
    user_question: str,
    analyst_results,
    tags: dict,
    api_key: str = None,
) -> str:
    """
    Identifie les tags FiscalOnline les plus pertinents pour la question.
    Retourne une string représentant un dict Python {id: name}.
    """
    api_key = api_key or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError(
            "L'API key OpenAI n'est pas définie !\n"
            "Passez api_key en paramètre ou définissez OPENAI_API_KEY."
        )

    prompt = f"""
Ta mission : sélectionner les tags les plus pertinents pour une question utilisateur, UNIQUEMENT parmi la liste de tags fournie.

Règles :
- Tu dois renvoyer uniquement un dictionnaire Python (pas de texte autour, pas de markdown).
- Clés = ID (int), valeurs = nom exact du tag (str) tel qu'il apparaît dans la liste.
- Ne jamais inventer de tag : si un tag n'existe pas dans la liste, tu ne le proposes pas.
- Sélectionne 3 à 7 tags maximum, les plus "load-bearing" (ceux qui permettent de router la question vers le bon régime / la bonne doctrine).
- Priorise : article(s) du CGI cités, régime fiscal principal, objet juridique (ex : fonds de commerce), mécanisme (ex : location-gérance), nature de revenus/flux (ex : redevances), et éventuellement procédure/contentieux si la question le suggère.
- Si plusieurs tags se ressemblent, choisis le plus spécifique et le plus directement lié à la question.

Entrées :
- question: {user_question}
- indices_agent: {analyst_results}
- tags_disponibles: {tags}

Sortie attendue :
Un dictionnaire Python

Maintenant, traite la demande avec les entrées fournies.
"""

    client = openai.OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "Tu es un expert en classification fiscale (droit fiscal français)."},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
    )
    return response.choices[0].message.content


# ---------------------------------------------------------------------------
# Étape 3 – Récupération des articles par tags
# ---------------------------------------------------------------------------

def fetch_articles_by(tag_ids) -> list:
    """
    Récupère les articles correspondant à chaque tag_id.
    Retourne une liste de listes d'articles.
    """
    list_articles = []
    for tag_id in tag_ids:
        url = f"{BASE_URL}/articles"
        params = {"tagIds": tag_id, "limit": 1000}
        try:
            resp = requests.get(url, params=params, headers=_headers(), timeout=15)
            resp.raise_for_status()
            articles = resp.json().get("data", [])
            list_articles.append(articles)
        except requests.RequestException as e:
            logger.error("Erreur lors de la récupération des articles (tag %s) : %s", tag_id, e)

    return list_articles


# ---------------------------------------------------------------------------
# Étape 4 – Classement et filtrage des articles
# ---------------------------------------------------------------------------

def agent_ranker_fiscalonline(
    user_question: str,
    analyst_results,
    dic_articles: dict,
    api_key: str = None,
) -> str:
    """
    Sélectionne les articles les plus pertinents parmi dic_articles.
    Retourne une string représentant une liste Python d'IDs.
    """
    api_key = api_key or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError(
            "L'API key OpenAI n'est pas définie !\n"
            "Passez api_key en paramètre ou définissez OPENAI_API_KEY."
        )

    prompt = f"""
Ta mission :
Identifier les articles les plus pertinents pour répondre à une question utilisateur, en t'appuyant sur :
- la question,
- l'analyse juridique fournie,
- et UNIQUEMENT la liste d'articles disponibles.

Règles strictes :

1. Tu dois répondre UNIQUEMENT sous la forme d'une liste Python contenant les IDs des articles. Exemple : [id1, id2, id3]
2. Aucun texte avant ou après.
3. Ne jamais inventer d'article.
4. Ne jamais reformuler les titres.
5. Sélectionne entre 3 et 10 articles maximum.
6. Priorise les articles :
- portant directement sur le régime fiscal central cité dans la question
- traitant des conditions d'application
- traitant des exclusions ou cas particuliers
- traitant de jurisprudence structurante
- traitant de doctrine administrative pertinente
7. Si plusieurs articles sont proches, privilégie ceux :
- les plus spécifiques
- les plus techniques
- les plus directement applicables au cas d'espèce
8. Évite les articles périphériques, trop généraux ou simplement contextuels.

Entrées :

question_user = {user_question}
analyse = {analyst_results}
articles_disponibles = {dic_articles}

Sortie attendue : une liste python avec l'ID des articles
[id_1, id2, ...]

Traite maintenant la demande.
"""

    client = openai.OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "Tu es un agent expert en filtrage d'articles juridiques et fiscaux (droit fiscal français)."},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
    )
    return response.choices[0].message.content


# ---------------------------------------------------------------------------
# Utilitaire – Nettoyage HTML
# ---------------------------------------------------------------------------

def clean_html_content(raw_html: str) -> str:
    decoded = html.unescape(raw_html)
    soup = BeautifulSoup(decoded, "html.parser")
    text = soup.get_text(separator=" ")
    return " ".join(text.split())


# ---------------------------------------------------------------------------
# Point d'entrée principal
# ---------------------------------------------------------------------------

def main_fiscalonline(
    user_question: str,
    analyst_results,
    api_key: str = None,
) -> list:
    """
    Récupère les articles FiscalOnline pertinents pour la question.

    Retourne une liste de dicts au format doc_enriched :
        [{"title": str, "url": str, "content": str}, ...]

    Peut être lancé en parallèle de la recherche des autres sources,
    après obtention du résultat de l'agent analyste.
    """
    try:
        # 1. Tags disponibles
        tags = fetch_tags()
        if not tags:
            logger.warning("Aucun tag récupéré depuis FiscalOnline, abandon.")
            return []

        # 2. Sélection des tags pertinents
        relevant_tags_raw = agent_relevent_fiscalonline_tag(
            user_question, analyst_results, tags, api_key=api_key
        )
        relevant_tags: dict = ast.literal_eval(clean_json_codefence(relevant_tags_raw))

        # 3. Articles correspondant aux tags sélectionnés
        list_articles = fetch_articles_by(relevant_tags.keys())
        flat_articles = [article for sublist in list_articles for article in sublist]
        if not flat_articles:
            logger.warning("Aucun article récupéré pour les tags sélectionnés.")
            return []

        articles = pd.DataFrame(flat_articles)
        articles.drop_duplicates(subset="id", inplace=True)
        articles = articles[~articles["title"].str.lower().str.contains("quiz")]
        dic_articles = dict(zip(articles["id"], articles["title"]))

        # 4. Filtrage des articles pertinents
        relevant_articles_raw = agent_ranker_fiscalonline(
            user_question, analyst_results, dic_articles, api_key=api_key
        )
        relevant_article_ids: list = ast.literal_eval(clean_json_codefence(relevant_articles_raw))
        df_relevant = articles[articles["id"].isin(relevant_article_ids)]

        # 5. Mise en forme au format doc_enriched
        doc_fiscalonline = [
            {
                "title": row["title"],
                "url": "https://fiscalonline.com" + row["url"],
                "content": clean_html_content(row["content"]),
                "source_domain": "fiscalonline.fr",
            }
            for _, row in df_relevant.iterrows()
        ]

        logger.info(
            "FiscalOnline : %d articles retenus sur %d candidats.",
            len(doc_fiscalonline),
            len(dic_articles),
        )
        return doc_fiscalonline

    except Exception as e:
        logger.error("Erreur dans main_fiscalonline : %s", e)
        return []
