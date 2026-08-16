"""
Budget global de l'étape de scraping.

Non-régression d'une panne observée en recette : le service est resté bloqué
plus de trente minutes dans `scrapper()`. Les timeouts par appel n'y suffisent
pas — `requests` est bien borné à 30 s, mais l'extraction trafilatura d'une page
volumineuse est du calcul pur, sans aucune limite. L'étape entière doit donc
être bornée, et rendre des résultats partiels plutôt que de bloquer la requête.
"""
from __future__ import annotations

import time

import pytest

import utils.scraper_utils as scraper_utils


class _FakeScraper:
    """Scraper factice : les URL contenant « lent » ne rendent jamais la main."""

    def scrape_url(self, url):
        if "lent" in url:
            time.sleep(60)

        class _Result:
            content = f"contenu de {url}"

        return _Result()

    def close(self):
        pass


@pytest.fixture
def scraper_borne(monkeypatch):
    monkeypatch.setattr(scraper_utils, "SCRAPE_TOTAL_TIMEOUT_S", 2.0)
    monkeypatch.setattr(scraper_utils, "LegalScraper", lambda *a, **k: _FakeScraper())


DOCS = [
    {"url": "https://exemple.fr/rapide-1", "title": "A"},
    {"url": "https://exemple.fr/lent-2", "title": "B"},
    {"url": "https://exemple.fr/rapide-3", "title": "C"},
    {"url": "https://exemple.fr/lent-4", "title": "D"},
]


def test_le_budget_global_est_respecte(scraper_borne):
    start = time.time()
    scraper_utils.scrapper(DOCS)
    assert time.time() - start < 8, "l'étape a dépassé son budget"


def test_les_documents_lents_sont_rendus_sans_contenu(scraper_borne):
    result = scraper_utils.scrapper(DOCS)
    assert len(result) == len(DOCS), "tous les documents doivent être rendus"
    assert [d["url"] for d in result] == [d["url"] for d in DOCS], "ordre préservé"

    avec_contenu = {d["url"] for d in result if d["content"]}
    assert avec_contenu == {"https://exemple.fr/rapide-1", "https://exemple.fr/rapide-3"}
    # Les métadonnées survivent : le rédactionnel sait exploiter titre + extrait
    # même quand le contenu manque.
    assert all("title" in d for d in result)


def test_le_contenu_prerempli_est_conserve(scraper_borne):
    """JusticeLibre et FiscalOnline fournissent déjà `content` : ne pas le perdre."""
    docs = [{"url": "https://exemple.fr/lent-9", "content": "déjà récupéré"}]
    result = scraper_utils.scrapper(docs)
    assert result[0]["content"] == "déjà récupéré"


def test_liste_vide(scraper_borne):
    assert scraper_utils.scrapper([]) == []
