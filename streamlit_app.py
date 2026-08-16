"""
UI Streamlit de debug de l'assistant fiscal.

⚠️ Ce n'est **plus** la cible de production : celle-ci est l'API FastAPI
(`api/`), consommée par fiscalonline.fr. Cette application reste l'outil de
test et de démonstration interne.

Elle appelle exactement les mêmes fonctions que l'API — `run_pipeline_stream`
et `run_follow_up` — et ne contient plus aucune logique métier. C'est ce qui
garantit que ce qu'on observe ici est bien ce que voient les utilisateurs :
la version précédente embarquait sa propre copie du pipeline, qui avait dérivé
de la version headless (branche FiscalOnline, parse jurisprudence, modèles).
"""
import logging
import uuid
from typing import Dict, List, Optional

import streamlit as st
from dotenv import load_dotenv
from streamlit_cookies_controller import CookieController

from pipeline.core import DEFAULT_MODELS, TraceOptions, run_pipeline_stream
from pipeline.events import ResultEvent, SourcesEvent, StepEvent, TextDelta
from pipeline.followup import build_contexte, run_follow_up
from services.supabase import get_supabase_client
from utils.conversations import (
    delete_conversation, list_conversations, load_conversation, save_conversation,
)
from utils.feedback import save_feedback
from utils.search import OFFICIAL_DOMAINS

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

st.set_page_config(
    page_title="Assistant Fiscal Intelligent",
    page_icon="📊",
    layout="wide",
)

# ─── État de session ──────────────────────────────────────────────────────────
_DEFAULT_STATE = {
    "messages": [],
    "contexte_conversation": None,
    "use_justicelibre": True,
    "feedbacks_sent": set(),
    "current_conversation_id": None,
    "user_email": None,
}
for key, value in _DEFAULT_STATE.items():
    if key not in st.session_state:
        st.session_state[key] = value.copy() if isinstance(value, (list, dict, set)) else value
if "active_domains" not in st.session_state:
    st.session_state.active_domains = OFFICIAL_DOMAINS.copy()


def process_question(question: str, contexte: Optional[Dict] = None) -> Optional[Dict]:
    """Traite une question et affiche la progression puis la réponse streamée.

    Retourne {answer, points_cles, sources, analyse, trace_id, is_follow_up},
    ou None en cas d'échec.
    """
    trace = TraceOptions(
        session_id=st.session_state.current_conversation_id,
        user_id=st.session_state.user_email,
        tags=["follow-up"] if contexte else ["question"],
    )

    # ── Question de suivi ────────────────────────────────────────────────────
    if contexte:
        with st.spinner("💭 Analyse de votre question de suivi..."):
            follow = run_follow_up(question, contexte, trace=trace)

        if follow.error:
            st.error(f"❌ Erreur lors du traitement de la question de suivi : {follow.error}")
            return None

        if follow.necessite_nouvelle_recherche:
            # L'API enchaîne automatiquement ; on reproduit ici le même choix.
            st.info("🔄 Cette question sort du contexte — relance d'une recherche complète.")
            return process_question(question, contexte=None)

        st.markdown(follow.answer_text)
        return {
            "answer": follow.answer_text,
            "points_cles": follow.points_cles,
            "sources": contexte.get("sources", []),
            "analyse": contexte.get("analyse", {}),
            "trace_id": follow.trace_id,
            "is_follow_up": True,
        }

    # ── Pipeline complet ─────────────────────────────────────────────────────
    progress_bar = st.progress(0)
    status = st.empty()
    placeholder = st.empty()
    buffer: List[str] = []
    result = None
    sources: List[Dict] = []

    try:
        for event in run_pipeline_stream(
            question,
            active_domains=st.session_state.active_domains,
            use_justicelibre=st.session_state.use_justicelibre,
            trace=trace,
        ):
            if isinstance(event, StepEvent):
                if event.status == "running":
                    status.text(f"{event.label}…")
                    progress_bar.progress(min(event.progress, 100))
            elif isinstance(event, SourcesEvent):
                sources = event.sources
                status.text("Rédaction de la réponse…")
                progress_bar.progress(90)
            elif isinstance(event, TextDelta):
                # Le pipeline émet déjà du markdown : plus de JSON qui s'écrit
                # à l'écran comme dans la version précédente.
                buffer.append(event.delta)
                placeholder.markdown("".join(buffer))
            elif isinstance(event, ResultEvent):
                result = event.result

        progress_bar.progress(100)
    except Exception as exc:
        logger.exception("Pipeline en échec")
        st.error(f"❌ Erreur lors du traitement : {exc}")
        return None
    finally:
        progress_bar.empty()
        status.empty()

    if result is None or result.error:
        st.error(f"❌ Erreur : {result.error if result else 'aucun résultat'}")
        return None

    placeholder.markdown(result.answer_text)
    return {
        "answer": result.answer_text,
        "points_cles": result.points_cles,
        "sources": result.sources or sources,
        "analyse": result.analyste,
        "trace_id": result.trace_id,
        "is_follow_up": False,
    }


