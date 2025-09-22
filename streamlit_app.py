import sys
import traceback
from pathlib import Path
import streamlit as st

# Ensure we can import the pipeline module from the same directory
this_dir = Path(__file__).resolve().parent
if str(this_dir) not in sys.path:
    sys.path.insert(0, str(this_dir))
try:
    from fisca_llm import run_pipeline
except Exception as import_error:
    st.error("Impossible d'importer 'fisca_llm.py'. Assurez-vous que 'streamlit_app.py' et 'fisca_llm.py' sont dans le même dossier.")
    st.caption(str(import_error))
    st.stop()


st.set_page_config(page_title="Fisca LLM – Chat", page_icon="💬", layout="wide")

st.title("💬 Fisca LLM – Chat")
st.caption("Posez une question fiscale en français. L'app appelle un pipeline de recherche et affiche les sources trouvées (loi, doctrine, jurisprudence, articles).")


def render_docs(docs: list[dict]):
    if not docs:
        st.info("Aucune source trouvée.")
        return

    family_to_label = {
        "loi": "Loi",
        "doctrine": "Doctrine (BOFiP/RM/QE)",
        "jurisprudence": "Jurisprudence",
        "travaux_parlementaires":"Travaux parlemenaires",
        "fiscalonline": "FiscalOnline",
    }

    with st.container():
        st.subheader("Sources")
        for idx, d in enumerate(docs, start=1):
            title = d.get("title") or d.get("url") or "(Sans titre)"
            url = d.get("url", "")
            snippet = d.get("snippet", "")
            domain = d.get("source_domain", "")
            family = d.get("family", "")
            score = d.get("score", "")

            cols = st.columns([0.06, 0.72, 0.22])
            with cols[0]:
                st.markdown(f"**{idx}.**")
            with cols[1]:
                if url:
                    st.markdown(f"[{title}]({url})")
                else:
                    st.markdown(title)
                if snippet:
                    st.caption(snippet)
            with cols[2]:
                right_lines = []
                if domain:
                    right_lines.append(f"Domaine: `{domain}`")
                if family:
                    fam_label = family_to_label.get(family, family)
                    right_lines.append(f"Famille: `{fam_label}`")
                if score:
                    right_lines.append(f"Score: `{score}`")
                if right_lines:
                    st.write("\n".join(right_lines))
            st.divider()


if "messages" not in st.session_state:
    st.session_state.messages = []

# Render chat history
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        # If we stored docs with the assistant message, render them
        docs = message.get("docs")
        if docs and message["role"] == "assistant":
            render_docs(docs)


prompt = st.chat_input("Votre question fiscale…")
if prompt:
    # Show user message immediately
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Run pipeline and display assistant response + sources
    try:
        with st.chat_message("assistant"):
            status_placeholder = st.empty()
            with st.spinner("Recherche des sources…"):
                def _status_cb(msg: str):
                    try:
                        status_placeholder.markdown(f"**Agent actif**: {msg}")
                    except Exception:
                        pass
                result = run_pipeline(prompt, status_callback=_status_cb)

            status_placeholder.empty()
            docs = (result.get("ranked_docs") ) if isinstance(result, dict) else None
            brief = result.get("brief") if isinstance(result, dict) else None
            answer = result.get("answer") if isinstance(result, dict) else None

            # Affiche d'abord la réponse à la question de l'utilisateur
            if answer:
                st.markdown("#### Réponse à votre question")
                st.markdown(answer)

            # Puis affiche les sources utilisées comme avant
            if isinstance(brief, dict) and brief.get("issue"):
                answer_header = f"Voici des sources pertinentes pour: {brief.get('issue')}"
            else:
                answer_header = "Voici des sources pertinentes que j'ai trouvées."

            st.markdown(answer_header)
            render_docs(docs or [])

            # Persist assistant message with docs and answer for history re-rendering
            st.session_state.messages.append({
                "role": "assistant",
                "content": (f"#### Réponse à votre question\n{answer}\n\n" if answer else "") + answer_header,
                "docs": docs or [],
            })
    except Exception as e:
        error_details = "\n".join(traceback.format_exception_only(type(e), e)).strip()
        with st.chat_message("assistant"):
            st.error("Une erreur est survenue lors de l'exécution du pipeline.")
            st.caption(error_details)
        st.session_state.messages.append({
            "role": "assistant",
            "content": f"Une erreur est survenue. {error_details}",
        })
