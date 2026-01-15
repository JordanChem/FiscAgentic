"""
Utilitaires pour le scraping
"""
from typing import List, Dict
from legal_scraper import LegalScraper


def scrapper(ranked_keep: List[Dict]) -> List[Dict]:
    """
    Pour chaque document de la liste filtrée, utilise LegalScraper pour récupérer le contenu de l'URL
    et ajoute une clé 'content' au dictionnaire.
    """
    if not ranked_keep:
        return []

    try:
        scraper = LegalScraper()
        enriched = []
        for doc in ranked_keep:
            url = doc.get("url")
            content = ""
            if url:
                try:
                    scraped = scraper.scrape_url(url)
                    # On tente d'extraire le contenu textuel principal
                    if hasattr(scraped, "content"):
                        content = scraped.content
                    elif isinstance(scraped, dict) and "content" in scraped:
                        content = scraped["content"]
                    else:
                        content = str(scraped) if scraped else ""
                except Exception:
                    content = ""
            doc_with_content = dict(doc)
            doc_with_content["content"] = content
            enriched.append(doc_with_content)
        try:
            scraper.close()
        except Exception:
            pass
        return enriched
    except ImportError:
        # Si l'import échoue, retourne la liste sans modification
        return ranked_keep
