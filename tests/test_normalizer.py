"""
Tests du normaliseur du flux rédactionnel.

Le contrat : quel que soit le découpage en chunks, le texte reconstitué doit
être **identique** à `json.loads(raw)["reponse_redigee"]`. C'est ce qui garantit
qu'aucun caractère (accent, retour à la ligne, guillemet échappé) n'est perdu ou
dupliqué entre deux chunks.
"""
import json

import pytest

from pipeline.normalizer import JsonStringFieldExtractor, RedactionNormalizer

# Markdown réaliste : accents, guillemets, retours ligne, backslash, listes.
MARKDOWN = (
    "## En résumé\n\n"
    "Le régime « Dutreil » permet un abattement de 75 %.\n\n"
    "### Analyse technique\n"
    "- Assiette : 1 000 000 €\n"
    "- Taux applicable : 20 %\n"
    '- Voir l\'article 787 B du CGI (dit "pacte Dutreil")\n\n'
    "Chemin d'exemple : C:\\Users\\test\\doc.pdf\n\n"
    "Retrouvez plus d'informations sur ce sujet ici : fiscalonline.com"
)
POINTS = ["Délai de conservation de 4 ans", "Risque d'abus de droit (L64 LPF)"]

PAYLOAD = {
    "question": "Quel est le régime du pacte Dutreil ?",
    "reponse_redigee": MARKDOWN,
    "points_cles": POINTS,
}

RAW_BARE = json.dumps(PAYLOAD, ensure_ascii=False)
RAW_ASCII = json.dumps(PAYLOAD, ensure_ascii=True)          # accents en \uXXXX
RAW_FENCED = f"```json\n{RAW_BARE}\n```"
RAW_FENCED_ASCII = f"```json\n{RAW_ASCII}\n```"

CHUNK_SIZES = [1, 2, 3, 5, 7, 13, 64, 1000, 10_000]


def _chunks(text: str, size: int):
    return [text[i:i + size] for i in range(0, len(text), size)]


def _run(raw: str, size: int) -> RedactionNormalizer:
    norm = RedactionNormalizer()
    for chunk in _chunks(raw, size):
        norm.feed(chunk)
    return norm


# ─── Extraction fidèle, tous découpages ──────────────────────────────────────
@pytest.mark.parametrize("size", CHUNK_SIZES)
@pytest.mark.parametrize(
    "raw", [RAW_BARE, RAW_ASCII, RAW_FENCED, RAW_FENCED_ASCII],
    ids=["bare", "ascii-escapes", "fenced", "fenced-ascii"],
)
def test_reconstitue_le_markdown_a_l_identique(raw, size):
    norm = _run(raw, size)
    text, points = norm.finish()
    assert text == MARKDOWN
    assert points == POINTS
    assert norm.mode == RedactionNormalizer.MODE_JSON


@pytest.mark.parametrize("size", CHUNK_SIZES)
def test_les_deltas_concatenes_egalent_le_texte_final(size):
    """Ce qui est streamé au client doit être exactement ce qu'on stocke."""
    norm = RedactionNormalizer()
    streamed = "".join(norm.feed(c) for c in _chunks(RAW_BARE, size))
    text, _ = norm.finish()
    assert streamed == text == MARKDOWN


def test_aucun_json_ne_fuit_dans_les_deltas():
    norm = RedactionNormalizer()
    streamed = "".join(norm.feed(c) for c in _chunks(RAW_BARE, 3))
    assert '"reponse_redigee"' not in streamed
    assert '"points_cles"' not in streamed
    assert "\\n" not in streamed          # les \n sont décodés, pas littéraux


# ─── Cas limites ─────────────────────────────────────────────────────────────
def test_echappement_unicode_coupe_entre_chunks():
    raw = json.dumps({"reponse_redigee": "caf\u00e9"}, ensure_ascii=True)
    # "caf\u00e9" → on coupe au milieu de la séquence \u00e9
    for cut in range(len(raw)):
        norm = RedactionNormalizer()
        norm.feed(raw[:cut])
        norm.feed(raw[cut:])
        assert norm.finish()[0] == "café", f"coupure à l'offset {cut}"


def test_cle_coupee_entre_chunks():
    raw = RAW_BARE
    idx = raw.index('"reponse_redigee"')
    for cut in range(idx, idx + len('"reponse_redigee"') + 3):
        norm = RedactionNormalizer()
        norm.feed(raw[:cut])
        norm.feed(raw[cut:])
        assert norm.finish()[0] == MARKDOWN, f"coupure à l'offset {cut}"


def test_guillemet_echappe_en_fin_de_chunk():
    raw = json.dumps({"reponse_redigee": 'dit "pacte" ici'}, ensure_ascii=False)
    idx = raw.index('\\"')
    norm = RedactionNormalizer()
    norm.feed(raw[:idx + 1])          # coupe entre le backslash et le guillemet
    norm.feed(raw[idx + 1:])
    assert norm.finish()[0] == 'dit "pacte" ici'


def test_json_tronque_rend_ce_qui_a_ete_recu():
    """Flux coupé en plein milieu : on garde le markdown déjà extrait."""
    raw = RAW_BARE[: RAW_BARE.index("### Analyse")]
    norm = RedactionNormalizer()
    for chunk in _chunks(raw, 7):
        norm.feed(chunk)
    text, _ = norm.finish()
    assert text.startswith("## En résumé")
    assert "###" not in text


# ─── Mode markdown (filet de sécurité) ───────────────────────────────────────
def test_passthrough_si_le_modele_sort_du_format_json():
    raw = MARKDOWN
    norm = RedactionNormalizer()
    streamed = "".join(norm.feed(c) for c in _chunks(raw, 11))
    text, points = norm.finish()
    assert norm.mode == RedactionNormalizer.MODE_MARKDOWN
    assert streamed == text == MARKDOWN
    assert points == []


def test_passthrough_extrait_le_bloc_points_cles():
    raw = MARKDOWN + "\n<points_cles>\n- Alerte 1\n- Alerte 2\n</points_cles>"
    norm = RedactionNormalizer()
    for chunk in _chunks(raw, 9):
        norm.feed(chunk)
    _, points = norm.finish()
    assert points == ["Alerte 1", "Alerte 2"]


def test_reponse_tres_courte_sous_la_fenetre_de_detection():
    raw = json.dumps({"reponse_redigee": "ok"}, ensure_ascii=False)
    norm = RedactionNormalizer()
    norm.feed(raw)
    assert norm.finish()[0] == "ok"


def test_message_de_repli_sans_sources_passe_en_markdown():
    """agent_redactionnel_stream yield une phrase brute si enriched_docs est vide."""
    msg = ("Je n'ai trouvé aucune source pertinente pour répondre à votre question "
           "fiscale. Merci de reformuler ou de préciser votre demande.")
    norm = RedactionNormalizer()
    streamed = norm.feed(msg)
    text, _ = norm.finish()
    assert streamed == text == msg


# ─── Extracteur bas niveau ───────────────────────────────────────────────────
def test_extracteur_ignore_la_cle_apparaissant_dans_une_valeur():
    raw = json.dumps(
        {"question": 'le champ "reponse_redigee" est-il rempli ?',
         "reponse_redigee": "oui"},
        ensure_ascii=False,
    )
    ext = JsonStringFieldExtractor()
    for chunk in _chunks(raw, 4):
        ext.feed(chunk)
    assert ext.text == "oui"


def test_extracteur_signale_la_fin_de_valeur():
    ext = JsonStringFieldExtractor()
    ext.feed(RAW_BARE)
    assert ext.done
    assert '"points_cles"' in ext.tail
