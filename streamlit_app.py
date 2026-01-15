"""
Application Streamlit pour l'assistant fiscal intelligent
"""
import streamlit as st
import os
import json
from typing import Dict, List
import time

# Imports des agents
from agents.analyste import agent_analyste
from agents.orchestrateur import agent_orchestrateur
from agents.specialises import (
    agent_particulier_revenu,
    agent_tva_indirect,
    agent_entreprise_is,
    agent_patrimoine_transmission,
    agent_structure_montage,
    agent_international
)
from agents.generaliste import agent_generaliste
from agents.verificateur import agent_verificateur
from agents.ranker import agent_ranker
from agents.redactionnel import agent_redactionnel
from agents.suivi import agent_suivi

# Imports des utilitaires
from utils.json_utils import clean_json_codefence, lire_json_beton
from utils.search import search_official_sources, OFFICIAL_DOMAINS
from utils.scraper_utils import scrapper

# Configuration de la page
st.set_page_config(
    page_title="Assistant Fiscal Intelligent",
    page_icon="📊",
    layout="wide"
)

# Initialisation de la session state
if 'messages' not in st.session_state:
    st.session_state.messages = []
if 'contexte_conversation' not in st.session_state:
    st.session_state.contexte_conversation = None
if 'processing' not in st.session_state:
    st.session_state.processing = False
if 'active_domains' not in st.session_state:
    # Par défaut, tous les domaines sont actifs
    st.session_state.active_domains = OFFICIAL_DOMAINS.copy()

# Mapping des noms de modèles vers les noms réels
MODEL_MAPPING = {
    "gemini-3-pro-preview": "gemini-3-pro-preview",
    "gemini-3-flash-preview": "gemini-3-flash-preview",
    "gemini-2.5-flash": "gemini-2.5-flash",
    "gpt-5.2": "gpt-5.2-2025-12-11",
    "gpt-5-chat-latest": "gpt-5-chat-latest",
    "gpt-4o": "gpt-4o"
}

# Modèles par défaut pour chaque agent
DEFAULT_MODELS = {
    "analyste": "gemini-3-flash-preview",
    "generaliste": "gpt-4o",
    "orchestrateur": "gpt-5.2",
    "ranker": "gpt-4o",
    "redactionnel": "gemini-3-flash-preview",
    "specialises": "gemini-3-flash-preview",
    "suivi": "gemini-3-flash-preview",
    "verificateur": "gemini-3-flash-preview"
}

# Initialisation des modèles dans session_state
if 'agent_models' not in st.session_state:
    st.session_state.agent_models = DEFAULT_MODELS.copy()


def get_api_keys():
    """Récupère les clés API depuis les variables d'environnement ou les secrets Streamlit"""
    openai_key = os.getenv("OPENAI_API_KEY") or st.secrets.get("OPENAI_API_KEY", "")
    google_key = os.getenv("GOOGLE_API_KEY") or st.secrets.get("GOOGLE_API_KEY", "")
    serpapi_key = os.getenv("SERPAPI_API_KEY") or st.secrets.get("SERPAPI_API_KEY", "")
    
    return openai_key, google_key, serpapi_key


def get_model_name(agent_name: str) -> str:
    """Récupère le nom réel du modèle pour un agent donné"""
    model_key = st.session_state.agent_models.get(agent_name, DEFAULT_MODELS.get(agent_name))
    return MODEL_MAPPING.get(model_key, model_key)
    return MODEL_MAPPING.get(model_key, model_key)


