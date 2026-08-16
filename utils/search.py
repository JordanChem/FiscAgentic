"""
Fonctions de recherche via SerpAPI, avec fallback JusticeLibre MCP pour la jurisprudence.
"""
import logging
import os
import requests
from urllib.parse import urlparse
from typing import List, Dict
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

# Taille du pool de requêtes SerpAPI. Sous FastAPI, plusieurs pipelines tournent
# en parallèle : ce plafond borne le nombre total de threads du process.
SEARCH_MAX_WORKERS = int(os.getenv("SEARCH_MAX_WORKERS", "8"))

# (connect, read) — sans timeout, une socket SerpAPI bloquée immobilise
# un worker (et donc un slot de pipeline) indéfiniment.
SERPAPI_TIMEOUT = (
    float(os.getenv("SERPAPI_CONNECT_TIMEOUT", "5")),
    float(os.getenv("SERPAPI_READ_TIMEOUT", "20")),
)

# Domaines couverts par JusticeLibre (retirés de SerpAPI quand JL est actif)
JL_COVERED_DOMAINS = {"conseil-etat.fr", "courdecassation.fr", "europa.eu"}

# Domaines officiels à cibler
OFFICIAL_DOMAINS = [
    "legifrance.gouv.fr",
    "bofip.impots.gouv.fr",
    "conseil-etat.fr",
    "courdecassation.fr",
    "conseil-constitutionnel.fr",
    "assemblee-nationale.fr",
    "senat.fr",
    "fiscalonline.fr",
    "europa.eu",  # CJUE - Cour de Justice de l'Union Européenne
    "opendata.justice-administrative.fr",  # CAA + TA (fallback SerpAPI quand JL down)
]


def search_official_sources(
    queries: List[str], 
    api_key: str, 
    max_results_per_query: int = 3,
    active_domains: List[str] = None
) -> List[Dict]:
    """
    Recherche sur SerpAPI, parse les résultats et retourne une liste structurée de résultats officiels.

    Args:
        queries: Liste de requêtes à lancer.
        api_key: Clé API SerpAPI.
        max_results_per_query: Nombre maximum de résultats à extraire par requête.
        active_domains: Liste des domaines à utiliser pour le filtrage. Si None, utilise tous les domaines OFFICIAL_DOMAINS.

    Returns:
        Liste de dictionnaires structurés avec titre, URL, snippet, domaine, position.
    """
    endpoint = "https://serpapi.com/search"
    results = []
    
    # Utiliser les domaines actifs ou tous les domaines par défaut
    domains_to_use = active_domains if active_domains is not None else OFFICIAL_DOMAINS
    
    # Si aucun domaine n'est activé, retourner une liste vide
    if not domains_to_use:
        return results

    def _search_single_query(query: str) -> List[Dict]:
        """Exécute une requête SerpAPI et retourne les résultats filtrés."""
        # On retire la description après ' — ' pour aider le matching SerpAPI
        clean_query = query.split(' — ')[0]
        num_results = max_results_per_query
        if "europa.eu" in query:
            num_results = 5
        
        clean_query += " -filetype:pdf"
        params = {
            "engine": "google_light",
            "q": clean_query,
            "num": num_results,
            "api_key": api_key,
            "hl": "fr",
            "gl": "fr"
        }
        query_results = []
        try:
            resp = requests.get(endpoint, params=params, timeout=SERPAPI_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning(f"Erreur lors de l'appel SerpAPI pour '{query}': {exc}")
            return query_results

        organic_results = data.get("organic_results", [])
        for idx, entry in enumerate(organic_results):
            link = entry.get("link", "")
            # Extraction robuste du domaine via urlparse (évite les bugs sur ports, chemins inhabituels)
            parsed = urlparse(link)
            domain = parsed.netloc.lower().lstrip("www.")
            title = entry.get("title", "")
            # Matching exact par suffixe de domaine (évite "fake-bofip.impots.gouv.fr")
            if any(domain == d or domain.endswith("." + d) for d in domains_to_use) and 'pdf' not in title.lower():
                query_results.append({
                    "query": query,
                    "title": title,
                    "url": link,
                    "snippet": entry.get("snippet", ""),
                    "source_domain": domain,
                    "position": entry.get("position", idx + 1)
                })
        return query_results

    # Exécution parallèle des requêtes
    with ThreadPoolExecutor(max_workers=min(SEARCH_MAX_WORKERS, max(1, len(queries)))) as executor:
        futures = {executor.submit(_search_single_query, q): q for q in queries}
        for future in as_completed(futures):
            results.extend(future.result())

    return results


def search_with_fallback(
    queries: List[str],
    serpapi_key: str,
    max_results_per_query: int = 3,
    active_domains: List[str] = None,
    use_justicelibre: bool = True,
    analyst_json: dict = None,
) -> List[Dict]:
    """
    Point d'entrée principal pour la recherche de sources.

    - Si use_justicelibre=True et JusticeLibre est disponible :
        • JL utilise analyst_json (axes + concepts T0) pour CE / CAA / TA / Cass
        • SerpAPI couvre le reste (BOFiP, Légifrance, Assemblée, Sénat, fiscalonline…)
    - Si JL est indisponible (down / timeout) : fallback automatique vers SerpAPI seul.
    - Si use_justicelibre=False ou analyst_json absent : SerpAPI seul.
    """
    jl_results: List[Dict] = []
    serp_domains = list(active_domains) if active_domains is not None else list(OFFICIAL_DOMAINS)

    if use_justicelibre and analyst_json:
        try:
            from utils.justicelibre import MCPClient, is_jl_available, search_justicelibre
            client = MCPClient(url="https://justicelibre.org/mcp")
            client.initialize()
            if is_jl_available(client):
                jl_results = search_justicelibre(analyst_json, client)
                # Retirer les domaines couverts par JL du scope SerpAPI
                serp_domains = [d for d in serp_domains if d not in JL_COVERED_DOMAINS]
                logger.info(
                    "[search] JusticeLibre OK — %d résultats | SerpAPI sur %d domaines",
                    len(jl_results), len(serp_domains),
                )
            else:
                logger.warning("[search] JusticeLibre indisponible — fallback SerpAPI complet")
        except Exception as exc:
            logger.warning("[search] JusticeLibre erreur (%s) — fallback SerpAPI complet", exc)
    elif use_justicelibre and not analyst_json:
        logger.warning("[search] JusticeLibre activé mais analyst_json absent — SerpAPI seul")

    serp_results: List[Dict] = []
    if serp_domains:
        serp_results = search_official_sources(
            queries, serpapi_key,
            max_results_per_query=max_results_per_query,
            active_domains=serp_domains,
        )

    return jl_results + serp_results
