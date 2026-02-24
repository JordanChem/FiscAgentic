"""
Application Streamlit pour l'assistant fiscal intelligent
"""
import streamlit as st
import os
import uuid
import logging
from typing import Dict, List
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from dotenv import load_dotenv

# Imports des agents
from agents.analyste import agent_analyste
from agents.orchestrateur import agent_orchestrateur
from agents.specialises import (
    agent_particulier_revenu,
    agent_tva_indirect,
    agent_entreprise_is,
    agent_patrimoine_transmission,
    agent_structure_montage,
    agent_international,
    agent_droit_europeen,
    agent_immobilier_urbanisme,
    agent_procedure_contentieux,
    agent_taxes_locales,
    agent_prelevements_sociaux
)
from agents.generaliste import agent_generaliste
from agents.verificateur import agent_verificateur
from utils.fiscalonline import main_fiscalonline
from agents.jurisprudence_dork import generate_jurisprudence_dork
from agents.ranker import agent_ranker
from agents.redactionnel import agent_redactionnel, agent_redactionnel_stream
from agents.suivi import agent_suivi

# Imports des utilitaires
from utils.json_utils import lire_json_beton
from utils.search import search_official_sources, OFFICIAL_DOMAINS
from utils.scraper_utils import scrapper
from utils.feedback import save_feedback, get_supabase_client
from utils.conversations import save_conversation, list_conversations, load_conversation, delete_conversation

# Charger les variables d'environnement (après tous les imports pour satisfaire le linter)
load_dotenv()

# Configuration du logging (une seule fois, au démarrage de l'app)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Mapping des agents spécialisés (module-level pour éviter la recréation à chaque appel)
AGENT_FUNCTIONS = {
    "AGENT_PARTICULIERS_REVENUS": agent_particulier_revenu,
    "AGENT_TVA_INDIRECTES": agent_tva_indirect,
    "AGENT_ENTREPRISES_IS": agent_entreprise_is,
    "AGENT_PATRIMOINE_TRANSMISSION": agent_patrimoine_transmission,
    "AGENT_STRUCTURES_MONTAGES": agent_structure_montage,
    "AGENT_INTERNATIONAL": agent_international,
    "AGENT_DROIT_EUROPEEN": agent_droit_europeen,
    "AGENT_IMMOBILIER_URBANISME": agent_immobilier_urbanisme,
    "AGENT_PROCEDURE_CONTENTIEUX": agent_procedure_contentieux,
    "AGENT_TAXES_LOCALES": agent_taxes_locales,
    "AGENT_PRELEVEMENTS_SOCIAUX": agent_prelevements_sociaux
}

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
if 'feedbacks_sent' not in st.session_state:
    st.session_state.feedbacks_sent = set()
if 'current_conversation_id' not in st.session_state:
    st.session_state.current_conversation_id = None
if 'user_email' not in st.session_state:
    st.session_state.user_email = None

# Mapping des noms de modèles vers les noms réels
MODEL_MAPPING = {
    "gemini-3-pro-preview": "gemini-3-pro-preview",
    "gemini-3-flash-preview": "gemini-3-flash-preview",
    "gemini-2.5-flash": "gemini-2.5-flash",
    "gpt-5.2": "gpt-5.2-2025-12-11",
    "gpt-4o": "gpt-4o"
}

# Modèles disponibles par famille (pour restreindre les choix par agent)
GEMINI_MODELS = ["gemini-3-pro-preview", "gemini-3-flash-preview", "gemini-2.5-flash"]
OPENAI_MODELS = ["gpt-5.2", "gpt-4o"]

# Agents utilisant l'API Google (Gemini)
GEMINI_AGENTS = {"analyste", "redactionnel", "specialises", "suivi", "verificateur", "jurisprudence"}
# Agents utilisant l'API OpenAI
OPENAI_AGENTS = {"orchestrateur", "generaliste", "ranker"}

