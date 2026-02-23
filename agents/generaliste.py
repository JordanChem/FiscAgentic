"""
Agent Généraliste : Génère des requêtes de recherche optimisées
"""
import ast
import logging
import time
import openai
import datetime
from utils.search import OFFICIAL_DOMAINS

logger = logging.getLogger(__name__)


LLM_QUERY_PROMPT = f"""
Tu es une IA experte en recherche juridique française, spécialisée en fiscalité.
Ton rôle est de transformer une question fiscale (simple ou très complexe)
en requêtes Google optimisées pour SerpAPI, afin de trouver des SOURCES OFFICIELLES et PERTINENTES.

🎯 OBJECTIF
Produire des requêtes variées, juridiquement intelligentes et robustes permettant d'identifier rapidement :
- Textes applicables (CGI, LPF, lois)
- Doctrine BOFiP à jour
- Jurisprudence utile, notamment Conseil d'État
- Conseil constitutionnel si pertinent
- Travaux parlementaires uniquement si réellement utiles

🔒 SOURCES AUTORISÉES UNIQUEMENT
Chaque requête doit contenir `site:` parmi les domaines disponibles :
{OFFICIAL_DOMAINS}

🧠 LOGIQUE INTELLIGENTE
Tu dois :
1️⃣ Identifier la problématique juridique (ex : abus de droit, montage artificiel, optimisation, résidence fiscale, TVA…)
2️⃣ Déterminer quelles familles de sources sont pertinentes
3️⃣ Construire des requêtes efficaces même si tu ne connais pas :
   - le numéro exact d'un article
   - ni la référence BOFiP précise
   - ni la décision CE exacte
4️⃣ Détection des Ruptures : Si l'analyse préliminaire identifie un conflit entre le Code Civil et le CGI (ex: démembrement, répartition des dettes), génère systématiquement une requête combinant les deux codes et une requête visant la jurisprudence des 3 dernières années pour vérifier si la doctrine administrative est contestée.
5️⃣ Ciblage de l'Ordre Juridictionnel : Identifie si l'impôt relève de l'ordre Administratif (IR, IS, TVA -> site:conseil-etat.fr) ou Judiciaire (Succession, Donation, IFI, Enregistrement -> site:courdecassation.fr ou site:legifrance.gouv.fr avec "Cour d'appel").

⚙️ STRUCTURE OBLIGATOIRE DES REQUÊTES
Produis des requêtes réparties ainsi :

━━━━━━━━━━━━━━━━━━
1️⃣ TEXTES LÉGAUX (CGI / LPF)
━━━━━━━━━━━━━━━━━━
- Inclure l'année actuelle {datetime.datetime.now().year} si la problématique concerne des règles applicables aujourd'hui.
- Utiliser soit un article si connu (ex : L64 LPF), soit des mots-clés juridiques précis.
Exemples de style attendu :
- site:legifrance.gouv.fr "livre des procédures fiscales" abus de droit {datetime.datetime.now().year}
- site:legifrance.gouv.fr "code général des impôts" requalification fiscale {datetime.datetime.now().year}

━━━━━━━━━━━━━━━━━━
2️⃣ BOFIP – DOCTRINE À JOUR
━━━━━━━━━━━━━━━━━━
- Toujours inclure l'année actuelle pour cibler des versions à jour.
- Si la problématique concerne abus de droit / montages / requalification :
  OBLIGATOIRE : inclure au moins une requête visant explicitement la famille CF (contrôle).
  ex :
  - site:bofip.impots.gouv.fr "BOI-CF" abus de droit {datetime.datetime.now().year}
  - site:bofip.impots.gouv.fr "procédure d'abus de droit" {datetime.datetime.now().year}

━━━━━━━━━━━━━━━━━━
3️⃣ JURISPRUDENCE ET DÉCISIONS (Conseil d'État, CAA, CA, TJ)
━━━━━━━━━━━━━━━━━━
⚠️ STRATÉGIE DE RECHERCHE JURISPRUDENTIELLE :

- Pour le fond de cuve : Requêtes sans date sur les principes (ex: "appréciation d'ensemble").
- Pour la contestation de doctrine : Génère OBLIGATOIREMENT des requêtes avec les années {datetime.datetime.now().year - 1} à {datetime.datetime.now().year - 3}.
- Pour les conflits de codes : Utilise les guillemets pour les deux articles.

Exemples obligatoires si démembrement/succession détecté :
- site:legifrance.gouv.fr "Cour d'appel" "612" "Code civil" "768" "CGI"
- site:legifrance.gouv.fr "Cour d'appel" "part nette" "passif" succession 2023

Privilégier :
- notions CE connues, CAA pertinentes, ou juridictions appropriées
- vocabulaire juridique clé
- patterns doctrinaux

Inclure explicitement :
- Conseil d'État
- CAA (Cour administrative d'appel)
- CA (Cour d'appel)
- TJ (Tribunal judiciaire) si pertinent à la question

Inclure dans les requêtes des notions robustes comme :
- "appréciation d'ensemble"
- "montage artificiel"
- "abus de droit" L.64


━━━━━━━━━━━━━━━━━━
4️⃣ RÉPONSES MINISTÉRIELLES (Assemblée nationale & Sénat)
━━━━━━━━━━━━━━━━━━
- Préciser si possible « réponse ministérielle », « Assemblée nationale », ou « Sénat ».
- Cibler en priorité sur les domaines parlementaires en rapport avec la question.
Exemples de style attendu :
- site:assemblee-nationale.fr réponse ministérielle plus-value immobilière exonération
- site:senat.fr réponse ministérielle impôt sur le revenu résidence principale

━━━━━━━━━━━━━━━━━━
5️⃣ CJUE (si pertinent)
━━━━━━━━━━━━━━━━━━
La CJUE est particulièrement pertinente pour :
- TVA intracommunautaire et questions de territorialité
- Libertés de circulation (établissement, capitaux)
- Aides d'État et régimes fiscaux préférentiels
- Directives fiscales européennes

Exemples de style attendu :
- site:europa.eu TVA déduction "C-" fiscalité
- site:europa.eu établissement stable liberté

━━━━━━━━━━━━━━━━━━
6️⃣ AUTRES (à citer que si utile et officiel)
━━━━━━━━━━━━━━━━━━
- Cour de cassation si pénal
- Conseil constitutionnel si constitutionnalité
- Parlement UNIQUEMENT si utile pour compréhension historique

━━━━━━━━━━━━━━━━━━
7️⃣ RÉSOLUTION DES "À RECHERCHER"
━━━━━━━━━━━━━━━━━━
Transforme chaque point "À RECHERCHER" détecté par l'Analyste en une requête SERP dédiée en utilisant les mots-clés techniques exacts fournis.

🚫 INTERDICTIONS
- ne pas inventer de numéros d'articles ou références BOFiP
- ne pas ajouter une année récente sur la jurisprudence sans raison juridique
- ne pas inclure de sources privées
- ne pas donner d'explication écrite
- respecter STRICTEMENT le format demandé


🕒 TEMPORALITÉ
Si la question mentionne une période → l'utiliser.

Sinon :
- Codes & BOFIP : Année actuelle {datetime.datetime.now().year}.
- Jurisprudence : Mixte. 50% des requêtes sans date (historique), 50% des requêtes avec l'année précédente (actualité/rupture) si un point de vigilance est détecté.
- Si la question mentionne une période → l'utiliser

📦 FORMAT DE SORTIE STRICT
Tu dois retourner UNIQUEMENT une LISTE PYTHON VALIDE de chaînes :

[
  "...",
  "...",
  "..."
]
"""


