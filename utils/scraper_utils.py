"""
Utilitaires pour le scraping avec fallback Firecrawl
"""
import os
import logging
from typing import List, Dict
from concurrent.futures import ThreadPoolExecutor
from legal_scraper import LegalScraper

logger = logging.getLogger(__name__)


def scrapper(ranked_keep: List[Dict]) -> List[Dict]:
    """
    Pour chaque document de la liste filtrée, utilise LegalScraper pour récupérer le contenu de l'URL.
    Si le scraping échoue ou retourne un contenu vide, utilise Firecrawl comme fallback.
    Ajoute une clé 'content' au dictionnaire.
    Les URLs sont scrapées en parallèle pour réduire le temps total.
    """
    if not ranked_keep:
        return []

    try:
        scraper = LegalScraper()
        firecrawl_client = None  # Lazy init pour éviter l'import si non nécessaire

        def _scrape_single(doc: Dict) -> Dict:
            """Scrape une URL unique avec fallback Firecrawl."""
            nonlocal firecrawl_client
            url = doc.get("url")
            content = ""
            source_method = None

            if url:
                # 1. Essayer d'abord avec LegalScraper
                try:
                    scraped = scraper.scrape_url(url)
                    if scraped:
                        if hasattr(scraped, "content") and scraped.content:
                            content = scraped.content
                            source_method = "LegalScraper"
                        elif isinstance(scraped, dict) and scraped.get("content"):
                            content = scraped["content"]
                            source_method = "LegalScraper"
                except Exception as e:
                    logger.warning(f"LegalScraper failed for {url}: {e}")

                # 2. Fallback Firecrawl si contenu vide
                if not content or 'requires JS' in content:
                    try:
                        if firecrawl_client is None:
                            from firecrawl import Firecrawl
                            api_key = os.getenv("FIRECRAWL_API_KEY")
                            if api_key:
                                firecrawl_client = Firecrawl(api_key=api_key)
                            else:
                                logger.warning("FIRECRAWL_API_KEY not set, skipping fallback")
                            
                        if firecrawl_client:
                            # Retirer cid=... de l'URL si présent et ;jsessionid=... aussi
                            import re
                            cleaned_url = url
                            # Enlever les paramètres jsessionid dans le path (ex : ;jsessionid=... )
                            cleaned_url = re.sub(r';jsessionid=[^/?&#]+', '', cleaned_url, flags=re.IGNORECASE)
                            # Retirer le paramètre cid=... dans la query string
                            cleaned_url = re.sub(r'([&?])cid=[^&]+', lambda m: '?' if m.group(1) == '?' else '', cleaned_url)
                            # Supprimer un ? en fin d'url restant 
                            cleaned_url = re.sub(r'\?$', '', cleaned_url)
                            result = firecrawl_client.scrape(cleaned_url)
                            if result and getattr(result, 'markdown'):
                                content = getattr(result, 'markdown')
                                source_method = "Firecrawl"
                                logger.info(f"Firecrawl fallback success for {cleaned_url}")
                    except Exception as e:
                        logger.warning(f"Firecrawl fallback failed for {url}: {e}")

                if source_method:
                    logger.debug(f"Scraped {url} using {source_method}")

            doc_with_content = dict(doc)
            doc_with_content["content"] = content
            return doc_with_content

        # Scraping parallèle (max 5 threads pour respecter les rate limits)
        with ThreadPoolExecutor(max_workers=min(5, max(1, len(ranked_keep)))) as executor:
            enriched = list(executor.map(_scrape_single, ranked_keep))

        try:
            scraper.close()
        except Exception:
            pass

        return enriched

    except ImportError as e:
        logger.error(f"Import error: {e}")
        return ranked_keep