def render_feedback(message_id: str, question: str, answer: str,
                    sources_count: int = 0, is_follow_up: bool = False,
                    trace_id: str = None):
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
                                   user_email=st.session_state.user_email, trace_id=trace_id)
                if ok:
                    st.session_state.feedbacks_sent.add(message_id)
                    st.rerun()
        else:
            ok = save_feedback(question, answer, rating=1,
                               sources_count=sources_count, is_follow_up=is_follow_up,
                               user_email=st.session_state.user_email, trace_id=trace_id)
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

    # Cookie controller (persistance de session entre rechargements)
    cookie = CookieController()

    # --- Restauration de session via cookie ---
    if not st.session_state.user_email:
        token = cookie.get("fisca_token")
        if token:
            try:
                client = get_supabase_client()
                user_data = client.auth.get_user(token)
                st.session_state.user_email = user_data.user.email
            except Exception:
                cookie.remove("fisca_token")

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
                    cookie.set("fisca_token", res.session.access_token, max_age=7 * 24 * 3600)
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
            cookie.remove("fisca_token")
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

        # JusticeLibre MCP
        st.subheader("⚖️ JusticeLibre")
        use_jl = st.toggle(
            "Activer JusticeLibre (TA / CAA / CE / CJUE)",
            value=st.session_state.use_justicelibre,
            key="toggle_justicelibre",
            help="Jurisprudence administrative gratuite (40 TA + 9 CAA + CE + CJUE). Fallback automatique sur SerpAPI si indisponible.",
        )
        st.session_state.use_justicelibre = use_jl
        if use_jl:
            st.caption("✅ Actif — CE/CAA/TA/CJUE via MCP, fallback SerpAPI auto")
        else:
            st.caption("⬜ Désactivé — SerpAPI seul")

        st.divider()

        # Modèles utilisés (lecture seule) — la configuration de production vit
        # dans pipeline.core.DEFAULT_MODELS, partagée avec l'API.
        with st.expander("🤖 Modèles utilisés", expanded=False):
            for agent, model in DEFAULT_MODELS.items():
                st.caption(f"**{agent}** — `{model}`")

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
                    is_follow_up=i > 2,
                    trace_id=message.get("trace_id")
                )
    
    # Zone de saisie de chat
    # Zone de saisie de chat
    if prompt := st.chat_input("Posez votre question fiscale..."):
        st.session_state.messages.append({"role": "user", "content": prompt})

        contexte = st.session_state.contexte_conversation
        is_follow_up = contexte is not None and len(st.session_state.messages) > 1

        # Id de conversation stable AVANT d'ouvrir la trace : il sert de
        # session_id Langfuse et regroupe tous les tours d'une conversation.
        if not st.session_state.current_conversation_id:
            st.session_state.current_conversation_id = str(uuid.uuid4())

        with st.chat_message("assistant"):
            result = process_question(prompt, contexte=contexte if is_follow_up else None)

            if result:
                sources = result["sources"]

                if result["points_cles"]:
                    st.info("**Points importants :** " + " | ".join(result["points_cles"]))

                if sources and not result["is_follow_up"]:
                    with st.expander("📚 Sources pertinentes", expanded=False):
                        for idx, source in enumerate(sources, 1):
                            col1, col2 = st.columns([4, 1])
                            with col1:
                                st.write(f"**{idx}.** [{source.get('title', 'Sans titre')}]"
                                         f"({source.get('url', '#')})")
                                if source.get("snippet"):
                                    st.caption(source["snippet"])
                            with col2:
                                st.metric("Score", f"{source.get('score', 0):.2f}")

                st.session_state.messages.append({
                    "role": "assistant",
                    "content": result["answer"],
                    "sources": sources,
                    "id": str(uuid.uuid4()),
                    "trace_id": result["trace_id"],      # rattache le feedback
                })

                # Contexte rafraîchi à CHAQUE tour : la version précédente ne
                # l'écrivait qu'après la première question, si bien que tous les
                # suivis d'une longue conversation raisonnaient sur le seul
                # premier échange.
                st.session_state.contexte_conversation = build_contexte(
                    prompt, result["answer"], sources, result["analyse"],
                    previous=contexte,
                )
                auto_save_conversation()
            else:
                error_msg = ("Désolé, une erreur s'est produite lors du traitement "
                             "de votre question.")
                st.session_state.messages.append({
                    "role": "assistant", "content": error_msg, "id": str(uuid.uuid4()),
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
