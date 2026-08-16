"""
Utilitaires pour le scraping avec fallback Firecrawl
"""
import os
import logging
from typing import List, Dict
from concurrent.futures import (
    ThreadPoolExecutor, TimeoutError as FuturesTimeout, as_completed,
)
from legal_scraper import LegalScraper

logger = logging.getLogger(__name__)

# Plafond de threads de scraping (rate limits des sites officiels + budget threads
# global du process quand plusieurs pipelines tournent en parallèle sous FastAPI).
SCRAPE_MAX_WORKERS = int(os.getenv("SCRAPE_MAX_WORKERS", "5"))

# Le SDK Firecrawl n'expose pas de timeout fiable : on borne l'appel côté appelant.
FIRECRAWL_TIMEOUT_S = float(os.getenv("FIRECRAWL_TIMEOUT_S", "45"))

# Budget de l'étape de scraping dans son ensemble. Indispensable : l'extraction
# trafilatura n'a aucun timeout (c'est du calcul, pas du réseau) et peut bloquer
# indéfiniment sur une page volumineuse.
SCRAPE_TOTAL_TIMEOUT_S = float(os.getenv("SCRAPE_TOTAL_TIMEOUT_S", "120"))

# Pool dédié aux appels Firecrawl : permet d'abandonner un appel qui ne rend pas
# la main (le thread se termine de lui-même) sans bloquer le thread de scraping.
_firecrawl_pool = ThreadPoolExecutor(max_workers=SCRAPE_MAX_WORKERS,
                                     thread_name_prefix="firecrawl")


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

            # Contenu pré-rempli par JusticeLibre : pas de scraping nécessaire
            if doc.get("content"):
                return doc

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
                            from firecrawl import V1FirecrawlApp
                            api_key = os.getenv("FIRECRAWL_API_KEY")
                            if api_key:
                                firecrawl_client = V1FirecrawlApp(api_key=api_key)
                            else:
                                logger.warning("FIRECRAWL_API_KEY not set, skipping fallback")

                        if firecrawl_client:
                            import re
                            cleaned_url = url
                            cleaned_url = re.sub(r';jsessionid=[^/?&#]+', '', cleaned_url, flags=re.IGNORECASE)
                            cleaned_url = re.sub(r'([&?])cid=[^&]+', lambda m: '?' if m.group(1) == '?' else '', cleaned_url)
                            cleaned_url = re.sub(r'\?$', '', cleaned_url)
                            future = _firecrawl_pool.submit(
                                firecrawl_client.scrape_url, cleaned_url, proxy='stealth'
                            )
                            result = future.result(timeout=FIRECRAWL_TIMEOUT_S)
                            if result and result.markdown:
                                content = result.markdown
                                source_method = "Firecrawl"
                                logger.info(f"Firecrawl fallback success for {cleaned_url}")
                    except FuturesTimeout:
                        logger.warning(
                            "Firecrawl fallback timeout (%ss) for %s", FIRECRAWL_TIMEOUT_S, url
                        )
                    except Exception as e:
                        logger.warning(f"Firecrawl fallback failed for {url}: {e}")

                if source_method:
                    logger.debug(f"Scraped {url} using {source_method}")

            doc_with_content = dict(doc)
            doc_with_content["content"] = content
            return doc_with_content

        # Scraping parallèle, borné dans le temps.
        #
        # Les timeouts par appel ne suffisent pas : `requests` est bien borné à
        # 30 s, mais l'extraction trafilatura d'une page BOFiP volumineuse est du
        # calcul pur, sans aucune limite. Un run de recette est resté bloqué là
        # plus de trente minutes. On borne donc l'étape entière et on rédige avec
        # ce qui a été récupéré : le prompt rédactionnel sait déjà exploiter une
        # source dont le contenu manque (titre + extrait).
        executor = ThreadPoolExecutor(
            max_workers=min(SCRAPE_MAX_WORKERS, max(1, len(ranked_keep))),
            thread_name_prefix="scraper",
        )
        futures = {executor.submit(_scrape_single, doc): i
                   for i, doc in enumerate(ranked_keep)}
        # Défaut : documents inchangés, avec un `content` vide.
        enriched = [{**doc, "content": doc.get("content", "")} for doc in ranked_keep]
        done = 0
        try:
            for future in as_completed(futures, timeout=SCRAPE_TOTAL_TIMEOUT_S):
                index = futures[future]
                try:
                    enriched[index] = future.result()
                    done += 1
                except Exception as exc:
                    logger.warning("Scraping en échec pour %s : %s",
                                   ranked_keep[index].get("url"), exc)
        except FuturesTimeout:
            logger.warning(
                "Scraping — budget global de %ss dépassé : %d/%d documents récupérés, "
                "rédaction avec ce qui est disponible",
                SCRAPE_TOTAL_TIMEOUT_S, done, len(ranked_keep),
            )
        finally:
            # wait=False : ne pas attendre les workers en souffrance, c'est
            # précisément ce dont on cherche à se protéger.
            executor.shutdown(wait=False, cancel_futures=True)
            try:
                scraper.close()
            except Exception:
                pass

        return enriched

    except ImportError as e:
        logger.error(f"Import error: {e}")
        return ranked_keep
