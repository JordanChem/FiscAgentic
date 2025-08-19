# -*- coding: utf-8 -*-
"""
Pipeline agentique (LLM-only + DuckDuckGo) pour récupérer et citer des sources fiscales FR :
- Loi (CGI/LPF/LF/LFR), Doctrine (BOFiP/RM/QE), Jurisprudence (CE/CC/Cass/CAA), FiscalOnline.
- Affiche (print) la sortie de chaque agent.
Version allégée (pas de readability-lxml).
"""

import os
import re
import sys
import json
import time
import random
import logging
import html
import tldextract
import requests
import trafilatura
from io import BytesIO
from bs4 import BeautifulSoup
from datetime import datetime
from dateutil import parser as dateparser
from pdfminer.high_level import extract_text as pdf_extract_text
import urllib.parse
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import re, urllib.parse
from openai import OpenAI
from dotenv import load_dotenv

# Charger les variables définies dans .env
load_dotenv()

# ---------------- Config ----------------
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
assert OPENAI_API_KEY, "Veuillez définir OPENAI_API_KEY"

GOOGLE_API_KEY =  os.getenv("GOOGLE_API_KEY")
GOOGLE_CSE_ID  = os.getenv("GOOGLE_CSE_ID")


PRINT_WIDTH = 100
TIMEOUT = 25
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Connection": "keep-alive",
}
DOMAIN_WHITELIST = [
    "legifrance.gouv.fr",
    "impots.gouv.fr",
    "bofip.impots.gouv.fr",
    "conseil-etat.fr",
    "courdecassation.fr",
    "conseil-constitutionnel.fr",
    "fiscalonline.com",
    "vie-publique.fr",
    "assemblee-nationale.fr",
    "senat.fr",
]

# Domain authority weights for ranking (higher = more authoritative)
# Domain authority (resserré : 0.65–0.90)

AUTH_MIN, AUTH_MAX = 0.70, 1   # bornes du dict resserré
FAM_MIN,  FAM_MAX  = 0.70, 1

DOMAIN_AUTHORITY_WEIGHTS = {
    "legifrance.gouv.fr":        1,
    "bofip.impots.gouv.fr":      1,
    "conseil-etat.fr":           1,
    "courdecassation.fr":        1,
    "conseil-constitutionnel.fr":1,
    "assemblee-nationale.fr":    1,
    "senat.fr":                  1,
    "vie-publique.fr":           1,
    "fiscalonline.com":          1
}

# Family priority (resserré : 0.70–0.82)
FAMILY_WEIGHTS = {
    "loi":         1,
    "doctrine":    0.95,
    "jurisprudence":0.90,
    "travaux_parlementaires":0.8,
    "fiscalonline":0.8,
}


# --------------- OpenAI client ---------------
client = OpenAI(api_key=OPENAI_API_KEY)

def llm_complete(system_prompt: str, user_prompt: str, temperature: float = 0.1, max_tokens: int = 1200):
    # On renforce l’instruction "JSON only"
    sys2 = system_prompt + "\n\nCONTRAINTE: Réponds exclusivement en JSON valide, sans texte avant/après."
    resp = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[{"role":"system","content":sys2},{"role":"user","content":user_prompt}],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content.strip()