# Modèles par défaut pour chaque agent
DEFAULT_MODELS = {
    "analyste": "gemini-3-flash-preview",
    "generaliste": "gpt-4o",
    "jurisprudence": "gemini-3-flash-preview",
    "orchestrateur": "gpt-4o",
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
    current_step = "Initialisation"
    t_pipeline = time.time()

    logger.info("=" * 60)
    logger.info("PIPELINE START — question: %r", user_question[:120])
    logger.info("=" * 60)

    try:
        # Étape 1 : Agent Analyste
        current_step = "Analyse de la question"
        status_text.text("🔍 Analyse de la question...")
        progress_bar.progress(10)
        model_analyste = get_model_name("analyste")
        logger.info("[1/9] Analyste — modèle: %s", model_analyste)
        t0 = time.time()
        result_analyste = agent_analyste(user_question, google_key, model_name=model_analyste)
        analyst_json = lire_json_beton(result_analyste)
        logger.info("[1/9] Analyste OK (%.1fs) — réponse: %d chars", time.time() - t0, len(result_analyste))

        # Lancement en parallèle : récupération articles FiscalOnline internes
        _fiscalonline_executor = ThreadPoolExecutor(max_workers=1)
        _fiscalonline_future = _fiscalonline_executor.submit(
            main_fiscalonline, user_question, result_analyste, openai_key
        )

        # Étape 2 : Agent Orchestrateur
        current_step = "Routage vers les agents spécialisés"
        status_text.text("🎯 Routage vers les agents spécialisés...")
        progress_bar.progress(20)
        model_orchestrateur = get_model_name("orchestrateur")
        logger.info("[2/9] Orchestrateur — modèle: %s", model_orchestrateur)
        t0 = time.time()
        rooting = agent_orchestrateur(user_question, result_analyste, openai_key, model_name=model_orchestrateur)
        # A4 : utiliser lire_json_beton (robuste) au lieu de json.loads nu
        routing_final = lire_json_beton(rooting)
        selected_agents = routing_final.get("selected_agents", [])
        scores = routing_final.get("scores", {})
        logger.info(
            "[2/9] Orchestrateur OK (%.1fs) — agents sélectionnés: %s",
            time.time() - t0,
            ", ".join(selected_agents) if selected_agents else "(aucun)"
        )
        for agent_name, score in sorted(scores.items(), key=lambda x: x[1], reverse=True):
            logger.info("       score %-35s %.2f", agent_name, score)

        # Étape 3 : Agents spécialisés
        current_step = "Consultation des agents spécialisés"
        status_text.text("👥 Consultation des agents spécialisés...")
        progress_bar.progress(30)

        results = {}
        model_specialises = get_model_name("specialises")
        active_domains = st.session_state.get('active_domains', OFFICIAL_DOMAINS)

        valid_agents = [name for name in selected_agents if name in AGENT_FUNCTIONS]

        # B1 : aucun agent sélectionné → message explicite
        if not valid_agents:
            logger.warning("[3/9] Aucun agent valide sélectionné — question hors périmètre fiscal")
            progress_bar.empty()
            status_text.empty()
            return {
                "reponse": {
                    "reponse_redigee": (
                        "**Ce type de question ne relève pas des domaines fiscaux couverts par cet assistant.**\n\n"
                        "L'assistant fiscal traite uniquement les sujets suivants : impôt sur le revenu, TVA, "
                        "impôt sur les sociétés, patrimoine et transmission, fiscalité internationale, "
                        "immobilier, procédure fiscale, taxes locales et prélèvements sociaux.\n\n"
                        "Merci de reformuler votre question dans ce cadre."
                    ),
                    "points_cles": ["Question hors périmètre fiscal couvert"]
                },
                "sources": [],
                "analyste": analyst_json
            }

        logger.info("[3/9] Agents spécialisés — lancement en parallèle: %s (modèle: %s)", ", ".join(valid_agents), model_specialises)
        t0 = time.time()

        def _call_specialist(agent_name):
            agent_fn = AGENT_FUNCTIONS.get(agent_name)
            if agent_fn:
                return agent_name, agent_fn(user_question, result_analyste, google_key, available_domain=active_domains, model_name=model_specialises)
            return agent_name, None

        with ThreadPoolExecutor(max_workers=max(1, len(valid_agents))) as executor:
            futures = {executor.submit(_call_specialist, name): name for name in valid_agents}
            for future in as_completed(futures):
                agent_name, result = future.result()
                if result is not None:
                    results[agent_name] = result
                    logger.info("       [3/9] %s OK", agent_name)
                else:
                    logger.warning("       [3/9] %s — résultat vide", agent_name)

        logger.info("[3/9] Agents spécialisés OK (%.1fs) — %d/%d agents ont répondu", time.time() - t0, len(results), len(valid_agents))

        # Étape 4 : Agent Vérificateur
        current_step = "Vérification des sources"
        status_text.text("✅ Vérification et nettoyage des sources...")
        progress_bar.progress(40)
        model_verificateur = get_model_name("verificateur")
        logger.info("[4/9] Vérificateur — modèle: %s", model_verificateur)
        t0 = time.time()
        result_clean = agent_verificateur(user_question, result_analyste, results, google_key, model_name=model_verificateur)
        verified_sources = lire_json_beton(result_clean)
        total_verified = sum(len(v) for v in verified_sources.values() if isinstance(v, list))
        logger.info("[4/9] Vérificateur OK (%.1fs) — %d sources vérifiées", time.time() - t0, total_verified)

        # Étape 5 : Agent Généraliste (requêtes de recherche)
        current_step = "Génération des requêtes de recherche"
        status_text.text("🔎 Génération des requêtes de recherche...")
        progress_bar.progress(45)
        # Utiliser les domaines actifs pour générer les requêtes
        active_domains = st.session_state.get('active_domains', OFFICIAL_DOMAINS)
        model_generaliste = get_model_name("generaliste")
        logger.info("[5/9] Généraliste — modèle: %s, domaines actifs: %d", model_generaliste, len(active_domains))
        t0 = time.time()
        queries = agent_generaliste(user_question, openai_key, active_domains=active_domains, model_name=model_generaliste)
        logger.info("[5/9] Généraliste OK (%.1fs) — %d requêtes générées", time.time() - t0, len(queries))

        # Étape 5bis : Recherche jurisprudence Cour de cassation
        current_step = "Recherche jurisprudence"
        status_text.text("⚖️ Recherche de jurisprudence Cour de cassation...")
        progress_bar.progress(50)
        logger.info("[5b] Jurisprudence dork...")
        t0 = time.time()
        try:
            model_jurisprudence = get_model_name("jurisprudence")
            jurisprudence_queries_raw = generate_jurisprudence_dork(user_question, result_analyste, google_key, model_name=model_jurisprudence)
            import ast as _ast
            jurisprudence_queries = _ast.literal_eval(jurisprudence_queries_raw) if isinstance(jurisprudence_queries_raw, str) else jurisprudence_queries_raw
            if not isinstance(jurisprudence_queries, list):
                jurisprudence_queries = []
        except Exception:
            jurisprudence_queries = []
        logger.info("[5b] Jurisprudence dork OK (%.1fs) — %d requêtes", time.time() - t0, len(jurisprudence_queries))

        # Étape 6 : Concaténation des sources
        l_experts_articles = []
        for values_list in verified_sources.values():
            if isinstance(values_list, list):
                l_experts_articles.extend(values_list)

        full_queries = queries + l_experts_articles + jurisprudence_queries
        logger.info(
            "[6/9] Requêtes totales: %d (%d généraliste + %d experts + %d jurisprudence)",
            len(full_queries), len(queries), len(l_experts_articles), len(jurisprudence_queries)
        )

        # Étape 7 : Recherche SerpAPI
        current_step = "Recherche des sources officielles (SerpAPI)"
        status_text.text("🌐 Recherche des sources officielles...")
        progress_bar.progress(60)
        # Utiliser les domaines actifs depuis la session state
        active_domains = st.session_state.get('active_domains', OFFICIAL_DOMAINS)
        logger.info("[7/9] SerpAPI — %d requêtes sur %d domaines...", len(full_queries), len(active_domains))
        t0 = time.time()
        structured_results = search_official_sources(full_queries, serpapi_key, active_domains=active_domains)
        logger.info("[7/9] SerpAPI OK (%.1fs) — %d résultats bruts", time.time() - t0, len(structured_results))

        # Étape 8 : Suppression des doublons
        current_step = "Déduplication des résultats"
        unique_structured_results = []
        seen_urls = set()
        for res in structured_results:
            url = res.get('url')
            if url and url not in seen_urls:
                unique_structured_results.append(res)
                seen_urls.add(url)
        logger.info("[8/9] Déduplication: %d → %d résultats uniques", len(structured_results), len(unique_structured_results))

        # Étape 9 : Ranking
        current_step = "Classement des sources"
        status_text.text("📊 Classement des sources...")
        progress_bar.progress(70)
        model_ranker = get_model_name("ranker")
        logger.info("[9/9] Ranker — modèle: %s, %d candidats", model_ranker, len(unique_structured_results))
        t0 = time.time()
        ranked_results = agent_ranker(
            user_question,
            unique_structured_results,
            result_analyste,
            results,  # specialists_results
            openai_key,
            model=model_ranker
        )
        n_keep_08 = sum(1 for x in ranked_results if x.get('keep', False) and x.get('score', 0) >= 0.8)
        n_keep_06 = sum(1 for x in ranked_results if x.get('keep', False) and x.get('score', 0) >= 0.6)
        n_drop = sum(1 for x in ranked_results if not x.get('keep', False))
        logger.info(
            "[9/9] Ranker OK (%.1fs) — keep≥0.8: %d | keep≥0.6: %d | drop: %d",
            time.time() - t0, n_keep_08, n_keep_06, n_drop
        )

        # Filtrage des résultats pertinents (score >= 0.8)
        current_step = "Filtrage des sources (seuil 0.8)"
        ranked_keep = [x for x in ranked_results if x.get('keep', False) and x.get('score', 0) >= 0.8]
        # B2 : si aucun résultat au seuil 0.8, on retente avec 0.6
        if not ranked_keep:
            ranked_keep = [x for x in ranked_results if x.get('keep', False) and x.get('score', 0) >= 0.6]
            logger.warning("Seuil 0.8 → 0 résultats, fallback seuil 0.6 → %d résultats", len(ranked_keep))
        else:
            logger.info("Filtrage seuil 0.8 → %d sources retenues", len(ranked_keep))

        # Étape 10 : Scraping
        current_step = "Extraction du contenu des sources"
        status_text.text("📄 Extraction du contenu des sources...")
        progress_bar.progress(80)
        logger.info("[10] Scraping — %d URLs...", len(ranked_keep))
        t0 = time.time()
        doc_enriched = scrapper(ranked_keep)
        n_ok = sum(1 for d in doc_enriched if d.get("content"))
        logger.info("[10] Scraping OK (%.1fs) — %d/%d URLs avec contenu", time.time() - t0, n_ok, len(doc_enriched))

        # Fusion avec les articles FiscalOnline récupérés en parallèle
        doc_fiscalonline = []
        try:
            doc_fiscalonline = _fiscalonline_future.result(timeout=60) or []
            _fiscalonline_executor.shutdown(wait=False)
            if doc_fiscalonline:
                logger.info("[10b] FiscalOnline — %d articles ajoutés à doc_enriched", len(doc_fiscalonline))
                doc_enriched = doc_fiscalonline + doc_enriched
        except Exception as e:
            logger.warning("[10b] FiscalOnline — erreur récupération articles : %s", e)

        # Étape 11 : Rédaction (streaming)
        current_step = "Rédaction de la réponse"
        status_text.text("✍️ Rédaction de la réponse...")
        progress_bar.progress(90)
        model_redactionnel = get_model_name("redactionnel")
        logger.info("[11] Rédactionnel — modèle: %s, %d docs enrichis", model_redactionnel, len(doc_enriched))

        # Préparer le générateur streaming
        stream_gen = agent_redactionnel_stream(user_question, result_analyste, doc_enriched, google_key, model_name=model_redactionnel)

        progress_bar.progress(100)
        status_text.text("✅ Terminé !")
        logger.info("=" * 60)
        logger.info("PIPELINE TERMINE en %.1fs", time.time() - t_pipeline)
        logger.info("=" * 60)
        time.sleep(0.3)
        status_text.empty()
        progress_bar.empty()

        return {
            "reponse_stream": stream_gen,
            "sources": doc_fiscalonline + ranked_keep,
            "analyste": analyst_json
        }
        
    except Exception as e:
        st.error(f"❌ Erreur à l'étape « {current_step} » : {str(e)}")
        import traceback
        st.code(traceback.format_exc())
        return None


def render_feedback(message_id: str, question: str, answer: str,
                    sources_count: int = 0, is_follow_up: bool = False):
    """Affiche le widget de feedback sous une réponse assistant"""
    already_sent = message_id in st.session_state.feedbacks_sent

    if already_sent:
        st.caption("✅ Merci pour votre retour !")
        return

    feedback = st.feedback("thumbs", key=f"fb_{message_id}")

    if feedback is not None:
        # feedback: 0 = thumbs down, 1 = thumbs up
        if feedback == 0:
            comment = st.text_input(
                "Qu'est-ce qui n'allait pas ?",
                key=f"comment_{message_id}",
                placeholder="Optionnel : décrivez le problème..."
            )
            if st.button("Envoyer", key=f"send_{message_id}"):
                ok = save_feedback(question, answer, rating=0, comment=comment or None,
                                   sources_count=sources_count, is_follow_up=is_follow_up,
                                   user_email=st.session_state.user_email)
                if ok:
                    st.session_state.feedbacks_sent.add(message_id)
                    st.rerun()
        else:
            ok = save_feedback(question, answer, rating=1,
                               sources_count=sources_count, is_follow_up=is_follow_up,
                               user_email=st.session_state.user_email)
            if ok:
                st.session_state.feedbacks_sent.add(message_id)
                st.rerun()


def auto_save_conversation():
    """Sauvegarde automatique de la conversation courante dans Supabase."""
    if not st.session_state.messages:
        return
    if not st.session_state.current_conversation_id:
        st.session_state.current_conversation_id = str(uuid.uuid4())
    save_conversation(
        st.session_state.current_conversation_id,
        st.session_state.messages,
        st.session_state.contexte_conversation,
        user_email=st.session_state.user_email,
    )


def main():
    """Fonction principale de l'application"""

    # --- Écran de login (Supabase Auth) ---
    if not st.session_state.user_email:
        st.title("📊 Assistant Fiscal Intelligent")
        st.markdown("Connectez-vous pour accéder à l'assistant fiscal.")
        email = st.text_input("Email")
        password = st.text_input("Mot de passe", type="password")
        if st.button("Se connecter", use_container_width=True):
            client = get_supabase_client()
            if not client:
                st.error("Configuration Supabase manquante.")
            else:
                try:
                    res = client.auth.sign_in_with_password({"email": email, "password": password})
                    st.session_state.user_email = res.user.email
                    st.rerun()
                except Exception:
                    st.error("Email ou mot de passe incorrect.")
        st.stop()

    # --- Application principale (utilisateur connecté) ---

    # Header
    st.title("📊 Assistant Fiscal Intelligent")
    st.markdown("""
    Posez votre question fiscale et obtenez une réponse détaillée avec les sources officielles pertinentes.
    Vous pouvez ensuite poursuivre la conversation avec des questions de suivi.

    **Sources consultées :** Legifrance, BOFiP, Conseil d'État, Cour de Cassation, etc.
    """)

    # Sidebar pour la configuration
    with st.sidebar:
        # Utilisateur connecté + déconnexion
        st.write(f"👤 {st.session_state.user_email}")
        if st.button("Se déconnecter"):
            st.session_state.user_email = None
            st.session_state.messages = []
            st.session_state.contexte_conversation = None
            st.session_state.current_conversation_id = None
            st.rerun()

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
            "fiscalonline.fr": "💼 FiscalOnline",
            "europa.eu": "🇪🇺 CJUE (Europe)"
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
        
        # Labels des agents
        agent_labels = {
            "analyste": "🔍 Analyste",
            "generaliste": "🔎 Généraliste",
            "jurisprudence": "⚖️ Jurisprudence",
            "orchestrateur": "🎯 Orchestrateur",
            "ranker": "📊 Ranker",
            "redactionnel": "✍️ Rédactionnel",
            "specialises": "👥 Spécialisés",
            "suivi": "💭 Suivi",
            "verificateur": "✅ Vérificateur"
        }

        # Créer les selectbox pour chaque agent (modèles filtrés par famille API)
        for agent_key in DEFAULT_MODELS.keys():
            label = agent_labels.get(agent_key, agent_key)
            agent_models = GEMINI_MODELS if agent_key in GEMINI_AGENTS else OPENAI_MODELS
            current_model = st.session_state.agent_models.get(agent_key, DEFAULT_MODELS[agent_key])
            # S'assurer que le modèle actuel est valide pour cet agent
            if current_model not in agent_models:
                current_model = DEFAULT_MODELS[agent_key]
                st.session_state.agent_models[agent_key] = current_model
            index = agent_models.index(current_model) if current_model in agent_models else 0
            selected_model = st.selectbox(
                label,
                options=agent_models,
                index=index,
                key=f"model_{agent_key}",
                help=f"Modèle par défaut : {DEFAULT_MODELS[agent_key]}"
            )
            st.session_state.agent_models[agent_key] = selected_model
        
        st.divider()
        
        # Bouton pour réinitialiser la conversation
        if st.button("🗑️ Nouvelle conversation", use_container_width=True):
            # Sauvegarder la conversation courante avant reset
            if st.session_state.messages and st.session_state.current_conversation_id:
                save_conversation(
                    st.session_state.current_conversation_id,
                    st.session_state.messages,
                    st.session_state.contexte_conversation,
                    user_email=st.session_state.user_email,
                )
            st.session_state.messages = []
            st.session_state.contexte_conversation = None
            st.session_state.current_conversation_id = None
            st.rerun()

        # Afficher le nombre de messages
        if st.session_state.messages:
            conv_status = "💾" if st.session_state.current_conversation_id else "📝"
            st.caption(f"{conv_status} {len(st.session_state.messages)} message(s) dans la conversation")

        st.divider()

        # --- Historique des conversations ---
        st.header("📂 Historique")

        past_conversations = list_conversations(limit=15, user_email=st.session_state.user_email)

        if past_conversations:
            for conv in past_conversations:
                conv_id = conv["id"]
                conv_title = conv["title"]
                conv_count = conv.get("message_count", 0)
                conv_date = conv.get("updated_at", "")[:10]

                is_current = conv_id == st.session_state.current_conversation_id
                prefix = "▶ " if is_current else ""

                col_title, col_delete = st.columns([5, 1])

                with col_title:
                    label = f"{prefix}{conv_title}"
                    if st.button(
                        label,
                        key=f"load_{conv_id}",
                        use_container_width=True,
                        disabled=is_current,
                        help=f"{conv_count} messages - {conv_date}",
                    ):
                        # Sauvegarder la conversation courante
                        if st.session_state.messages and st.session_state.current_conversation_id:
                            save_conversation(
                                st.session_state.current_conversation_id,
                                st.session_state.messages,
                                st.session_state.contexte_conversation,
                                user_email=st.session_state.user_email,
                            )
                        # Charger la conversation sélectionnée
                        loaded = load_conversation(conv_id, user_email=st.session_state.user_email)
                        if loaded:
                            st.session_state.messages = loaded.get("messages", [])
                            st.session_state.contexte_conversation = loaded.get("contexte_conversation")
                            st.session_state.current_conversation_id = conv_id
                            st.session_state.feedbacks_sent = set()
                            st.rerun()
                        else:
                            st.error("Impossible de charger cette conversation.")

                with col_delete:
                    if st.button("🗑", key=f"del_{conv_id}", help="Supprimer"):
                        if delete_conversation(conv_id, user_email=st.session_state.user_email):
                            if conv_id == st.session_state.current_conversation_id:
                                st.session_state.messages = []
                                st.session_state.contexte_conversation = None
                                st.session_state.current_conversation_id = None
                            st.rerun()
        else:
            st.caption("Aucune conversation sauvegardée.")
    
    # Affichage de l'historique des messages
    for i, message in enumerate(st.session_state.messages):
        # Assigner un ID stable aux messages qui n'en ont pas encore
        if "id" not in message:
            message["id"] = str(uuid.uuid4())

        with st.chat_message(message["role"]):
            st.markdown(message["content"])

            # Afficher les sources si c'est une réponse de l'assistant
            if message["role"] == "assistant" and "sources" in message and message["sources"]:
                with st.expander("📚 Sources référencées", expanded=False):
                    for idx, source in enumerate(message["sources"], 1):
                        st.write(f"**{idx}.** [{source.get('title', 'Sans titre')}]({source.get('url', '#')})")
                        if source.get('snippet'):
                            st.caption(source.get('snippet'))

            # Feedback pour les réponses assistant
            if message["role"] == "assistant":
                # Trouver la question associée (message précédent)
                question_text = ""
                if i > 0 and st.session_state.messages[i - 1]["role"] == "user":
                    question_text = st.session_state.messages[i - 1]["content"]
                render_feedback(
                    message_id=message["id"],
                    question=question_text,
                    answer=message["content"],
                    sources_count=len(message.get("sources", [])),
                    is_follow_up=i > 2
                )
    
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
                    sources = result.get("sources", [])

                    # Streaming response (full pipeline)
                    if "reponse_stream" in result:
                        stream_gen = result["reponse_stream"]
                        full_text = st.write_stream(stream_gen)
                        reponse_data = lire_json_beton(full_text)
                    else:
                        # Follow-up (non-streaming)
                        reponse_data = result.get("reponse", {})

                    # Extraire la réponse
                    if isinstance(reponse_data, dict):
                        reponse_redigee = reponse_data.get("reponse_redigee", "")
                        points_cles = reponse_data.get("points_cles", [])
                        necessite_recherche = reponse_data.get("necessite_nouvelle_recherche", False)

                        # Afficher la réponse (non-streaming path, e.g. follow-ups)
                        if "reponse_stream" not in result:
                            st.markdown(reponse_redigee)

                        # Afficher les points clés
                        if points_cles:
                            st.info("**Points importants :** " + " | ".join(points_cles))

                        # Si une nouvelle recherche est nécessaire, le signaler
                        if necessite_recherche:
                            st.warning("⚠️ Cette question nécessite une nouvelle recherche complète. Veuillez poser une nouvelle question principale.")
                    else:
                        if "reponse_stream" not in result:
                            st.markdown(reponse_data)
                        reponse_redigee = reponse_data if isinstance(reponse_data, str) else str(reponse_data)

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
                    if isinstance(message_content, dict):
                        message_content = message_content.get("reponse_redigee", str(message_content))
                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": message_content,
                        "sources": sources,
                        "id": str(uuid.uuid4())
                    })

                    # Mettre à jour le contexte de conversation (seulement pour la première question)
                    # Note: auto_save_conversation() est appelé après la mise à jour du contexte
                    if not is_follow_up:
                        st.session_state.contexte_conversation = {
                            "question_initial": prompt,
                            "reponse_initial": message_content,
                            "sources": sources,
                            "analyse": result.get("analyste", {})
                        }

                    # Auto-save conversation dans Supabase
                    auto_save_conversation()
                else:
                    error_msg = "Désolé, une erreur s'est produite lors du traitement de votre question."
                    st.error(error_msg)
                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": error_msg,
                        "id": str(uuid.uuid4())
                    })
                    auto_save_conversation()

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
