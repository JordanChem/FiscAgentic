# API Assistant Fiscal — contrat d'intégration

Service HTTP exposant le pipeline multi-agents de réponse aux questions fiscales
françaises. Il est appelé par l'API de fiscalonline.fr, qui joue le rôle de
**proxy d'authentification** : elle authentifie l'abonné, puis relaie l'appel.

Base : `https://api-fiscal.fiscalonline.fr` (à confirmer)
Documentation interactive (hors production) : `/docs`

---

## 1. Authentification

Chaque requête porte trois en-têtes :

| En-tête | Obligatoire | Rôle |
|---|---|---|
| `X-API-Key` | oui | Secret partagé entre le proxy et ce service |
| `X-User-Email` | oui | Identité de l'abonné, déjà authentifié par le proxy |
| `X-User-Id` | non | Identifiant interne fiscalonline (utilisé pour les quotas) |
| `X-Request-Id` | non | Repris tel quel dans les logs et la réponse ; sinon généré |

L'identité **ne doit jamais** transiter par le corps de requête : toutes les
lectures et écritures Supabase sont filtrées sur `X-User-Email`.

Le service écoute sur la boucle locale. Il n'est jamais joignable directement
depuis un navigateur : le front parle au proxy, le proxy parle à ce service.

---

## 2. `POST /v1/chat` — flux SSE

L'endpoint principal. Une requête = un tour de conversation.

### Corps

Deux formes acceptées. La plus simple :

```json
{
  "message": "Quel est le taux de TVA sur la rénovation énergétique ?",
  "conversation_id": "b3f1…",
  "options": {
    "active_domains": ["legifrance.gouv.fr", "bofip.impots.gouv.fr"],
    "use_justicelibre": true
  }
}
```

Et le corps natif du hook `useChat` de l'AI SDK, accepté tel quel :

```json
{
  "id": "chat-abc",
  "messages": [
    { "role": "user", "parts": [{ "type": "text", "text": "…" }] }
  ]
}
```

| Champ | Type | Notes |
|---|---|---|
| `message` | string | Question. Alternative à `messages`. |
| `messages` | UIMessage[] | Le **dernier** message `user` est retenu ; formats AI SDK v4 et v5 gérés. |
| `id` | string | Id de chat généré côté client. **Adopté** s'il n'existe pas encore côté serveur. |
| `conversation_id` | string | Référence à une conversation *existante* : `404` si introuvable. |
| `options.active_domains` | string[] | Sous-ensemble de `GET /v1/config`. Défaut : tous. |
| `options.use_justicelibre` | bool | Recherche jurisprudence via JusticeLibre. Défaut `true`. |
| `options.use_fiscalonline` | bool | Articles internes FiscalOnline. Défaut : déduit des domaines actifs. |
| `options.models` | object | Réservé au debug. `403` si `ALLOW_MODEL_OVERRIDE=false`. |

> **`id` vs `conversation_id`** : `useChat` génère son identifiant de chat et
> l'envoie dès le premier message, alors que rien n'existe encore côté serveur.
> `id` signifie donc « utilise cet identifiant, crée-le si besoin », tandis que
> `conversation_id` affirme l'existence de la conversation.
>
> **Le `conversation_id` renvoyé peut différer de l'`id` envoyé.** Le stockage
> impose des UUID ; un identifiant client qui n'en est pas un est converti de
> façon déterministe (le même `id` retombe toujours sur la même conversation).
> Le front doit donc **reprendre `data-meta.conversation_id`** pour les appels
> `/v1/conversations/*` et `/v1/feedback`, plutôt que son propre `id`.

### Réponse

`text/event-stream`, protocole **AI SDK v5 (UI Message Stream)**.

En-têtes : `x-vercel-ai-ui-message-stream: v1`, `x-accel-buffering: no`,
`x-request-id`.

Séquence typique :

```
data: {"type":"start","messageId":"m_9f2c…"}

data: {"type":"data-progress","id":"progress","transient":true,
       "data":{"step":"analyse","label":"Analyse de la question","status":"running",
               "progress":0,"index":1,"total":11}}
… (11 étapes × running/done)

data: {"type":"data-sources","data":{"sources":[
       {"title":"BOI-TVA-LIQ-30-20-95","url":"https://bofip…","score":0.95,
        "source_domain":"bofip.impots.gouv.fr","snippet":"…"}]}}

data: {"type":"text-start","id":"t0"}
data: {"type":"text-delta","id":"t0","delta":"## En résumé\n"}
…
data: {"type":"text-end","id":"t0"}

data: {"type":"data-points_cles","data":{"points":["Vérifier l'ancienneté du logement"]}}
data: {"type":"data-meta","data":{"conversation_id":"b3f1…","message_id":"m_9f2c…",
       "trace_id":"fisca-8a1c…","is_follow_up":false,"escalated":false,
       "cost_usd":0.238,"duration_s":284.0,"saved":true}}

data: {"type":"finish"}
data: [DONE]
```

Points à connaître côté front :

- **`data-progress` est `transient`** : reçu via `onData`, jamais persisté dans le
  message. Son `id` est constant (`"progress"`), donc l'AI SDK réconcilie **une**
  part au lieu d'en empiler onze. Idéal pour une barre de progression.
- **`data-sources` et `data-meta` sont persistés** : le front en a besoin au
  rechargement d'une conversation.
- **`text-delta` contient du markdown**, jamais de JSON.
- **Conserver `trace_id` et `conversation_id`** de `data-meta` : ce sont les
  entrées de `POST /v1/feedback`.
