"""
Agent de Suivi : Répond aux questions de suivi en utilisant le contexte de la conversation
"""
import google.generativeai as genai
from typing import Dict, List


def agent_suivi(user_question: str, contexte_conversation: Dict, api_key: str, model_name: str = "gemini-3-flash-preview") -> str:
    """
    Agent qui répond aux questions de suivi en utilisant le contexte de la conversation précédente.
    
    Args:
        user_question: La nouvelle question de l'utilisateur
        contexte_conversation: Dictionnaire contenant :
            - question_initial: La question initiale
            - reponse_initial: La réponse initiale
            - sources: Les sources trouvées
            - analyse: L'analyse de l'agent analyste
        api_key: Clé API Google
        model_name: Nom du modèle à utiliser. Par défaut "gemini-3-flash-preview".
    
    Returns:
        str: Réponse en format JSON avec la réponse rédigée
    """
    
    question_initial = contexte_conversation.get("question_initial", "")
    reponse_initial = contexte_conversation.get("reponse_initial", "")
    sources = contexte_conversation.get("sources", [])
    analyse = contexte_conversation.get("analyse", {})
    
    # Construire le contexte des sources
    sources_context = ""
    if sources:
        sources_list = []
        for idx, source in enumerate(sources[:5], 1):  # Limiter à 5 sources pour le contexte
            sources_list.append(f"{idx}. {source.get('title', 'Sans titre')} - {source.get('url', '')}")
        sources_context = "\n".join(sources_list)
    
    system_prompt = f"""
        Tu es un Expert Fiscaliste Senior assistant conversationnel. Ta mission est de répondre aux questions de suivi de l'utilisateur en te basant sur le contexte de la conversation précédente.

        🎯 CONTEXTE DE LA CONVERSATION PRÉCÉDENTE
        
        Question initiale de l'utilisateur :
        {question_initial}
        
        Réponse initiale fournie :
        {reponse_initial}
        
        Sources consultées :
        {sources_context if sources_context else "Aucune source disponible"}
        
        Analyse technique initiale :
        {analyse}
        
        ---
        
        🧠 TA MISSION
        
        L'utilisateur pose maintenant une nouvelle question de suivi :
        "{user_question}"
        
        Tu dois :
        1. Comprendre si cette question est liée à la question initiale
        2. Répondre en utilisant le contexte de la conversation précédente
        3. Si la question nécessite de nouvelles sources ou une nouvelle analyse, l'indiquer clairement
        4. Fournir une réponse claire, concise et professionnelle
        
        ❌ INTERDICTIONS
        - Ne pas inventer de nouvelles sources non mentionnées dans le contexte
        - Ne pas donner d'informations contradictoires avec la réponse initiale
        - Si la question sort du contexte fiscal initial, le signaler poliment
        
        📦 FORMAT DE SORTIE (JSON STRICT)
        {{
        "reponse_redigee": "Ta réponse structurée en Markdown ici",
        "necessite_nouvelle_recherche": false,
        "points_cles": ["Point important 1", "Point important 2"]
        }}
        
        Si la question nécessite une nouvelle recherche complète (ex: changement de sujet), mets "necessite_nouvelle_recherche" à true.
    """
    
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(model_name=model_name)
    response = model.generate_content(system_prompt)
    
    return response.text