# --------------- Utils ----------------
def println(title, obj=None, sep="="):
    print("\n" + title)
    print(sep * min(PRINT_WIDTH, max(20, len(title))))
    if obj is not None:
        if isinstance(obj, (dict, list)):
            print(json.dumps(obj, ensure_ascii=False, indent=2))
        else:
            print(obj)

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\}|$begin:math:display$.*?$end:math:display$)\s*```", re.S | re.I)

def parse_json_robuste(text: str):
    """
    1) Essaie direct json.loads
    2) Cherche un bloc ```json ... ```
    3) Cherche la première structure { ... } ou [ ... ] balancée
    """
    if text is None:
        raise ValueError("Texte vide")
    # 1) tentative directe
    try:
        return json.loads(text)
    except Exception:
        pass
    # 2) bloc ```json ... ```
    m = _JSON_BLOCK_RE.search(text)
    if m:
        inner = m.group(1)
        try:
            return json.loads(inner)
        except Exception:
            pass
    # 3) extraction heuristique de la première structure JSON
    start = text.find("{")
    sq_start = text.find("[")
    if start == -1 and sq_start == -1:
        raise ValueError("Aucun JSON détecté")
    if start == -1 or (sq_start != -1 and sq_start < start):
        # commence par liste
        start = sq_start
        end_char = "]"
    else:
        end_char = "}"
    # on tente d'équilibrer les crochets/accolades
    depth = 0
    end = None
    for i in range(start, len(text)):
        c = text[i]
        if c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end:
        candidate = text[start:end]
        try:
            return json.loads(candidate)
        except Exception:
            pass
    # Dernier recours: nettoyage grossier des backticks et relance
    cleaned = text.replace("```json", "").replace("```", "").strip()
    return json.loads(cleaned)

def domain_of(url: str) -> str:
    try:
        ext = tldextract.extract(url)
        dom = ".".join([p for p in [ext.domain, ext.suffix] if p])
        if "legifrance" in dom:
            return "legifrance.gouv.fr"
        if "bofip" in dom:
            return "bofip.impots.gouv.fr"
        
        if "courdecassation" in dom:
            return "courdecassation.fr"
        return dom
    except Exception:
        return ""

def is_whitelisted(url: str) -> bool:
    d = domain_of(url)
    return any(d.endswith(w) for w in DOMAIN_WHITELIST)

def parse_date_guess(s: str):
    try:
        return dateparser.parse(s, dayfirst=True)
    except Exception:
        return None

def _session_with_retries(total=3, backoff=0.6):
    s = requests.Session()
    retry = Retry(total=total, connect=total, read=total, status_forcelist=[429,500,502,503,504],
                  allowed_methods=["GET","POST"], backoff_factor=backoff)
    s.headers.update(HEADERS)
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://",  HTTPAdapter(max_retries=retry))
    return s

SESSION = _session_with_retries()

def fetch_url(url: str, timeout=TIMEOUT, extra_headers=None):
    h = {**HEADERS, **(extra_headers or {})}
    r = SESSION.get(url, headers=h, timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    return r

def extract_text_from_response(resp: requests.Response):
    """Extraction simple avec trafilatura, sinon BeautifulSoup."""
    ctype = resp.headers.get("Content-Type","").lower()
    content = resp.content
    if "pdf" in ctype or resp.url.lower().endswith(".pdf"):
        try:
            return pdf_extract_text(BytesIO(content))
        except Exception:
            return ""
    try:
        txt = trafilatura.extract(content, include_comments=False, favor_recall=True)
        if txt:
            return txt
        soup = BeautifulSoup(content, "lxml")
        return soup.get_text(separator="\n")
    except Exception:
        return resp.text


def _decode_ddg_href(href: str) -> str:
    if not href:
        return href
    # /l/?uddg=...
    if href.startswith("/l/?"):
        q = urllib.parse.urlparse(href).query
        params = urllib.parse.parse_qs(q)
        if "uddg" in params and params["uddg"]:
            return urllib.parse.unquote(params["uddg"][0])
    # https://duckduckgo.com/l/?uddg=...
    try:
        urlp = urllib.parse.urlparse(href)
        if "duckduckgo.com" in (urlp.netloc or "") and urlp.path.startswith("/l/"):
            params = urllib.parse.parse_qs(urlp.query)
            if "uddg" in params and params["uddg"]:
                return urllib.parse.unquote(params["uddg"][0])
    except Exception:
        pass
    return href

def ddg_search(query: str, max_results: int = 4) -> list[dict]:
    endpoints = [
        ("POST", "https://duckduckgo.com/html", {"q": query}),
        ("GET",  "https://html.duckduckgo.com/html", {"q": query}),
        ("GET",  "https://lite.duckduckgo.com/lite", {"q": query}),
    ]
    s = _session_with_retries()
    for method, url, params in endpoints:
        try:
            resp = s.post(url, data=params, timeout=TIMEOUT) if method == "POST" else s.get(url, params=params, timeout=TIMEOUT)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")
            results = []

            anchors = soup.select("a.result__a")
            snippets = soup.select(".result__snippet")
            if anchors:
                for i, a in enumerate(anchors):
                    href = _decode_ddg_href(a.get("href"))
                    title = a.get_text(" ", strip=True)
                    if href and title:
                        item = {"url": href, "title": html.unescape(title)}
                        if i < len(snippets):
                            item["snippet"] = snippets[i].get_text(" ", strip=True)
                        results.append(item)
            if not results:
                for a in soup.find_all("a"):
                    href = _decode_ddg_href(a.get("href"))
                    title = a.get_text(" ", strip=True)
                    if href and title and href.startswith("http"):
                        results.append({"url": href, "title": title})

            clean = []
            for r in results:
                if r["url"].startswith("http"):
                    clean.append(r)
                if len(clean) >= max_results:
                    break
            if clean:
                return clean
        except Exception as e:
            logging.error(f"DDG error on {url}: {e}")
            time.sleep(0.8 + random.random()*0.6)
    logging.error("All DuckDuckGo endpoints failed or returned no usable links.")
    return []

def google_cse_search(query: str, max_results: int = 4) -> list[dict]:
    if not (GOOGLE_API_KEY and GOOGLE_CSE_ID):
        return []
    try:
        params = {
            "key": GOOGLE_API_KEY,
            "cx": GOOGLE_CSE_ID,
            "q": query,
            "num": min(max_results, 10),
            "safe": "off",
        }
        r = requests.get("https://www.googleapis.com/customsearch/v1", params=params, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        out = []
        for it in (data.get("items") or [])[:max_results]:
            url = it.get("link")
            title = it.get("title")
            snippet = it.get("snippet") or ""
            if url and title:
                out.append({"url": url, "title": title, "snippet": snippet})
        return out
    except Exception as e:
        logging.error(f"Google CSE error: {e}")
        return []
    

def search_web(query: str, max_results: int = 4) -> list[dict]:
    # 1) Google CSE prioritaire (fiable, filtrable par domaines)
    res = google_cse_search(query, max_results=max_results)
    if res:
        return res
    # 2) Fallback DuckDuckGo HTML si CSE indispo/bloqué
    return ddg_search(query, max_results=max_results)


# --- Ajoute cette fonction utilitaire quelque part au-dessus des agents ---
def normalize_queries(raw) -> list[dict]:
    """
    Normalise les sorties possibles du LLM en une liste de {family, q}.
    Accepte:
      - liste de dicts: [{"family":"doctrine","q":"..."}]
      - liste de strings: ["site:bofip...","..."]
      - dict par familles: {"loi":[...], "doctrine":[...], "jurisprudence":[...], "travaux_parlementaires":[...], "fiscalonline":[...]}
    """
    out = []
    families = {"loi","doctrine","jurisprudence","travaux_parlementaires", "fiscalonline"}
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                fam = item.get("family", "misc")
                q = item.get("q") or item.get("query") or ""
                if q.strip():
                    out.append({"family": fam if fam in families else "misc", "q": q.strip()})
            elif isinstance(item, str):
                if item.strip():
                    out.append({"family": "misc", "q": item.strip()})
    elif isinstance(raw, dict):
        for fam, lst in raw.items():
            mapped_fam = fam if fam in families else "misc"
            if isinstance(lst, list):
                for s in lst:
                    if isinstance(s, str) and s.strip():
                        out.append({"family": mapped_fam, "q": s.strip()})
                    elif isinstance(s, dict):
                        q = s.get("q") or s.get("query")
                        if q and q.strip():
                            out.append({"family": mapped_fam, "q": q.strip()})
    return out

# --------------- Agents ----------------
def agent_A_clarify(user_query: str) -> dict:
    system = (
        "Tu es fiscaliste senior en France. Reformule la question en un BRIEF JSON : "
        "{issue, scope:{impot, fait_generateur, periode, population}, key_terms[], exclusions[], "
        "doc_types:[CGI, BOFiP, Jurisprudence, FiscalOnline, vie-publique.fr, assemblee-nationale.fr, senat.fr, conseil-constitutionnel.fr, conseil-etat.fr, courdecassation.fr], time_bounds:{from,to}}. "
        "Prends en compte ces sources : legifrance.gouv.fr, bofip.impots.gouv.fr, conseil-etat.fr, courdecassation.fr, conseil-constitutionnel.fr, fiscalonline.com, vie-publique.fr, assemblee-nationale.fr, senat.fr."
    )

    
    prompt = f"Question: {user_query}"
    out = llm_complete(system, prompt)
    try:
        return parse_json_robuste(out)
    except Exception:
        return {"issue": user_query, "raw": out}

def agent_B_plan(brief: dict, gaps: dict | None = None) -> dict:
    system = (
        "Transforme le BRIEF en plan de recherche à haut rappel. "
        "Sortie JSON: {queries:{loi[], doctrine[], jurisprudence[], travaux_parlementaires[], fiscalonline[]}, "
        "must_have_terms[], time_filters:{after}, notes}. "
        "Prends en compte ces sources : legifrance.gouv.fr, bofip.impots.gouv.fr, conseil-etat.fr, courdecassation.fr, conseil-constitutionnel.fr, fiscalonline.com, vie-publique.fr, assemblee-nationale.fr, senat.fr. "
    )

    system += (
        "Inclure alias d'articles et opérateurs site:/intitle:/filetype:. "
        "Utilise STRICTEMENT ces domaines pour site: : "
        "legifrance.gouv.fr, bofip.impots.gouv.fr, conseil-etat.fr, courdecassation.fr, conseil-constitutionnel.fr, fiscalonline.com, vie-publique.fr, assemblee-nationale.fr, senat.fr. "
        "N'utilise pas d'autres domaines ressemblants (ex: 'bo-fip.fr' est interdit)."
    )

    system = (
    "Transforme le BRIEF en plan de recherche à haut rappel.\n"
    "Sortie JSON STRICTE au format : "
    "{queries:{loi[], doctrine[], jurisprudence[], travaux_parlementaires[], fiscalonline[]}, "
    "must_have_terms[], time_filters:{after}, notes}.\n\n"
    "RÈGLES :\n"
    "- Utilise STRICTEMENT ces domaines avec site: : "
    f"{DOMAIN_WHITELIST}.\n"
    "- Chaque requête doit COMBINER :\n"
    "   • les termes du SUJET (ex: impôt, fait générateur, dispositif)\n"
    "   • ET les termes de la POPULATION (ex: associations, entreprises, particuliers)\n"
    "   • ET la temporalité si celle-ci est mentionnée \n"
    "Si une population est mentionnée dans le BRIEF, elle doit apparaître dans TOUTES les requêtes.\n"
    "- Inclure aussi des formulations alternatives (intitle:, synonymes, alias d’articles).\n"
    "- Si le BRIEF contient une borne temporelle (time_bounds.from), ajoute un filtre temporel approprié (after:YYYY-MM-DD).\n"
    "- Évite les doublons et reste concis (3 à 6 requêtes par famille suffisent).\n"
)
        
    prompt = f"BRIEF:\n{json.dumps(brief, ensure_ascii=False)}\nGAPS:\n{json.dumps(gaps or {}, ensure_ascii=False)}"

    

    out = llm_complete(system, prompt)
    try:
        return parse_json_robuste(out)
    except Exception:
        return {"queries": {}, "raw": out}

# --- Remplace ta fonction agent_C_queries par celle-ci ---
def agent_C_queries(plan: dict) -> list[dict]:
    system = (
        "Génère des requêtes DuckDuckGo à partir du plan (plan.queries.*). "
        "Tu peux renvoyer soit un dict par familles {loi[], doctrine[], jurisprudence[], travaux_parlementaires[], fiscalonline[]}, "
        "soit une liste d'objets [{family, q}], soit une liste de chaînes. "
        "Prends en compte ces sources : legifrance.gouv.fr, bofip.impots.gouv.fr, conseil-etat.fr, courdecassation.fr, conseil-constitutionnel.fr, fiscalonline.com, vie-publique.fr, assemblee-nationale.fr, senat.fr. "
        "Inclure alias d'articles et opérateurs site:/intitle:/filetype:. "
        "Utilise STRICTEMENT ces domaines pour site: : "
        "legifrance.gouv.fr, bofip.impots.gouv.fr, conseil-etat.fr, courdecassation.fr, conseil-constitutionnel.fr, fiscalonline.com, vie-publique.fr, assemblee-nationale.fr, senat.fr. "
        "N'utilise pas d'autres domaines ressemblants (ex: 'bo-fip.fr' est interdit)."
    )
    prompt = json.dumps(plan, ensure_ascii=False)
    out = llm_complete(system, prompt)
    try:
        raw = parse_json_robuste(out)
    except Exception:
        raw = []
    return normalize_queries(raw)

# --- Remplace ta fonction agent_D_search par celle-ci (rendue plus robuste) ---
def agent_D_search(queries: list[dict], max_per_family=6) -> dict:
    hits = {
        "loi": [],
        "doctrine": [],
        "jurisprudence": [],
        "fiscalonline": [],
        "vie-publique": [],
        "assemblee-nationale": [],
        "senat": [],
        "conseil-constitutionnel": [],
        "conseil-etat": [],
        "courdecassation": [],
    }
    for q in queries:
        print('Analyzing query : ', q)
        fam = q.get("family", "misc") if isinstance(q, dict) else "misc"
        qtext = (q.get("q") if isinstance(q, dict) else str(q)) or ""
        if not qtext.strip():
            continue
        if fam not in hits:
            hits[fam] = []
        if len(hits[fam]) >= max_per_family:
            continue

        results = search_web(qtext, max_results=4)
        for r in results:
            if is_whitelisted(r["url"]):
                r["query"] = qtext
                r["source_domain"] = domain_of(r["url"])
                hits[fam].append(r)
        time.sleep(0.5 + random.random()*0.3)
    return hits

def agent_E_fetch_and_normalize(hits: dict) -> list[dict]:
    """
    Ne télécharge plus les pages. Retourne uniquement les métadonnées utiles
    pour chaque hit : url, title, snippet, source_domain, family.
    """
    docs = []
    for fam, arr in hits.items():
        for h in arr:
            url = h.get("final_url") or h.get("url", "")
            docs.append({
                "url": url,
                "title": h.get("title", ""),
                "snippet": h.get("snippet", ""),
                "source_domain": (h.get("source_domain") or domain_of(url) or ""),
                "family": fam,
            })
    return docs

# ===== Ranking v2 (Heuristique + BM25F) =====
# Dépend de: is_whitelisted(url), domain_of(url),
#            DOMAIN_AUTHORITY_WEIGHTS (dict), FAMILY_WEIGHTS (dict)


def _minmax(value, vmin, vmax):
    if vmax <= vmin: return 0.5
    return max(0.0, min(1.0, (value - vmin) / (vmax - vmin)))

def _norm_authority(domain: str) -> float:
    w = DOMAIN_AUTHORITY_WEIGHTS.get(domain, (AUTH_MIN + AUTH_MAX)/2)
    return _minmax(w, AUTH_MIN, AUTH_MAX)

def _norm_family(family: str) -> float:
    w = FAMILY_WEIGHTS.get(family, (FAM_MIN + FAM_MAX)/2)
    return _minmax(w, FAM_MIN, FAM_MAX)

def _family_intent_boost(query: str, family: str) -> float:
    q = (query or "").lower()
    boost = 0.0
    if family == "loi" and re.search(r"\b(150-0\s?[a-z])\b|\bcode général des impôts\b|\bcgi\b", q, re.I):
        boost += 0.10
    if family == "doctrine" and re.search(r"\bboi-[a-z0-9\-]+|\bbofip\b", q, re.I):
        boost += 0.10
    if family == "jurisprudence" and re.search(r"\b(ecli|cass|conseil d[’']etat|ce|caa)\b", q, re.I):
        boost += 0.10
    # fiscalonline : pas de boost par défaut
    return boost  # 0..0.10

def _normalize_url_for_dedupe(url: str) -> str:
    try:
        p = urllib.parse.urlparse(url)
        # Lowercase scheme/netloc, drop query & fragment, strip trailing slash on path
        return urllib.parse.urlunparse((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), "", "", ""))
    except Exception:
        return url or ""

# ---- Fraîcheur (date réelle quand possible) ----
_DATE_FULL_RE = re.compile(r"\b(20\d{2})[-/](0[1-9]|1[0-2])[-/](0[1-9]|[12]\d|3[01])\b")

def _extract_max_year(text: str) -> int | None:
    if not text:
        return None
    years = re.findall(r"\b(20\d{2}|19\d{2})\b", text)
    if not years:
        return None
    try:
        return max(int(y) for y in years)
    except Exception:
        return None

def _extract_date_from_url_or_meta(d: dict):
    """
    1) yyyy-mm-dd / yyyy/mm/dd dans URL/titre/snippet
    2) sinon, utilise l'année max détectée (au 1er janvier)
    """
    s = " ".join([d.get("url",""), d.get("title",""), d.get("snippet","")])
    m = _DATE_FULL_RE.search(s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except Exception:
            pass
    y = _extract_max_year(s)
    if y:
        try:
            return datetime(y, 1, 1)
        except Exception:
            return None
    return None

def _recency_real_bonus(d: dict, now: datetime | None = None) -> float:
    """
    Bonus 0..0.2 en fonction de l'écart (max 10 ans) entre la date détectée et aujourd'hui.
    """
    now = now or datetime.utcnow()
    dt = _extract_date_from_url_or_meta(d)
    if not dt:
        return 0.0
    delta_years = min(10.0, max(0.0, (now - dt).days / 365.25))
    return max(0.0, 0.2 * (1.0 - delta_years / 10.0))

# ---- Bonus "identifiants canoniques" ----
_CANON_PATTERNS = [
    r"\bLEGIARTI\d+\b",         # ID article Legifrance
    r"\bBOI-[A-Z0-9\-]+",       # BOFiP
    r"\b(150-0\s?[A-Z])\b",     # Article CGI 150-0 A/B/etc.
    r"\bECLI:[A-Z]{2}:\w+:\d{4}:\w+\b",  # ECLI jurisprudence
    r"\b(n°|no|nº)\s?\d{2,}\b"  # numéros de décisions
]

def _canonical_bonus(title: str, snippet: str, url: str) -> float:
    s = " ".join([title or "", snippet or "", url or ""])
    return 0.1 if any(re.search(p, s, flags=re.I) for p in _CANON_PATTERNS) else 0.0

# ---- Pénalité titres génériques / trop courts ----
def _generic_penalty(title: str) -> float:
    t = (title or "").strip().lower()
    bad = ["accueil", "home", "plan du site", "mentions légales", "cookies", "erreur", "not found"]
    pen = 0.0
    if len(t) < 10: pen += 0.03
    if any(b in t for b in bad): pen += 0.05
    return pen

# ---- BM25F (titre > snippet) ----
def _bm25_component(q_terms, doc_terms, k1=1.2, b=0.75, avgdl=50.0):
    """
    BM25 simplifié pour courts textes (dl = nb termes, tf = nb termes de q présents).
    """
    if not q_terms or not doc_terms:
        return 0.0
    tf = sum(1 for t in q_terms if t in doc_terms)
    dl = float(max(1, len(doc_terms)))
    return (tf * (k1 + 1.0)) / (tf + k1 * (1.0 - b + b * (dl / avgdl)))

def _tokens(s: str) -> list[str]:
    return re.findall(r"[a-zA-Zà-öù-ÿÀ-ÖÙ-Ÿ0-9\-]+", (s or "").lower())

def bm25f_title_snippet(query: str, title: str, snippet: str) -> float:
    q = _tokens(query)
    t = _tokens(title)
    s = _tokens(snippet)
    # pondération: titre 2.0, snippet 1.0
    return 2.0 * _bm25_component(q, t) + 1.0 * _bm25_component(q, s)

# ---- Déduplication: garder le meilleur score par URL normalisée ----
def _dedupe_keep_best(scored_docs: list[dict]) -> list[dict]:
    best = {}
    for d in scored_docs:
        key = _normalize_url_for_dedupe(d.get("url",""))
        if key not in best or d.get("score", 0.0) > best[key].get("score", 0.0):
            best[key] = d
    return list(best.values())

# ---- Agent F (version Heuristique v2 + BM25F) ----
def agent_F_rank_and_dedupe(brief: dict, plan: dict, docs: list[dict], max_per_family: int = 10) -> list[dict]:
    scored: list[dict] = []

    for d in docs:
        url = d.get("url", "")
        if not url or not is_whitelisted(url):
            continue

        domain = d.get("source_domain") or domain_of(url)
        family = d.get("family", "")
        query  = d.get("query", "")

        # 1) Priors normalisés + boost d'intention
        auth = _norm_authority(domain)                 # 0..1
        fam  = _norm_family(family)                    # 0..1
        fam += _family_intent_boost(query, family)     # +0..0.10 (cap via min)
        fam = min(1.0, fam)

        # 2) Composants "données"
        recency   = _recency_real_bonus(d)             # 0..0.2
        bm25f     = bm25f_title_snippet(query, d.get("title",""), d.get("snippet",""))
        bm25f_norm = min(1.0, bm25f / 3.0)             # ~0..1
        canon     = _canonical_bonus(d.get("title",""), d.get("snippet",""), url)  # 0..0.1
        gen_pen   = _generic_penalty(d.get("title",""))                             # 0..~0.08

        # 3) Score pondéré (plus de poids à pertinence & fraicheur)
        score = (0.10*auth + 0.10*fam + 0.30*recency + 0.40*bm25f_norm + 0.08*canon - 0.02*gen_pen)

        scored.append({**d, "score": round(float(score), 4)})

    # 4) Dédup par URL normalisée (garde le meilleur)
    deduped = _dedupe_keep_best(scored)

    # 5) Tri décroissant + cap par famille
    deduped.sort(key=lambda x: x.get("score", 0.0), reverse=True)
    out, per_family_counts = [], {}
    for d in deduped:
        fam = d.get("family", "misc")
        cnt = per_family_counts.get(fam, 0)
        if cnt >= max_per_family:
            continue
        per_family_counts[fam] = cnt + 1
        out.append(d)

    return out

def agent_G_filter_simple(brief: dict, docs: list[dict]) -> list[dict]:
    """
    Filtre les documents via LLM.
    Entrée: BRIEF (dict), docs (liste d'articles formatés)
    Sortie: liste filtrée d'articles pertinents (même format que l'entrée)
    """
    if not docs:
        return []

    system = (
        "Tu es un assistant fiscal. "
        "Tu reçois une DEMANDE (BRIEF) et une LISTE D'ARTICLES (JSON). "
        "Ta tâche est de NE GARDER QUE les sources pertinentes pour répondre à la demande, correspondant à l'issue du BRIEF. "
        "Accorde de l'importance à ces sources Code Général des Impôts, BOFIP, jurisprudence, FiscalOnline."
        "Renvoie UNIQUEMENT un JSON valide: une liste d'articles pertinents, "
        "dans le même format que la liste reçue (sans rien changer à la structure). "
        "Si aucun article n'est pertinent, renvoie la liste initiale."
    )

    user = (
        "DEMANDE (BRIEF):\n" +
        json.dumps(brief, ensure_ascii=False) +
        "\n\nARTICLES:\n" +
        json.dumps(docs, ensure_ascii=False)
    )

    out = llm_complete(system, user)
    try:
        filtered = parse_json_robuste(out)
        if isinstance(filtered, list):
            return filtered
        else:
            return []
    except Exception:
        return []
    
# --------------- Orchestrateur ----------------
def run_pipeline(user_query: str, status_callback=None):
    # Internal helper to safely notify UI about current agent
    def _update_status(message: str):
        if status_callback is not None:
            try:
                status_callback(message)
            except Exception:
                pass

    _update_status("Agent A – Clarificateur")
    println("[Agent A] Clarificateur")
    brief = agent_A_clarify(user_query)
    println("BRIEF", brief)

    _update_status("Agent B – Planificateur")
    println("[Agent B] Planificateur")
    plan = agent_B_plan(brief)
    println("PLAN", plan)

    _update_status("Agent C – Générateur de requêtes")
    println("[Agent C] Générateur de requêtes")
    queries = agent_C_queries(plan)
    println("QUERIES", queries)

    _update_status("Agent D – Recherche web")
    println("[Agent D] Recherche web")
    hits = agent_D_search(queries, max_per_family=4)
    println("HITS", hits)

    _update_status("Agent E – Collecteur (métadonnées uniquement)")
    println("[Agent E] Collecteur (métadonnées uniquement)")
    docs = agent_E_fetch_and_normalize(hits)
    println("DOCS", docs)
    
    _update_status("Agent F – Rang & Dédupe")
    println("[Agent F] Rank & Dédupe")
    ranked_docs = agent_F_rank_and_dedupe(brief, plan, docs, max_per_family=4)
    println("RANKED_DOCS", ranked_docs)

    _update_status("Agent G – Vérifie la perinence des sources")
    println("[Agent G] Check relevency")
    usefull_docs = agent_G_filter_simple(brief,ranked_docs)
    println("USEFULL_DOCS", usefull_docs)

    _update_status("Finalisation…")
    return {
        "brief": brief,
        "plan": plan,
        "queries": queries,
        "hits": hits,
        "docs": docs,
        "ranked_docs": ranked_docs,
        "usefull_docs":usefull_docs,
    }

if __name__ == "__main__":
    if len(sys.argv) < 2:
        question = "Comment sont traitées fiscalement les plus-values sur cession de titres après 2023 ?"
    else:
        question = " ".join(sys.argv[1:])
    run_pipeline(question)