- **Lignes `: ping`** : commentaires SSE de maintien de connexion émis toutes les
  15 s pendant les étapes longues (recherche, scraping). À ignorer — les clients
  SSE standards s'en chargent.
- **Un flux n'est jamais laissé pendant** : en cas d'erreur, une trame
  `{"type":"error","errorCode":…,"errorText":…}` précède toujours `finish`.

Durée : **1 à 5 minutes** pour une question nouvelle, **5 à 15 secondes** pour une
question de suivi. Prévoir l'affichage de la progression en conséquence.

### Questions de suivi

Renvoyer le `conversation_id` obtenu suffit : le serveur conserve le contexte
technique (sources, analyse, historique) et route automatiquement.

Si l'agent de suivi estime que la question sort du contexte, le service
**enchaîne de lui-même** sur le pipeline complet — la trame
`data-progress {"step":"escalade"}` le signale, et `data-meta.escalated` vaut
`true`. Aucune action côté front.

### Passer en AI SDK v4

Le protocole est un réglage serveur (`AI_SDK_PROTOCOL=v4`) : les trames
deviennent `0:"texte"` / `2:[{…}]` / `d:{…}` avec l'en-tête
`x-vercel-ai-data-stream: v1`. À décider avec l'équipe front avant l'intégration.

---

## 3. Autres endpoints

| Méthode | Route | Description |
|---|---|---|
| `POST` | `/v1/chat/sync` | Même traitement, réponse JSON complète (pas de streaming). Batch, debug. |
| `GET` | `/v1/conversations?limit=20` | Liste des conversations de l'utilisateur. |
| `GET` | `/v1/conversations/{id}` | Détail avec messages et sources. |
| `DELETE` | `/v1/conversations/{id}` | Suppression logique. |
| `POST` | `/v1/feedback` | 👍/👎 → Supabase + score sur la trace Langfuse. |
| `GET` | `/v1/config` | Domaines disponibles + libellés, étapes du pipeline, limites. |
| `GET` | `/health` | Liveness. Public, aucune I/O. |
| `GET` | `/ready` | Readiness : secrets + Supabase + places libres. |

`POST /v1/feedback` :

```json
{
  "conversation_id": "b3f1…",
  "question": "…",
  "answer": "…",
  "rating": 1,
  "comment": "Très clair",
  "trace_id": "fisca-8a1c…",
  "sources_count": 12,
  "is_follow_up": false
}
```

Le `trace_id` attache la note à la trace Langfuse : les réponses mal notées
deviennent directement filtrables dans le dashboard qualité.

---

## 4. Erreurs

Toutes les réponses non-SSE en échec partagent la même enveloppe :

```json
{"error": {"code": "capacity_exceeded", "message": "…",
           "request_id": "a1b2…", "trace_id": null, "retriable": true}}
```

| Code HTTP | `code` | Signification |
|---|---|---|
| 401 | `unauthorized` | Clé ou identité absente / invalide |
| 403 | `forbidden` | Option interdite (surcharge de modèles) |
| 404 | `not_found` | Conversation inexistante ou appartenant à un autre utilisateur |
| 413 | `validation_error` | Corps trop volumineux |
| 422 | `validation_error` | Question vide / trop longue, domaine inconnu |
| 429 | `capacity_exceeded` | Toutes les places d'exécution occupées — `Retry-After` fourni |
| 429 | `rate_limited` | Quota utilisateur dépassé |
| 502 | `pipeline_failed` | Le pipeline n'a pas produit de réponse |
| 504 | `pipeline_timeout` | Budget de temps dépassé |
| 500 | `internal_error` | Erreur interne (détail dans les logs, jamais dans la réponse) |

Les erreurs survenant **pendant** un flux SSE prennent la forme
`{"type":"error","errorCode":…}` et n'ont pas de code HTTP (les en-têtes `200`
sont déjà partis).

---

## 5. Exemples

### curl

```bash
curl -N -X POST https://api-fiscal.fiscalonline.fr/v1/chat \
  -H "X-API-Key: $FISCA_API_KEY" \
  -H "X-User-Email: abonne@example.fr" \
  -H "Content-Type: application/json" \
  -d '{"message":"Quel taux de TVA pour la rénovation énergétique ?"}'
```

`-N` désactive le buffering de curl : sans lui, on ne voit pas le streaming.

### useChat (AI SDK v5)

```tsx
const { messages, sendMessage } = useChat({
  transport: new DefaultChatTransport({
    api: '/api/fiscal/chat',        // route Next.js qui relaie vers le proxy
  }),
  onData: (part) => {
    if (part.type === 'data-progress') setProgress(part.data);
    if (part.type === 'data-sources') setSources(part.data.sources);
    if (part.type === 'data-meta')    setMeta(part.data);   // trace_id, conversation_id
  },
});
```

La route Next.js relaie vers le proxy fiscalonline en ajoutant les en-têtes
d'authentification ; le secret partagé ne doit jamais atteindre le navigateur.

---

## 6. Capacité et quotas

- **3 pipelines simultanés** par défaut (`MAX_CONCURRENT_PIPELINES`). Au-delà,
  `429 capacity_exceeded` immédiat plutôt qu'une mise en file : mieux vaut un
  refus explicite qu'un onglet figé cinq minutes.
- **30 questions/heure et 3/minute** par utilisateur par défaut.
- Une **déconnexion du client annule le pipeline** : un utilisateur qui
  rafraîchit ne laisse pas cinq pipelines tourner en arrière-plan.
