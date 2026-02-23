"""
Module de persistance des conversations avec Supabase
"""
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

import streamlit as st
from utils.feedback import get_supabase_client


def _strip_heavy_content(sources: List[Dict]) -> List[Dict]:
    """Retire le champ 'content' des sources, garde un aperçu de 200 chars."""
    if not sources:
        return sources
    stripped = []
    for source in sources:
        s = dict(source)
        full_content = s.pop("content", "")
        s.pop("raw_html", None)
        if full_content and isinstance(full_content, str):
            s["content_preview"] = full_content[:200]
        stripped.append(s)
    return stripped


def _prepare_messages_for_storage(messages: List[Dict]) -> List[Dict]:
    """Copie les messages en retirant le contenu lourd des sources."""
    prepared = []
    for msg in messages:
        m = dict(msg)
        if "sources" in m and m["sources"]:
            m["sources"] = _strip_heavy_content(m["sources"])
        prepared.append(m)
    return prepared


def _prepare_context_for_storage(contexte: Optional[Dict]) -> Optional[Dict]:
    """Copie le contexte en retirant le contenu lourd des sources."""
    if not contexte:
        return contexte
    ctx = dict(contexte)
    if "sources" in ctx and ctx["sources"]:
        ctx["sources"] = _strip_heavy_content(ctx["sources"])
    if "reponse_initial" in ctx and isinstance(ctx["reponse_initial"], str):
        ctx["reponse_initial"] = ctx["reponse_initial"][:5000]
    return ctx


def generate_title(messages: List[Dict]) -> str:
    """Génère un titre depuis la première question utilisateur (80 chars max)."""
    for msg in messages:
        if msg.get("role") == "user":
            title = msg["content"].strip()
            if len(title) > 80:
                title = title[:77] + "..."
            return title
    return "Conversation sans titre"


def save_conversation(
    conversation_id: str,
    messages: List[Dict],
    contexte_conversation: Optional[Dict],
    title: Optional[str] = None,
    user_email: Optional[str] = None,
) -> bool:
    """Sauvegarde ou met à jour une conversation (upsert)."""
    client = get_supabase_client()
    if not client or not messages:
        return False

    try:
        data = {
            "id": conversation_id,
            "title": title or generate_title(messages),
            "messages": _prepare_messages_for_storage(messages),
            "contexte_conversation": _prepare_context_for_storage(contexte_conversation),
            "message_count": len(messages),
        }
        if user_email:
            data["user_email"] = user_email
        client.table("conversations").upsert(data).execute()
        return True
    except Exception as e:
        print(f"Erreur sauvegarde conversation: {e}")
        return False


def list_conversations(limit: int = 15, user_email: Optional[str] = None) -> List[Dict]:
    """Liste les conversations récentes (triées par date de mise à jour)."""
    client = get_supabase_client()
    if not client:
        return []

    try:
        query = (
            client.table("conversations")
            .select("id, title, message_count, updated_at, created_at")
            .is_("deleted_at", "null")
        )
        if user_email:
            query = query.eq("user_email", user_email)
        response = query.order("updated_at", desc=True).limit(limit).execute()
        return response.data or []
    except Exception as e:
        print(f"Erreur listing conversations: {e}")
        return []


def load_conversation(conversation_id: str, user_email: Optional[str] = None) -> Optional[Dict]:
    """Charge une conversation complète par son ID."""
    client = get_supabase_client()
    if not client:
        return None

    try:
        query = (
            client.table("conversations")
            .select("*")
            .eq("id", conversation_id)
            .is_("deleted_at", "null")
        )
        if user_email:
            query = query.eq("user_email", user_email)
        response = query.single().execute()
        return response.data
    except Exception as e:
        print(f"Erreur chargement conversation: {e}")
        return None


def delete_conversation(conversation_id: str, user_email: Optional[str] = None) -> bool:
    """Soft-delete d'une conversation (met deleted_at à maintenant)."""
    client = get_supabase_client()
    if not client:
        return False

    try:
        query = client.table("conversations").update({
            "deleted_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", conversation_id)
        if user_email:
            query = query.eq("user_email", user_email)
        query.execute()
        return True
    except Exception as e:
        print(f"Erreur suppression conversation: {e}")
        return False
