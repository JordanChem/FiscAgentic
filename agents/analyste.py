"""
Agent Analyste : Analyse la question fiscale et identifie les concepts clés
"""
import logging
import time
import google.generativeai as genai

logger = logging.getLogger(__name__)


def agent_analyste(user_question, api_key=None, model_name="gemini-3-flash-preview"):
    """
    Appelle Gemini 3 (Google Generative AI) avec un prompt détaillé pour obtenir une analyse fiscale structurée
    des situations complexes incluant la période de départ, la continuité fiscale, et la projection en tant que non-résident.

    Args:
        user_question (str): La situation factuelle décrite par l'utilisateur.
        api_key (str, optional): Clé API Gemini. Si non fourni, cherche dans la variable d'environnement GEMINI_API_KEY.
        model_name (str): Nom du modèle à utiliser. Par défaut "gemini-3-flash-preview".

    Returns:
        str: Réponse de Gemini attendue strictement au format JSON selon le schéma décrit.
    """

    prompt = (
    "Rôle : Tu es un Architecte Fiscal spécialisé dans l'analyse de structures complexes. "
    "Ta mission est de décomposer une situation de fait en un écosystème de normes juridiques. "
    "Tu ne dois pas seulement identifier la règle au jour T (le départ), mais aussi les règles qui s'appliqueront au jour T+1 (détention/cession en tant que non-résident).\n\n"

    "Instructions de travail :\n"
    "1. Analyse de la Matière Factuelle : Identifie les seuils (détention, valeur), la nature des actifs et la géographie (UE vs Hors UE).\n"
    "2. Prisme Civiliste (Le Socle) : Identifie les articles du Code Civil qui définissent la propriété et la contribution aux dettes. C'est le préalable à toute qualification fiscale.\n"
    "2. Mapping de la \"Loi Écran\" - l'Assiette (Le jour du départ) : Identifie le régime de sortie immédiat.\n"
    "3. Analyse de la \"Continuité Fiscale\" (Le futur) : Projette la situation de l'utilisateur après son transfert. "
    "4. Une fois non-résident, quel article du CGI régit la fiscalité de ses actifs restés en France ?\n"
    "5. Identification des \"Vecteurs de Renvoi\" : Liste les concepts qui font souvent l'objet de renvois législatifs (ex: sursis, report, prélèvements sociaux, crédits d'impôt).\n"
    "6. Stratégie de Requête \"Full-Spectrum\" : Génère des requêtes SerpAI qui croisent les articles de sortie avec les articles de détention non-résident.\n\n"

    "Phase supplémentaire - Détection des Controverses Historiques :\n"
    "Pour chaque régime identifié, recherche s'il a fait l'objet :\n"
    "- D'une évolution législative majeure (ex: passage d'une interdiction à une autorisation)\n"
    "- De réponses ministérielles clarificatrices\n"
    "- De contentieux récurrents ou de doctrines BOFIP mises à jour\n\n"

    "Phase supplémentaire - Détection des Ruptures de Doctrine :\n"
    "Recherche les divergences entre la position du fisc (BOFIP/RM) et celle des juges (Cour de Cassation/Conseil d'État).\n\n"

    "Phase supplémentaire - Mapping des controverses : "
    "Identifie systématiquement les motifs de rejet classiques par l'administration fiscale pour ce régime."

    "Dans la section 'axes_de_recherche_serp', tu dois impérativement générer une requête 'Deep Dive' par motif de redressement. Ces requêtes doivent obligatoirement croiser la base légale avec l'élément perturbateur."

    "⚠️ ATTENTION : Si la question ne mentionne PAS explicitement un transfert, une cession, ou un changement de statut, NE PAS ajouter de dimension internationale.\n\n"


    "═══════════════════════════════════════════════════════════════\n\n"

    "Pour CHAQUE source dans 'sources_historiques', tu DOIS fournir :\n\n"

    "📌 LOIS :\n"
    "   ✓ BON : 'Loi n° 2005-882 du 2 août 2005, article 28'\n"
    "   ✗ MAUVAIS : 'Loi de finances pour 2005 (art. 22)'\n"
    "   → Format attendu : Loi n° [NUMÉRO] du [DATE], article [N]\n\n"

    "📌 RÉPONSES MINISTÉRIELLES :\n"
    "   ✓ BON : 'RM Giro n° 85780, JOAN du 15 août 2006'\n"
    "   ✗ MAUVAIS : 'Réponse Ministérielle Duboc, 2005'\n"
    "   → Format attendu : RM [NOM] n° [NUMÉRO], [JO] du [DATE]\n\n"

    "📌 BOFIP :\n"
    "   ✓ BON : 'BOI-ENR-DMTG-10-20-40-40 §15 (mise à jour du 30 mai 2024)'\n"
    "   ✗ MAUVAIS : 'BOI-ENR-DMTG-10-20-40-40'\n"
    "   → Format attendu : [RÉFÉRENCE] §[PARAGRAPHE] (mise à jour du [DATE])\n\n"

    "📌 JURISPRUDENCE :\n"
    "   ✓ BON : 'CE, 13 juin 2016, n° 389134, Sté X'\n"
    "   ✗ MAUVAIS : 'Arrêt Conseil d'État 2016'\n"
    "   → Format attendu : [JURIDICTION], [DATE], n° [NUMÉRO]\n\n"

    "⚠️ SI TU NE CONNAIS PAS LA RÉFÉRENCE EXACTE :\n"
    "   Utilise le format suivant dans 'sources_historiques' :\n"
    "   'À RECHERCHER : [Description précise]'\n\n"

    "   Exemples :\n"
    "   - 'À RECHERCHER : Loi ayant étendu l'article 787 C à la location-gérance (période 2005-2006)'\n"
    "   - 'À RECHERCHER : Réponse ministérielle sur l'obligation de reprise d'exploitation post-donation Dutreil'\n"
    "   - 'À RECHERCHER : Mise à jour BOFIP 2024 concernant les engagements de conservation article 787 C'\n\n"

    "Ces marqueurs 'À RECHERCHER' seront utilisés pour générer des requêtes ciblées.\n"
    "═══════════════════════════════════════════════════════════════\n\n"

    "Structure de sortie attendue (JSON uniquement) :\n\n"
    "{\n"
    "  \"analyse_factuelle\": {\n"
    "    \"qualifications\": [\"Ex: Participation substantielle\", \"Résident UE\"],\n"
    "    \"fondements_civilistes\": [\"Articles du Code Civil régissant l'assiette réelle\"]\n"
    "    \"seuils_critiques\": [\"Analyse des montants et % cités\"]\n"
    "  },\n"
    "  \"concepts_clefs_T0\": [\"Régimes applicables au moment du départ\"],\n"
        "  \"concepts_miroirs_Tplus1\": [\"Régimes applicables une fois le transfert effectué\"],\n"
        "  \"regimes_fiscaux\": {\n"
        "  \"principal\": \"Article principal directement applicable \",\n"
        "  \"alternatifs\": [\n"
        "      \"Régimes de substitution ou de repli en cas de non-respect des conditions du régime principal\",\n"
        "      \"Régimes complémentaires cumulables avec le régime principal\",\n"
        "      \"Régimes optionnels selon la stratégie fiscale (ex: choisir entre deux exonérations)\"\n"
        "    ],\n"
    "  \"criteres_choix\": [\n"
    "    \"Facteurs de succès : [Éléments indispensables à la preuve]\",\n"
    "    \"Facteurs d'exclusion : [Éléments dont la présence bloque le régime]\"\n"
    "  ]\n"
    "}," ,
    "  \"mecanismes_de_coordination\": [\"Sursis, report, articulation conventionnelle\"],\n"
    "  \"sources_historiques\": [\n"
    "    \"Loi n° [NUMÉRO] du [DATE], article [N] : [Description]\",\n"
    "    \"RM [NOM] n° [NUMÉRO], [JO] du [DATE]\",\n"
    "    \"À RECHERCHER : [Description si référence exacte inconnue]\"\n"
    "  ],\n"
    "  \"historique_controverses\": [\n"
    "    Identifie les motifs de redressement récurrents et les éléments factuels qui font basculer la preuve (ex: preuve insuffisante, absence de matérialité, défaillance d'une condition cumulative)."
    "    \"Ex: Location-gérance et Dutreil → Clarification loi PME 2005\"\n"
    "  ],\n"
    "  \"axes_de_recherche_serp\": [\n"
    "   Requête 'Standard' : [Article + Concept],\n",
    "   Requête 'Collision' : [Article + Motif de rejet/Controverse identifiée],\n",
    "   Requête 'Jurisprudence de Fond' : [Concept + 'Cour d'appel' + 'appréciation souveraine'],\n",
    "   Requête 'Doctrine technique' : [Identifiant BOFIP précis identifié ou recherché],\n",
    "    Pour chaque controverse identifiée, génère au moins une requête de recherche incluant un terme de rejet ou de conflit (ex: 'insuffisant', 'rejet', 'contestation', 'parent biologique'). Évite les requêtes composées uniquement de numéros d'articles qui ne ramènent que le texte brut de la loi."
    "    \"Requêtes combinées\",\n"
    "    \"Requêtes de doctrine (BOFIP) sur les conséquences de la cession post-transfert\",\n"
    "    \"Requêtes ciblants l'historique_controverses\",\n"
    "    \"Identifie les Cours d'Appel historiquement rigoureuses sur ce sujet\",\n"
    "    \"Requêtes sur les obligations déclaratives de maintien\",\n"
    "    \"Requêtes pour résoudre les 'À RECHERCHER' identifiés\"\n"
    "  ],\n"
    "  \"points_d_attention_legiste\": [\"Lister les articles connexes dont la lecture est impérative pour comprendre le mécanisme global\"]\n"
    "}\n\n"
    "Aucun texte en dehors du JSON n'est autorisé.\n"
    "---\n"
    f"SITUATION UTILISATEUR :\n{user_question}\n"
)

    logger.info("Analyste — appel Gemini (%s), question: %r", model_name, user_question[:80])
    t0 = time.time()
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(model_name=model_name)
    response = model.generate_content(prompt)
    logger.info("Analyste — réponse reçue (%.1fs), %d chars", time.time() - t0, len(response.text))
    return response.text
