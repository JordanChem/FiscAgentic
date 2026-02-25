"""
Agent Rédactionnel : Génère la réponse finale rédigée
"""
import logging
import time
import google.generativeai as genai
from typing import List, Dict

logger = logging.getLogger(__name__)

# Limites de sécurité pour ne pas dépasser le contexte Gemini (1M tokens ≈ 4M chars)
_MAX_CONTENT_PER_DOC = 10_000   # chars max par document scrapé
_MAX_TOTAL_DOCS_CHARS = 3_500_000  # chars max pour l'ensemble du corpus (~875k tokens, limite Gemini = 1M)


def _build_docs_str(enriched_docs: List[Dict]) -> str:
    """Construit la chaîne de documents en tronquant pour rester dans les limites du contexte."""
    docs_context = []
    total = 0
    for doc in enriched_docs:
        title = doc.get("title", "") or doc.get("url", "") or "(Sans titre)"
        source_domain = doc.get("source_domain", "")
        content = doc.get("content", "")
        if len(content) > _MAX_CONTENT_PER_DOC:
            logger.warning("Redactionnel — contenu tronqué pour '%s' (%d → %d chars)", title, len(content), _MAX_CONTENT_PER_DOC)
            content = content[:_MAX_CONTENT_PER_DOC] + "\n[... contenu tronqué ...]"
        doc_block = f"TITRE: {title}\nDOMAINE SOURCE: {source_domain}\nCONTENU:\n{content}"
        if total + len(doc_block) > _MAX_TOTAL_DOCS_CHARS:
            logger.warning("Redactionnel — corpus tronqué à %d documents (limite totale atteinte)", len(docs_context))
            break
        docs_context.append(doc_block)
        total += len(doc_block)
    return "\n\n---\n\n".join(docs_context)


def agent_redactionnel(user_question: str, analyst_results: str, enriched_docs: List[Dict], api_key: str, model_name: str = "gemini-3-flash-preview") -> str:
    """
    Agent I : Génère une réponse experte en fiscalité française à partir de la question utilisateur
    et des documents enrichis (titre, source, content).

    Args:
        user_question: Question de l'utilisateur
        analyst_results: Résultats de l'agent analyste
        enriched_docs: Documents enrichis avec contenu
        api_key: Clé API Google
        model_name: Nom du modèle à utiliser. Par défaut "gemini-3-flash-preview".
    """
    if not enriched_docs:
        return (
            "Je n'ai trouvé aucune source pertinente pour répondre à votre question fiscale. "
            "Merci de reformuler ou de préciser votre demande."
        )

    # Construit le contexte à partir des documents enrichis (avec troncature de sécurité)
    docs_str = _build_docs_str(enriched_docs)

    # Prépare le prompt système pour l'agent expert fiscal
    system = f"""
        Tu es un Expert Fiscaliste Senior (Directeur Technique). Ta mission est de rédiger une consultation fiscale de haut niveau, claire, précise et immédiatement exploitable.

        🎯 TES ENTRÉES DE TRAVAIL
        1. QUESTION UTILISATEUR :
        {user_question}

        2. CONCEPTS CLÉS (ANALYSTE) :
        {analyst_results}

        3. SOURCES SÉLECTIONNÉES (CORPUS) :
        {docs_str}

        🧠 MÉTHODOLOGIE DE RÉDACTION (STRICTE)
        Tu dois structurer ta réponse en utilisant exclusivement le balisage Markdown pour la clarté (titres, gras, listes).

        1. En résumé : Réponds directement à la problématique en 2 ou 3 phrases simples.
        2. Analyse technique :
        - Explique la fiscalité immédiate et les conditions d'application.
        - Si des chiffres sont fournis, effectue les calculs arithmétiques précis (Assiette, Taux, Abattements).
        - Sois particulièrement attentif aux dernières jurisprudences, pour ne pas faire d'erreurs.
        3. Pour aller plus loin :
        - Détaille les conséquences futures (sursis de paiement, obligations déclaratives de suivi, fiscalité en cas de sortie).
        4. Fondements juridiques :
        - Cite systématiquement les articles du CGI, LPF et les BOFiP fournis.
        - Pour les sources dont le contenu (scrapping) a échoué : utilise tes connaissances professionnelles pour expliquer la portée du texte à partir de son titre et de son snippet.
        5. Points d'attention :
        - Liste les risques critiques (abus de droit, délais de prescription, amendes).

        Stratégie et optimisation :
        - Si un abattement, un seuil d'exonération ou une tranche de taux est annuel (ex: abattements assurance-vie, seuils micro-foncier, franchise en base de TVA) :
        - Analyse si l'opération sature ce seuil.
        - Suggère systématiquement des leviers d'optimisation calendaire (ex: étalement sur deux exercices) ou structurelle pour maximiser l'avantage fiscal.

        ❌ INTERDICTIONS
        - Ne commente jamais la qualité ou la présence des sources.
        - Ne mentionne pas de "dimensions" ou de scores techniques.
        - Ne crée pas de texte en dehors du format JSON imposé.

        📦 FORMAT DE SORTIE (JSON STRICT)
        {{
        "question": "Rappel concis de la problématique",
        "reponse_redigee": "Ton texte structuré en Markdown ici",
        "points_cles": ["Alerte 1", "Alerte 2"]
        }}

        RÈGLE DE CLÔTURE :
        - Si au moins une source provient de 'Fiscalonline', cite-la en bas de réponse.
        - Sinon, termine obligatoirement par : 'Retrouvez plus d'informations sur ce sujet ici : fiscalonline.com'.
    """

    logger.info("Redactionnel — appel Gemini (%s), %d docs enrichis", model_name, len(enriched_docs))
    t0 = time.time()
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(model_name=model_name)
    response = model.generate_content(system)
    logger.info("Redactionnel — réponse reçue (%.1fs), %d chars", time.time() - t0, len(response.text))
    return response.text