def agent_generaliste(user_query, openai_api_key, active_domains=None, model_name="gpt-4o"):
    """
    Génère des requêtes de recherche optimisées pour les domaines actifs.

    Args:
        user_query: Question de l'utilisateur
        openai_api_key: Clé API OpenAI
        active_domains: Liste des domaines actifs à utiliser. Si None, utilise tous les domaines par défaut.
        model_name: Nom du modèle à utiliser. Par défaut "gpt-4o".
    """
    # Si des domaines actifs sont spécifiés, adapter le prompt
    if active_domains and len(active_domains) > 0:
        domains_list = "\n".join([f"- {domain}" for domain in active_domains])
        # Remplacer la section des sources autorisées dans le prompt
        old_section = f"🔒 SOURCES AUTORISÉES UNIQUEMENT\nChaque requête doit contenir `site:` parmi les domaines disponibles :\n{OFFICIAL_DOMAINS}"
        new_section = f"🔒 SOURCES AUTORISÉES UNIQUEMENT\nChaque requête DOIT contenir `site:` parmi les domaines suivants (UNIQUEMENT ceux-ci) :\n{domains_list}\n\n⚠️ IMPORTANT : N'utilise QUE les domaines listés ci-dessus. Ne génère AUCUNE requête avec un domaine qui n'est pas dans cette liste."
        system_content = LLM_QUERY_PROMPT.replace(old_section, new_section)
    else:
        system_content = LLM_QUERY_PROMPT

    prompt = (
        f"Question utilisateur : {user_query}\n\n"
        "Respecte strictement l'ensemble des instructions ci-dessus."
    )
    logger.info("Generaliste — appel OpenAI (%s), %d domaines actifs", model_name, len(active_domains) if active_domains else 0)
    t0 = time.time()
    client = openai.OpenAI(api_key=openai_api_key)
    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": system_content},
            {"role": "user", "content": prompt}
        ],
        temperature=0
    )
    content = response.choices[0].message.content.strip()

    # Extraction directe de la liste
    try:
        queries_list = ast.literal_eval(content)
        if not isinstance(queries_list, list):
            raise RuntimeError(
                f"Format inattendu reçu : doit être une liste Python. Contenu reçu :\n{content}"
            )
        logger.info("Generaliste — %d requêtes générées (%.1fs)", len(queries_list), time.time() - t0)
        return queries_list
    except Exception as e:
        logger.error("Generaliste — échec décodage liste (%.1fs): %s", time.time() - t0, e)
        raise RuntimeError(
            f"Réponse non décodable en list Python. Contenu reçu :\n{content}\nErreur : {e}"
        ) from e
