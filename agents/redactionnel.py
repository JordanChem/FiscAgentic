"""
Agent Rédactionnel : Génère la réponse finale rédigée
"""
import google.generativeai as genai
from typing import List, Dict


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

    # Construit le contexte à partir des documents enrichis
    docs_context = []
    for doc in enriched_docs:
        title = doc.get("title", "") or doc.get("url", "") or "(Sans titre)"
        source_domain = doc.get("source_domain", "")
        snippet = doc.get("snippet", "")
        content = doc.get("content", "")
        # On limite la taille du contenu pour éviter un prompt trop long
        content_excerpt = content[:5000] + ("..." if len(content) > 5000 else "")
        doc_block = f"TITRE: {title}\nDOMAINE SOURCE: {source_domain}\nCONTENU:\n{content_excerpt}\nSNIPPET:\n{snippet}"
        docs_context.append(doc_block)
    docs_str = "\n\n---\n\n".join(docs_context)

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

        1. RÉSUMÉ EXÉCUTIF : Réponds directement à la problématique en 2 ou 3 phrases simples.
        2. ANALYSE TECHNIQUE (Régime T0) : 
        - Explique la fiscalité immédiate et les conditions d'application.
        - Si des chiffres sont fournis, effectue les calculs arithmétiques précis (Assiette, Taux, Abattements).
        3. PROJECTION ET CONTINUITÉ (Régime T+1) :
        - Détaille les conséquences futures (sursis de paiement, obligations déclaratives de suivi, fiscalité en cas de sortie).
        4. FONDEMENTS JURIDIQUES :
        - Cite systématiquement les articles du CGI, LPF et les BOFiP fournis.
        - Pour les sources dont le contenu (scrapping) a échoué : utilise tes connaissances professionnelles pour expliquer la portée du texte à partir de son titre et de son snippet.
        5. POINTS DE VIGILANCE :
        - Liste les risques critiques (abus de droit, délais de prescription, amendes).

        STRATÉGIE ET OPTIMISATION (Conseil proactif) :
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
    
        QUESTION UTILISATEUR:\n{user_question}\n\n
        SOURCES FOURNIES:\n{docs_str}\n\n
    """

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(model_name=model_name) 
    response = model.generate_content(system)
    
    return response.text
