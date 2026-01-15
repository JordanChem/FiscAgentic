"""
Agent Généraliste : Génère des requêtes de recherche optimisées
"""
import ast
import openai
import datetime


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
- legifrance.gouv.fr
- bofip.impots.gouv.fr
- conseil-etat.fr
- courdecassation.fr
- conseil-constitutionnel.fr
- assemblee-nationale.fr
- senat.fr
- fiscalonline.fr

🧠 LOGIQUE INTELLIGENTE
Tu dois :
1️⃣ Identifier la problématique juridique (ex : abus de droit, montage artificiel, optimisation, résidence fiscale, TVA…)
2️⃣ Déterminer quelles familles de sources sont pertinentes
3️⃣ Construire des requêtes efficaces même si tu ne connais pas :
   - le numéro exact d'un article
   - ni la référence BOFiP précise
   - ni la décision CE exacte

⚙️ STRUCTURE OBLIGATOIRE DES REQUÊTES
Produis des requêtes réparties ainsi :

━━━━━━━━━━━━━━━━━━
1️⃣ TEXTES LÉGAUX (CGI / LPF)
━━━━━━━━━━━━━━━━━━
- Inclure l'année actuelle {datetime.datetime.now().year} si la problématique concerne des règles applicables aujourd'hui
- Utiliser soit un article si connu (ex: L64 LPF), soit des mots-clés juridiques précis
Exemples de style attendu :
- site:legifrance.gouv.fr "livre des procédures fiscales" abus de droit {datetime.datetime.now().year}
- site:legifrance.gouv.fr "code général des impôts" requalification fiscale {datetime.datetime.now().year}

━━━━━━━━━━━━━━━━━━
2️⃣ BOFIP – DOCTRINE À JOUR
━━━━━━━━━━━━━━━━━━
- Toujours inclure l'année actuelle pour cibler des versions à jour
- Si la problématique concerne abus de droit / montages / requalification :
  OBLIGATOIRE : inclure au moins une requête visant explicitement la famille CF (contrôle)
  ex :
  - site:bofip.impots.gouv.fr "BOI-CF" abus de droit {datetime.datetime.now().year}
  - site:bofip.impots.gouv.fr "procédure d'abus de droit" {datetime.datetime.now().year}

━━━━━━━━━━━━━━━━━━
3️⃣ CONSEIL D'ÉTAT / JURISPRUDENCE
━━━━━━━━━━━━━━━━━━
⚠️ Ne PAS ajouter arbitrairement une année récente sur la jurisprudence.
Privilégier :
- notions CE connues
- vocabulaire juridique clé
- patterns doctrinaux

Inclus dans les requêtes des notions robustes comme par exemple : 

Si la question concerne abus de droit / optimisation / montage / requalification,
il faut inclure des requêtes contenant explicitement des notions CE robustes, par exemple :
- "appréciation d'ensemble"
- "montage artificiel"
- "abus de droit" L.64

Exemples de style attendu :
- site:conseil-etat.fr "appréciation d'ensemble" abus de droit
- site:legifrance.gouv.fr Conseil d'État "montage artificiel" fiscal
- site:conseil-etat.fr "abus de droit" L.64

━━━━━━━━━━━━━━━━━━
4️⃣ ÉVENTUELLEMENT
━━━━━━━━━━━━━━━━━━
- Cour de cassation si pénal
- Conseil constitutionnel si constitutionnalité
- Parlement UNIQUEMENT si utile pour compréhension historique

🚫 INTERDICTIONS
- ne pas inventer de numéros d'articles ou références BOFiP
- ne pas ajouter une année récente sur la jurisprudence sans raison juridique
- ne pas inclure de sources privées
- ne pas donner d'explication écrite
- respecter STRICTEMENT le format demandé

🕒 TEMPORALITÉ
- Si la question mentionne une période → l'utiliser
- Sinon :
  - inclure l'année actuelle UNIQUEMENT pour :
    ✔ codes
    ✔ LPF
    ✔ BOFIP / doctrine applicable
  - ne pas coller d'année moderne sur les jurisprudences

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
        old_section = "🔒 SOURCES AUTORISÉES UNIQUEMENT\nChaque requête doit contenir `site:` parmi les domaines disponibles :\n- legifrance.gouv.fr\n- bofip.impots.gouv.fr\n- conseil-etat.fr\n- courdecassation.fr\n- conseil-constitutionnel.fr\n- assemblee-nationale.fr\n- senat.fr\n- fiscalonline.fr"
        new_section = f"🔒 SOURCES AUTORISÉES UNIQUEMENT\nChaque requête DOIT contenir `site:` parmi les domaines suivants (UNIQUEMENT ceux-ci) :\n{domains_list}\n\n⚠️ IMPORTANT : N'utilise QUE les domaines listés ci-dessus. Ne génère AUCUNE requête avec un domaine qui n'est pas dans cette liste."
        system_content = LLM_QUERY_PROMPT.replace(old_section, new_section)
    else:
        system_content = LLM_QUERY_PROMPT
    
    prompt = (
        f"Question utilisateur : {user_query}\n\n"
        "Respecte strictement l'ensemble des instructions ci-dessus."
    )
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
        return queries_list
    except Exception as e:
        raise RuntimeError(
            f"Réponse non décodable en list Python. Contenu reçu :\n{content}\nErreur : {e}"
        ) from e