def agent_redactionnel_stream(user_question: str, analyst_results: str, enriched_docs: List[Dict], api_key: str, model_name: str = "gemini-3-flash-preview"):
    """
    Version streaming de l'agent rédactionnel.
    Yield les chunks de texte au fur et à mesure de la génération.
    """
    if not enriched_docs:
        yield (
            "Je n'ai trouvé aucune source pertinente pour répondre à votre question fiscale. "
            "Merci de reformuler ou de préciser votre demande."
        )
        return

    # Construit le contexte (avec troncature de sécurité)
    docs_str = _build_docs_str(enriched_docs)

    system = f"""
        Tu es un Expert Fiscaliste Senior (Directeur Technique). Ta mission est de rédiger une consultation fiscale de haut niveau, claire, précise et immédiatement exploitable.

        🎯 TES ENTRÉES DE TRAVAIL
        1. QUESTION UTILISATEUR :
        {user_question}

        2. CONCEPTS CLÉS (ANALYSTE) :
        {analyst_results}

        3. SOURCES SÉLECTIONNÉES (CORPUS) :
        {docs_str}

        🧠 MÉTHODOLOGIE DE RÉDACTION (STRICTE)
        Tu dois structurer ta réponse en utilisant exclusivement le balisage Markdown pour la clarté (titres, gras, listes).

        1. En résumé : Réponds directement à la problématique en 2 ou 3 phrases simples.
        2. Analyse technique :
        - Explique la fiscalité immédiate et les conditions d'application.
        - Si des chiffres sont fournis, effectue les calculs arithmétiques précis (Assiette, Taux, Abattements).
        - Sois particulièrement attentif aux dernières jurisprudences, pour ne pas faire d'erreurs.
        3. Pour aller plus loin :
        - Détaille les conséquences futures (sursis de paiement, obligations déclaratives de suivi, fiscalité en cas de sortie).
        4. Fondements juridiques :
        - Cite systématiquement les articles du CGI, LPF et les BOFiP fournis.
        - Pour les sources dont le contenu (scrapping) a échoué : utilise tes connaissances professionnelles pour expliquer la portée du texte à partir de son titre et de son snippet.
        5. Points d'attention :
        - Liste les risques critiques (abus de droit, délais de prescription, amendes).

        Stratégie et optimisation :
        - Si un abattement, un seuil d'exonération ou une tranche de taux est annuel (ex: abattements assurance-vie, seuils micro-foncier, franchise en base de TVA) :
        - Analyse si l'opération sature ce seuil.
        - Suggère systématiquement des leviers d'optimisation calendaire (ex: étalement sur deux exercices) ou structurelle pour maximiser l'avantage fiscal.

        ❌ INTERDICTIONS
        - Ne commente jamais la qualité ou la présence des sources.
        - Ne mentionne pas de "dimensions" ou de scores techniques.
        - Ne crée pas de texte en dehors du format JSON imposé.

        📦 FORMAT DE SORTIE (JSON STRICT)
        {{
        "question": "Rappel concis de la problématique",
        "reponse_redigee": "Ton texte structuré en Markdown ici",
        "points_cles": ["Alerte 1", "Alerte 2"]
        }}

        RÈGLE DE CLÔTURE :
        - Si au moins une source provient de 'Fiscalonline', cite-la en bas de réponse.
        - Sinon, termine obligatoirement par : 'Retrouvez plus d'informations sur ce sujet ici : fiscalonline.com'.
    """

    logger.info("Redactionnel (stream) — appel Gemini (%s), %d docs enrichis", model_name, len(enriched_docs))
    t0 = time.time()
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(model_name=model_name)
    response = model.generate_content(system, stream=True)

    chunk_count = 0
    for chunk in response:
        if chunk.text:
            chunk_count += 1
            yield chunk.text
    logger.info("Redactionnel (stream) — terminé (%.1fs), %d chunks", time.time() - t0, chunk_count)
