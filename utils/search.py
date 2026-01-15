"""
Fonctions de recherche via SerpAPI
"""
import requests
from typing import List, Dict

# Domaines officiels à cibler
OFFICIAL_DOMAINS = [
    "legifrance.gouv.fr",
    "bofip.impots.gouv.fr",
    "conseil-etat.fr",
    "courdecassation.fr",
    "conseil-constitutionnel.fr",
    "assemblee-nationale.fr",
    "senat.fr",
    'fiscalonline.fr'
]


def search_official_sources(
    queries: List[str], 
    api_key: str, 
    max_results_per_query: int = 2,
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

    for query in queries:
        params = {
            "engine": "google_light",
            "q": query,
            "num": max_results_per_query,
            "api_key": api_key,
            "hl": "fr",
            "gl": "fr"
        }
        try:
            resp = requests.get(endpoint, params=params)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            print(f"Erreur lors de l'appel SerpAPI pour '{query}': {exc}")
            continue

        organic_results = data.get("organic_results", [])
        for idx, entry in enumerate(organic_results):
            domain = entry.get("link", "").split("/")[2] if "link" in entry else ""
            # Filtrer sur les domaines actifs
            if any(d in domain for d in domains_to_use):
                results.append({
                    "query": query,
                    "title": entry.get("title", ""),
                    "url": entry.get("link", ""),
                    "snippet": entry.get("snippet", ""),
                    "source_domain": domain,
                    "position": entry.get("position", idx+1)
                })

    return results
