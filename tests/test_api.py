"""
Tests de la plomberie HTTP : authentification, isolation, SSE, capacité.

Le pipeline est substitué (cf. conftest). Ce qui est vérifié ici, ce sont les
propriétés que l'évaluation ne couvre pas et dont dépend la mise en production.
"""
from __future__ import annotations

import pytest

from tests.conftest import (
    API_KEY, USER_A, USER_B, frames_of_type, meta_of, sse_frames, streamed_text,
)

CHAT = "/v1/chat"


# ─── Authentification ─────────────────────────────────────────────────────────
def test_chat_sans_cle_est_refuse(client):
    r = client.post(CHAT, json={"message": "question"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


def test_chat_avec_mauvaise_cle_est_refuse(client):
    r = client.post(CHAT, json={"message": "q"},
                    headers={"X-API-Key": "mauvaise", "X-User-Email": "a@b.fr"})
    assert r.status_code == 401


def test_chat_sans_identite_utilisateur_est_refuse(client):
    r = client.post(CHAT, json={"message": "q"}, headers={"X-API-Key": API_KEY})
    assert r.status_code == 401
    assert "X-User-Email" in r.json()["error"]["message"]


def test_health_est_public(client):
    assert client.get("/health").status_code == 200


def test_ready_exige_la_cle(client):
    assert client.get("/ready").status_code == 401


# ─── Validation ───────────────────────────────────────────────────────────────
def test_message_vide_rejete(client):
    r = client.post(CHAT, json={"message": "   "}, headers=USER_A)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"


def test_domaine_inconnu_rejete(client):
    r = client.post(CHAT, json={"message": "q", "options": {"active_domains": ["evil.com"]}},
                    headers=USER_A)
    assert r.status_code == 422


def test_surcharge_de_modeles_interdite_par_defaut(client, fake_pipeline):
    r = client.post(CHAT, json={"message": "q", "options": {"models": {"ranker": "gpt-4o"}}},
                    headers=USER_A)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


def test_corps_trop_volumineux_rejete(client):
    r = client.post(CHAT, content=b"x" * 100_000,
                    headers={**USER_A, "Content-Type": "application/json"})
    assert r.status_code == 413


# ─── Corps natif de useChat ───────────────────────────────────────────────────
def test_accepte_le_corps_useChat_v5(client, store, fake_pipeline):
    r = client.post(CHAT, headers=USER_A, json={
        "id": "chat-123",
        "messages": [{"role": "user", "parts": [{"type": "text", "text": "Quel taux de TVA ?"}]}],
    })
    assert r.status_code == 200
    assert fake_pipeline[0]["question"] == "Quel taux de TVA ?"


def test_accepte_le_corps_useChat_v4(client, store, fake_pipeline):
    r = client.post(CHAT, headers=USER_A, json={
        "messages": [{"role": "assistant", "content": "…"},
                     {"role": "user", "content": "Question v4"}],
    })
    assert r.status_code == 200
    assert fake_pipeline[0]["question"] == "Question v4"


# ─── Flux SSE ─────────────────────────────────────────────────────────────────
def test_le_flux_respecte_le_protocole_ai_sdk(client, store, fake_pipeline):
    r = client.post(CHAT, json={"message": "Quel taux de TVA ?"}, headers=USER_A)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["x-vercel-ai-ui-message-stream"] == "v1"
    # Sans cet en-tête, nginx bufferise et le streaming disparaît.
    assert r.headers["x-accel-buffering"] == "no"

    frames = sse_frames(r.text)
    types = [f["type"] for f in frames]
    assert types[0] == "start"
    assert types[-1] == "[DONE]"
    assert "finish" in types
    for expected in ("data-progress", "data-sources", "text-start", "text-delta",
                     "text-end", "data-points_cles", "data-meta"):
        assert expected in types, f"trame {expected} absente"


def test_la_progression_precede_le_texte(client, store, fake_pipeline):
    frames = sse_frames(client.post(CHAT, json={"message": "q"}, headers=USER_A).text)
    types = [f["type"] for f in frames]
    assert types.index("data-progress") < types.index("text-delta")
    assert types.index("data-sources") < types.index("text-delta")


def test_les_metadonnees_d_etape_ne_cassent_pas_l_encodeur(client, store, fake_pipeline):
    """Le pipeline nomme librement ses métadonnées d'étape (`total` = nombre d'URL
    scrapées, par exemple). Elles ne doivent jamais entrer en collision avec la
    signature de l'encodeur SSE — un splat `**meta` faisait échouer tout le flux."""
    frames = sse_frames(client.post(CHAT, json={"message": "q"}, headers=USER_A).text)
    assert not frames_of_type(frames, "error"), "le flux a échoué"

    scraping = [f for f in frames_of_type(frames, "data-progress")
                if f["data"]["step"] == "scraping" and f["data"]["status"] == "done"]
    assert scraping, "étape scraping absente"
    data = scraping[0]["data"]
    assert data["total"] == 11          # champ de l'enveloppe : nombre d'étapes
    assert data["meta"]["total"] == 18  # métadonnée de l'étape : nombre d'URL
    assert data["meta"]["avec_contenu"] == 15


def test_la_progression_est_transitoire_et_reconciliee(client, store, fake_pipeline):
    frames = sse_frames(client.post(CHAT, json={"message": "q"}, headers=USER_A).text)
    progress = frames_of_type(frames, "data-progress")
    assert progress, "aucune trame de progression"
    # transient → non persistée dans le message ; id constant → une seule part.
    assert all(f.get("transient") is True for f in progress)
    assert len({f.get("id") for f in progress}) == 1


def test_le_texte_streame_est_du_markdown_pas_du_json(client, store, fake_pipeline):
    text = streamed_text(sse_frames(client.post(CHAT, json={"message": "q"},
                                                headers=USER_A).text))
    assert text == "## En résumé\nLe taux est de **20 %**.\n"
    assert "reponse_redigee" not in text
    assert not text.lstrip().startswith("{")


def test_les_sources_ne_contiennent_jamais_le_contenu_scrape(client, store, fake_pipeline):
    frames = sse_frames(client.post(CHAT, json={"message": "q"}, headers=USER_A).text)
    for frame in frames_of_type(frames, "data-sources"):
        for source in frame["data"]["sources"]:
            assert "content" not in source
            assert "raw_html" not in source


def test_meta_porte_les_identifiants_de_suivi(client, store, fake_pipeline):
    meta = meta_of(sse_frames(client.post(CHAT, json={"message": "q"}, headers=USER_A).text))
    assert meta["trace_id"] == "fisca-test-trace"
    assert meta["conversation_id"]
    assert meta["cost_usd"] == pytest.approx(0.0123)
    assert meta["is_follow_up"] is False
    assert meta["saved"] is True


# ─── Persistance et isolation ─────────────────────────────────────────────────
def test_la_conversation_est_persistee(client, store, fake_pipeline):
    meta = meta_of(sse_frames(client.post(CHAT, json={"message": "Ma question"},
                                          headers=USER_A).text))
    row = store.rows[meta["conversation_id"]]
    assert row["user_email"] == "a@fiscalonline.fr"
    assert [m["role"] for m in row["messages"]] == ["user", "assistant"]
    assert row["contexte_conversation"]["question_initial"] == "Ma question"


def test_l_id_useChat_est_normalise_en_uuid(client, store, fake_pipeline):
    """`conversations.id` est de type `uuid` côté Postgres, or `useChat` génère
    des identifiants alphanumériques quelconques. Sans normalisation, la
    sauvegarde échoue en production alors que la réponse s'affiche normalement —
    panne invisible côté utilisateur."""
    import uuid as _uuid

    meta = meta_of(sse_frames(client.post(CHAT, json={"id": "aBc123XyZ", "message": "q"},
                                          headers=USER_A).text))
    _uuid.UUID(meta["conversation_id"])          # lève si ce n'est pas un UUID
    assert meta["saved"] is True

    # Dérivation déterministe : le même id client retombe sur la même ligne.
    meta2 = meta_of(sse_frames(client.post(CHAT, json={"id": "aBc123XyZ", "message": "q2"},
                                           headers=USER_A).text))
    assert meta2["conversation_id"] == meta["conversation_id"]
    assert len(store.rows) == 1

    # …mais scopée par utilisateur : deux abonnés ne se télescopent pas.
    meta3 = meta_of(sse_frames(client.post(CHAT, json={"id": "aBc123XyZ", "message": "q"},
                                           headers=USER_B).text))
    assert meta3["conversation_id"] != meta["conversation_id"]


def test_un_utilisateur_ne_voit_pas_les_conversations_d_un_autre(client, store, fake_pipeline):
    meta = meta_of(sse_frames(client.post(CHAT, json={"message": "q"}, headers=USER_A).text))
    cid = meta["conversation_id"]

    assert client.get(f"/v1/conversations/{cid}", headers=USER_A).status_code == 200
    assert client.get(f"/v1/conversations/{cid}", headers=USER_B).status_code == 404
    assert client.delete(f"/v1/conversations/{cid}", headers=USER_B).status_code == 404
    assert client.get("/v1/conversations", headers=USER_B).json() == []
    assert client.delete(f"/v1/conversations/{cid}", headers=USER_A).status_code == 200


def test_conversation_inexistante_donne_404_dans_le_flux(client, store, fake_pipeline):
    frames = sse_frames(client.post(CHAT, json={"message": "q", "conversation_id": "inexistante"},
                                    headers=USER_A).text)
    errors = frames_of_type(frames, "error")
    assert errors and errors[0]["errorCode"] == "not_found"


# ─── Questions de suivi ───────────────────────────────────────────────────────
def test_le_second_tour_passe_par_l_agent_de_suivi(client, store, fake_pipeline, fake_followup):
    cid = meta_of(sse_frames(client.post(CHAT, json={"message": "q1"},
                                         headers=USER_A).text))["conversation_id"]
    appels_avant = len(fake_pipeline)

    frames = sse_frames(client.post(CHAT, json={"message": "q2", "conversation_id": cid},
                                    headers=USER_A).text)
    meta = meta_of(frames)
    assert meta["is_follow_up"] is True
    assert meta["escalated"] is False
    assert streamed_text(frames) == "Réponse de suivi."
    assert len(fake_pipeline) == appels_avant, "le pipeline complet n'aurait pas dû être rejoué"


def test_le_suivi_hors_sujet_declenche_le_pipeline_complet(client, store, fake_pipeline,
                                                           fake_followup):
    cid = meta_of(sse_frames(client.post(CHAT, json={"message": "q1"},
                                         headers=USER_A).text))["conversation_id"]
    fake_followup(answer_text="Hors sujet.", points_cles=[],
                  necessite_nouvelle_recherche=True, trace_id="t")
    appels_avant = len(fake_pipeline)

    frames = sse_frames(client.post(CHAT, json={"message": "tout autre sujet",
                                                "conversation_id": cid}, headers=USER_A).text)
    meta = meta_of(frames)
    assert meta["escalated"] is True
    assert len(fake_pipeline) == appels_avant + 1
    # La réponse du suivi est écartée au profit de celle du pipeline complet.
    assert "Hors sujet." not in streamed_text(frames)


def test_le_contexte_conserve_l_historique_des_tours(client, store, fake_pipeline, fake_followup):
    cid = meta_of(sse_frames(client.post(CHAT, json={"message": "q1"},
                                         headers=USER_A).text))["conversation_id"]
    client.post(CHAT, json={"message": "q2", "conversation_id": cid}, headers=USER_A)
    client.post(CHAT, json={"message": "q3", "conversation_id": cid}, headers=USER_A)

    contexte = store.rows[cid]["contexte_conversation"]
    assert contexte["question_initial"] == "q1"          # l'ancrage initial est conservé…
    assert [t["question"] for t in contexte["historique"]] == ["q1", "q2", "q3"]  # …et l'historique suit


# ─── Endpoint synchrone ───────────────────────────────────────────────────────
def test_chat_sync_rend_la_reponse_complete(client, store, fake_pipeline):
    r = client.post("/v1/chat/sync", json={"message": "q"}, headers=USER_A)
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == "## En résumé\nLe taux est de **20 %**.\n"
    assert body["points_cles"] == ["Vérifier l'ancienneté du logement"]
    assert body["sources"][0]["source_domain"] == "bofip.impots.gouv.fr"


# ─── Capacité ─────────────────────────────────────────────────────────────────
def test_au_dela_de_la_capacite_le_service_repond_429(client, store, monkeypatch):
    """Les places sont bornées : la requête en trop est refusée proprement,
    pas mise en file jusqu'au timeout du navigateur."""
    import asyncio

    import api.runner as runner
    from api.settings import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "slot_acquire_timeout_s", 0.05)
    runner._slots = asyncio.Semaphore(0)          # toutes les places occupées

    r = client.post(CHAT, json={"message": "q"}, headers=USER_A)
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "capacity_exceeded"
    assert r.headers["Retry-After"]


def test_la_place_est_liberee_apres_le_flux(client, store, fake_pipeline):
    import api.runner as runner

    avant = runner.free_slots()
    client.post(CHAT, json={"message": "q"}, headers=USER_A)
    assert runner.free_slots() == avant


# ─── Quotas ───────────────────────────────────────────────────────────────────
def test_quota_par_utilisateur(client, store, fake_pipeline, monkeypatch):
    from api.deps import get_rate_limiter, reset_rate_limiter
    from api.settings import get_settings

    reset_rate_limiter()
    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_burst_per_min", 2)
    get_rate_limiter(settings)

    assert client.post(CHAT, json={"message": "q"}, headers=USER_A).status_code == 200
    assert client.post(CHAT, json={"message": "q"}, headers=USER_A).status_code == 200
    r = client.post(CHAT, json={"message": "q"}, headers=USER_A)
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "rate_limited"
    # Le quota est par utilisateur, pas global.
    assert client.post(CHAT, json={"message": "q"}, headers=USER_B).status_code == 200
    reset_rate_limiter()


# ─── Divers ───────────────────────────────────────────────────────────────────
def test_config_expose_les_domaines_et_les_etapes(client):
    body = client.get("/v1/config", headers=USER_A).json()
    assert any(d["domain"] == "bofip.impots.gouv.fr" for d in body["domains"])
    assert len(body["steps"]) == 11
    assert body["ai_sdk_protocol"] == "v5"


def test_request_id_est_propage(client):
    r = client.get("/health", headers={"X-Request-Id": "mon-id-123"})
    assert r.headers["x-request-id"] == "mon-id-123"


def test_aucune_trace_d_execution_dans_les_reponses(client, store, monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("secret interne : /var/lib/fisca/token")

    monkeypatch.setattr("api.routes.conversations.list_conversations", _boom)
    r = client.get("/v1/conversations", headers=USER_A)
    assert r.status_code == 500
    assert "secret interne" not in r.text
    assert "Traceback" not in r.text
    assert r.json()["error"]["code"] == "internal_error"
