"""
Agent Vérificateur : Vérifie et nettoie les sources identifiées
"""
import logging
import time
import google.generativeai as genai
from typing import Dict

logger = logging.getLogger(__name__)


def agent_verificateur(user_question: str, analyst_results: str, agents_outputs: dict, api_key: str, model_name: str = "gemini-3-flash-preview") -> str:
    """
    Agent Auditeur : Compare le diagnostic de l'Analyste avec les sources des Spécialistes.
    Garantit l'absence d'oublis techniques et la suppression des doublons.

    Args:
        user_question: Question de l'utilisateur
        analyst_results: Résultats de l'agent analyste
        agents_outputs: Sorties des agents spécialisés
        api_key: Clé API Google
        model_name: Nom du modèle à utiliser. Par défaut "gemini-3-flash-preview".
    """
    system_prompt = (
        "Tu es une IA experte en contrôle qualité juridique et fiscal.\n"
        "Tu n'es PAS un agent d'analyse : tu es un AUDITEUR + CORRECTEUR final.\n\n"

        "🎯 TA MISSION\n"
        "À partir de :\n"
        "1️⃣ La QUESTION UTILISATEUR.\n"
        "2️⃣ L'ANALYSE PRÉLIMINAIRE (le diagnostic de l'Analyste : concepts, seuils, points d'attention).\n"
        "3️⃣ Les RÉSULTATS DES AGENTS SPÉCIALISÉS (listes de sources JSON).\n\n"

        "Tu dois produire une liste UNIQUE, FIABLE et COHÉRENTE de sources en suivant ces étapes :\n\n"

        "1) AUDIT DE COUVERTURE (Règle d'or)\n"
        "- Pour chaque 'point_de_vigilance_legiste' identifié par l'Analyste, vérifie qu'il existe au moins une source correspondante.\n"
        "- Si l'Analyste a identifié un régime futur (T+1) ou un article de renvoi, et que les spécialistes l'ont oublié, tu DOIS l'ajouter.\n"
        "- Si une source proposée par un spécialiste est hors-sujet par rapport au diagnostic, tu dois la SUPPRIMER.\n\n"

        "2) NORMALISATION ET NETTOYAGE\n"
        "- Supprime les doublons (plusieurs agents citant le même article).\n"
        "- Harmonise le formatage (ex: 'Article 167 bis du CGI' au lieu de 'Art. 167bis').\n"

        "3) CONTRÔLE DE FIABILITÉ\n"
        "- Vérifie la cohérence des numéros d'articles et de BOFiP (ne pas inventer).\n"
        "- Assure-toi que la jurisprudence citée est réelle et pertinente pour le cas d'espèce.\n\n"

        "❌ INTERDICTIONS ABSOLUES\n"
        "- Ne pas faire de commentaire juridique.\n"
        "- Ne pas ajouter de texte explicatif.\n"
        "- Ne pas inventer de sources non mentionnées dans les étapes précédentes, sauf si l'oubli d'un texte légal cité par l'Analyste est flagrant.\n\n"

        "📦 FORMAT DE SORTIE STRICT\n"
        "Renvoie UNIQUEMENT un JSON final :\n"
        f"Conserve exactement le format transmis en entrée, avec le type de source  'site: ', ne change pas la source, conserve la telle que transmise."
        "{\n"
        '  "textes_legaux": [...],\n'
        '  "bofip": [...],\n'
        '  "jurisprudence": [...],\n'
        '  "reponse_ministerielle": [...], \n'
        '  "autres": [...]\n'
        "}\n\n"
        "---\n"
        f"QUESTION UTILISATEUR : {user_question}\n\n"
        f"ANALYSE PRÉLIMINAIRE DE L'ANALYSTE : {analyst_results}\n\n"
        f"SOURCES DES AGENTS SPÉCIALISÉS : {agents_outputs}\n"
    )

    n_input = sum(len(v) for v in agents_outputs.values() if isinstance(v, list))
    logger.info("Verificateur — %d sources en entrée, appel Gemini (%s)", n_input, model_name)
    t0 = time.time()
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(model_name=model_name)
    response = model.generate_content(system_prompt)
    logger.info("Verificateur — réponse reçue (%.1fs), %d chars", time.time() - t0, len(response.text))
    return response.text