def process_question(user_question: str, is_follow_up: bool = False, contexte: Dict = None) -> Dict:
    """
    Traite une question fiscale en suivant le workflow complet
    Si is_follow_up est True, utilise l'agent de suivi au lieu du workflow complet
    """
    openai_key, google_key, serpapi_key = get_api_keys()
    
    # Vérification des clés API
    if not openai_key or not google_key or not serpapi_key:
        st.error("⚠️ Les clés API ne sont pas configurées. Veuillez les définir dans les secrets Streamlit ou les variables d'environnement.")
        return None
    
    # Si c'est une question de suivi et qu'on a un contexte, utiliser l'agent de suivi
    if is_follow_up and contexte:
        try:
            status_text = st.empty()
            status_text.text("💭 Analyse de votre question de suivi...")
            
            model_suivi = get_model_name("suivi")
            reponse_suivi = agent_suivi(user_question, contexte, google_key, model_name=model_suivi)
            json_suivi = lire_json_beton(reponse_suivi)
            
            status_text.empty()
            
            return {
                "reponse": json_suivi,
                "sources": contexte.get("sources", []),
                "analyste": contexte.get("analyse", {}),
                "is_follow_up": True
            }
        except Exception as e:
            st.error(f"❌ Erreur lors du traitement de la question de suivi : {str(e)}")
            return None
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    try:
        # Étape 1 : Agent Analyste
        status_text.text("🔍 Analyse de la question...")
        progress_bar.progress(10)
        model_analyste = get_model_name("analyste")
        result_analyste = agent_analyste(user_question, google_key, model_name=model_analyste)
        analyst_json = lire_json_beton(result_analyste)
        
        # Étape 2 : Agent Orchestrateur
        status_text.text("🎯 Routage vers les agents spécialisés...")
        progress_bar.progress(20)
        model_orchestrateur = get_model_name("orchestrateur")
        rooting = agent_orchestrateur(user_question, result_analyste, openai_key, model_name=model_orchestrateur)
        rooting_clean = clean_json_codefence(rooting)
        routing_final = json.loads(rooting_clean)
        selected_agents = routing_final.get("selected_agents", [])
        
        # Étape 3 : Agents spécialisés
        status_text.text("👥 Consultation des agents spécialisés...")
        progress_bar.progress(30)
        agent_functions = {
            "AGENT_PARTICULIERS_REVENUS": agent_particulier_revenu,
            "AGENT_TVA_INDIRECTES": agent_tva_indirect,
            "AGENT_ENTREPRISES_IS": agent_entreprise_is,
            "AGENT_PATRIMOINE_TRANSMISSION": agent_patrimoine_transmission,
            "AGENT_STRUCTURES_MONTAGES": agent_structure_montage,
            "AGENT_INTERNATIONAL": agent_international
        }
        
        results = {}
        model_specialises = get_model_name("specialises")
        for agent_name in selected_agents:
            agent_fn = agent_functions.get(agent_name)
            if agent_fn:
                results[agent_name] = agent_fn(user_question, result_analyste, google_key, model_name=model_specialises)
        
        # Étape 4 : Agent Vérificateur
        status_text.text("✅ Vérification et nettoyage des sources...")
        progress_bar.progress(40)
        model_verificateur = get_model_name("verificateur")
        result_clean = agent_verificateur(user_question, result_analyste, results, google_key, model_name=model_verificateur)
        verified_sources = lire_json_beton(result_clean)
        
        # Étape 5 : Agent Généraliste (requêtes de recherche)
        status_text.text("🔎 Génération des requêtes de recherche...")
        progress_bar.progress(50)
        # Utiliser les domaines actifs pour générer les requêtes
        active_domains = st.session_state.get('active_domains', OFFICIAL_DOMAINS)
        model_generaliste = get_model_name("generaliste")
        queries = agent_generaliste(user_question, openai_key, active_domains=active_domains, model_name=model_generaliste)
        
        # Étape 6 : Concaténation des sources
        l_experts_articles = []
        for values_list in verified_sources.values():
            if isinstance(values_list, list):
                l_experts_articles.extend(values_list)
        
        full_queries = queries + l_experts_articles
        
        # Étape 7 : Recherche SerpAPI
        status_text.text("🌐 Recherche des sources officielles...")
        progress_bar.progress(60)
        # Utiliser les domaines actifs depuis la session state
        active_domains = st.session_state.get('active_domains', OFFICIAL_DOMAINS)
        structured_results = search_official_sources(full_queries, serpapi_key, active_domains=active_domains)
        
        # Étape 8 : Suppression des doublons
        unique_structured_results = []
        seen_urls = set()
        for res in structured_results:
            url = res.get('url')
            if url and url not in seen_urls:
                unique_structured_results.append(res)
                seen_urls.add(url)
        
        # Étape 9 : Ranking
        status_text.text("📊 Classement des sources...")
        progress_bar.progress(70)
        model_ranker = get_model_name("ranker")
        ranked_results = agent_ranker(
            user_question,
            unique_structured_results,
            result_analyste,
            openai_key,
            model=model_ranker
        )
        
        # Filtrage des résultats pertinents (score >= 0.8)
        ranked_keep = [x for x in ranked_results if x.get('keep', False) and x.get('score', 0) >= 0.8]
        
        # Étape 10 : Scraping
        status_text.text("📄 Extraction du contenu des sources...")
        progress_bar.progress(80)
        doc_enriched = scrapper(ranked_keep)
        
        # Étape 11 : Rédaction
        status_text.text("✍️ Rédaction de la réponse...")
        progress_bar.progress(90)
        model_redactionnel = get_model_name("redactionnel")
        answer = agent_redactionnel(user_question, result_analyste, doc_enriched, google_key, model_name=model_redactionnel)
        json_answer = lire_json_beton(answer)
        
        progress_bar.progress(100)
        status_text.text("✅ Terminé !")
        time.sleep(0.5)
        status_text.empty()
        progress_bar.empty()
        
        return {
            "reponse": json_answer,
            "sources": ranked_keep,
            "analyste": analyst_json
        }
        
    except Exception as e:
        st.error(f"❌ Erreur lors du traitement : {str(e)}")
        import traceback
        st.code(traceback.format_exc())
        return None


def main():
    """Fonction principale de l'application"""
    
    # Header
    st.title("📊 Assistant Fiscal Intelligent")
    st.markdown("""
    Posez votre question fiscale et obtenez une réponse détaillée avec les sources officielles pertinentes.
    Vous pouvez ensuite poursuivre la conversation avec des questions de suivi.
    
    **Sources consultées :** Legifrance, BOFiP, Conseil d'État, Cour de Cassation, etc.
    """)
    
    # Sidebar pour la configuration
    with st.sidebar:
        st.header("⚙️ Configuration")
        st.info("Les clés API doivent être configurées dans les secrets Streamlit ou les variables d'environnement.")
        
        # Afficher l'état des clés API
        openai_key, google_key, serpapi_key = get_api_keys()
        st.write("**État des clés API :**")
        st.write(f"- OpenAI: {'✅' if openai_key else '❌'}")
        st.write(f"- Google: {'✅' if google_key else '❌'}")
        st.write(f"- SerpAPI: {'✅' if serpapi_key else '❌'}")
        
        st.divider()
        
        # Sélection des sources actives
        st.header("🔍 Sources de recherche")
        st.caption("Sélectionnez les sources à utiliser pour la recherche")
        
        # Noms d'affichage plus lisibles pour chaque domaine
        domain_labels = {
            "legifrance.gouv.fr": "📜 Legifrance",
            "bofip.impots.gouv.fr": "📋 BOFiP",
            "conseil-etat.fr": "⚖️ Conseil d'État",
            "courdecassation.fr": "🏛️ Cour de Cassation",
            "conseil-constitutionnel.fr": "📐 Conseil Constitutionnel",
            "assemblee-nationale.fr": "🏛️ Assemblée Nationale",
            "senat.fr": "🏛️ Sénat",
            "fiscalonline.fr": "💼 FiscalOnline"
        }
        
        # Créer les checkboxes pour chaque domaine
        active_domains = []
        for domain in OFFICIAL_DOMAINS:
            label = domain_labels.get(domain, domain)
            is_active = st.checkbox(
                label,
                value=domain in st.session_state.active_domains,
                key=f"domain_{domain}"
            )
            if is_active:
                active_domains.append(domain)
        
        # Mettre à jour les domaines actifs
        st.session_state.active_domains = active_domains
        
        # Afficher le nombre de sources actives
        if active_domains:
            st.success(f"✅ {len(active_domains)} source(s) active(s)")
        else:
            st.warning("⚠️ Aucune source active. Activez au moins une source pour effectuer des recherches.")
        
        st.divider()
        
        # Sélection des modèles pour chaque agent
        st.header("🤖 Modèles IA")
        st.caption("Choisissez le modèle à utiliser pour chaque agent")
        
        # Liste des modèles disponibles
        available_models = list(MODEL_MAPPING.keys())
        
        # Labels des agents
        agent_labels = {
            "analyste": "🔍 Analyste",
            "generaliste": "🔎 Généraliste",
            "orchestrateur": "🎯 Orchestrateur",
            "ranker": "📊 Ranker",
            "redactionnel": "✍️ Rédactionnel",
            "specialises": "👥 Spécialisés",
            "suivi": "💭 Suivi",
            "verificateur": "✅ Vérificateur"
        }
        
        # Créer les selectbox pour chaque agent
        for agent_key in DEFAULT_MODELS.keys():
            label = agent_labels.get(agent_key, agent_key)
            current_model = st.session_state.agent_models.get(agent_key, DEFAULT_MODELS[agent_key])
            # S'assurer que le modèle actuel est valide
            if current_model not in available_models:
                current_model = DEFAULT_MODELS[agent_key]
                st.session_state.agent_models[agent_key] = current_model
            index = available_models.index(current_model) if current_model in available_models else 0
            selected_model = st.selectbox(
                label,
                options=available_models,
                index=index,
                key=f"model_{agent_key}",
                help=f"Modèle par défaut : {DEFAULT_MODELS[agent_key]}"
            )
            st.session_state.agent_models[agent_key] = selected_model
        
        st.divider()
        
        # Bouton pour réinitialiser la conversation
        if st.button("🗑️ Nouvelle conversation", use_container_width=True):
            st.session_state.messages = []
            st.session_state.contexte_conversation = None
            st.rerun()
        
        # Afficher le nombre de messages
        if st.session_state.messages:
            st.caption(f"💬 {len(st.session_state.messages)} message(s) dans la conversation")
    
    # Affichage de l'historique des messages
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            
            # Afficher les sources si c'est une réponse de l'assistant
            if message["role"] == "assistant" and "sources" in message and message["sources"]:
                with st.expander("📚 Sources référencées", expanded=False):
                    for idx, source in enumerate(message["sources"], 1): 
                        st.write(f"**{idx}.** [{source.get('title', 'Sans titre')}]({source.get('url', '#')})")
                        if source.get('snippet'):
                            st.caption(source.get('snippet'))
    
    # Zone de saisie de chat
    if prompt := st.chat_input("Posez votre question fiscale..."):
        # Ajouter le message de l'utilisateur à l'historique
        st.session_state.messages.append({"role": "user", "content": prompt})
        
        # Déterminer si c'est une question de suivi
        is_follow_up = st.session_state.contexte_conversation is not None and len(st.session_state.messages) > 1
        
        # Traiter la question
        with st.chat_message("assistant"):
            with st.spinner("Réflexion en cours..."):
                if is_follow_up:
                    result = process_question(prompt, is_follow_up=True, contexte=st.session_state.contexte_conversation)
                else:
                    result = process_question(prompt, is_follow_up=False)
                
                if result:
                    reponse_data = result.get("reponse", {})
                    sources = result.get("sources", [])
                    
                    # Extraire la réponse
                    if isinstance(reponse_data, dict):
                        reponse_redigee = reponse_data.get("reponse_redigee", "")
                        points_cles = reponse_data.get("points_cles", [])
                        necessite_recherche = reponse_data.get("necessite_nouvelle_recherche", False)
                        
                        # Afficher la réponse
                        st.markdown(reponse_redigee)
                        
                        # Afficher les points clés
                        if points_cles:
                            st.info("**Points importants :** " + " | ".join(points_cles))
                        
                        # Si une nouvelle recherche est nécessaire, le signaler
                        if necessite_recherche:
                            st.warning("⚠️ Cette question nécessite une nouvelle recherche complète. Veuillez poser une nouvelle question principale.")
                    else:
                        st.markdown(reponse_data)
                    
                    # Afficher les sources si disponibles
                    if sources and not result.get("is_follow_up", False):
                        with st.expander("📚 Sources pertinentes", expanded=False):
                            for idx, source in enumerate(sources, 1):
                                col1, col2 = st.columns([4, 1])
                                with col1:
                                    st.write(f"**{idx}.** [{source.get('title', 'Sans titre')}]({source.get('url', '#')})")
                                    if source.get('snippet'):
                                        st.caption(source.get('snippet'))
                                with col2:
                                    score = source.get('score', 0)
                                    st.metric("Score", f"{score:.2f}", delta=None)
                    
                    # Sauvegarder la réponse dans l'historique
                    message_content = reponse_redigee if isinstance(reponse_data, dict) else reponse_data
                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": message_content,
                        "sources": sources
                    })
                    
                    # Mettre à jour le contexte de conversation (seulement pour la première question)
                    if not is_follow_up:
                        st.session_state.contexte_conversation = {
                            "question_initial": prompt,
                            "reponse_initial": message_content,
                            "sources": sources,
                            "analyse": result.get("analyste", {})
                        }
                else:
                    error_msg = "Désolé, une erreur s'est produite lors du traitement de votre question."
                    st.error(error_msg)
                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": error_msg
                    })
        
        st.rerun()
    
    # Message d'accueil si aucune conversation
    if not st.session_state.messages:
        with st.chat_message("assistant"):
            st.markdown("""
            👋 Bonjour ! Je suis votre assistant fiscal intelligent.
            
            Je peux vous aider à :
            - Comprendre les règles fiscales françaises
            - Identifier les sources officielles pertinentes
            - Répondre à vos questions sur la fiscalité
            
            **Commencez par poser votre question fiscale ci-dessous !**
            
            Exemple : *"Ma fille a eu 18 ans en mars 2025 et poursuit ses études. Dois-je la rattacher à mon foyer fiscal pour la déclaration 2026 sur les revenus 2025 ?"*
            """)
    
    # Footer
    st.divider()
    st.caption("💡 Cet assistant utilise l'IA pour analyser les questions fiscales et identifier les sources officielles pertinentes.")


if __name__ == "__main__":
    main()
