# Déploiement — API Assistant Fiscal

Cible : serveur fiscalonline, service systemd derrière nginx.
Le contrat consommé par le front est décrit dans [../docs/API.md](../docs/API.md).

---

## 1. Prérequis

- Python **3.12+**
- ~2 Go de RAM libres (≈500 Mo par worker gunicorn, litellm + trafilatura étant
  les plus gourmands), 2 vCPU
- Accès sortant HTTPS vers : les providers LLM (OpenAI, Google, Anthropic),
  `serpapi.com`, `justicelibre.org`, `api.fiscalonline.com`, `firecrawl.dev`,
  Supabase, Langfuse
- nginx (ou tout reverse-proxy sachant désactiver le buffering)

---

## 2. Installation

```bash
sudo useradd --system --create-home --home-dir /opt/fisca-api fisca
sudo -u fisca git clone <repo> /opt/fisca-api
cd /opt/fisca-api

sudo -u fisca python3.12 -m venv venv
sudo -u fisca venv/bin/pip install -r requirements.txt   # runtime seul, sans Streamlit
```

`requirements.txt` ne contient **que** le runtime de l'API. `requirements-dev.txt`
(Streamlit, deepeval, pytest) n'a rien à faire sur le serveur de production.

---

## 3. Configuration

```bash
sudo install -d -m 750 -o root -g fisca /etc/fisca-api
sudo cp .env.example /etc/fisca-api/env
sudo chmod 640 /etc/fisca-api/env
sudo chown root:fisca /etc/fisca-api/env
sudo -e /etc/fisca-api/env
```

Variables indispensables (le service refuse de démarrer en `ENVIRONMENT=prod`
si l'une manque) :

| Variable | Valeur |
|---|---|
| `API_SHARED_SECRET` | `openssl rand -hex 32` — à communiquer au développeur du proxy |
| `OPENAI_API_KEY`, `GOOGLE_API_KEY`, `ANTHROPIC_API_KEY` | providers LLM |
| `SERPAPI_API_KEY` | recherche web |
| `SUPABASE_URL`, `SUPABASE_KEY` | historique et feedbacks |
| `ENVIRONMENT` | `prod` (désactive aussi `/docs`) |

Optionnelles mais recommandées : `FISCALONLINE_TOKEN` (articles internes),
`FIRECRAWL_API_KEY` (repli de scraping), `LANGFUSE_*` (observabilité).
Le détail complet est commenté dans `.env.example`.

### Schéma Supabase attendu

Tables `conversations` (`id` texte PK, `title`, `messages` jsonb,
`contexte_conversation` jsonb, `message_count` int, `user_email` texte,
`created_at`, `updated_at`, `deleted_at` nullable) et `feedbacks` (`question`,
`answer`, `rating` int, `comment`, `sources_count` int, `is_follow_up` bool,
`user_email`).

> ⚠️ L'isolation entre abonnés repose aujourd'hui sur le filtre applicatif
> `user_email`, pas sur des politiques RLS. Activer la RLS côté Supabase en
> défense en profondeur est recommandé.

---

## 4. Service

```bash
sudo cp deploy/fisca-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fisca-api
sudo systemctl status fisca-api
curl -s http://127.0.0.1:8080/health
```

Le service écoute sur `127.0.0.1` : il n'est joignable que par le reverse-proxy.

---

## 5. nginx

```bash
sudo cp deploy/nginx.conf.example /etc/nginx/sites-available/fisca-api
sudo ln -s /etc/nginx/sites-available/fisca-api /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

Trois directives conditionnent le streaming — sans elles la réponse arrive d'un
seul bloc après plusieurs minutes :

```nginx
proxy_buffering          off;
chunked_transfer_encoding on;
proxy_read_timeout       600s;
```

---

## 6. Recette après déploiement

```bash
KEY=…   # API_SHARED_SECRET
BASE=https://api-fiscal.fiscalonline.fr

# 1. Disponibilité
curl -s $BASE/health
curl -s $BASE/ready -H "X-API-Key: $KEY" -H "X-User-Email: recette@fiscalonline.fr"

# 2. Authentification : doit répondre 401
curl -s -o /dev/null -w '%{http_code}\n' -X POST $BASE/v1/chat \
  -H 'Content-Type: application/json' -d '{"message":"test"}'

# 3. Streaming réel — à travers nginx (c'est là que proxy_buffering mord).
#    Les trames data-progress doivent arriver AU FUR ET À MESURE, pas d'un bloc.
curl -N -X POST $BASE/v1/chat \
  -H "X-API-Key: $KEY" -H "X-User-Email: recette@fiscalonline.fr" \
  -H 'Content-Type: application/json' \
  -d '{"message":"Quel taux de TVA pour la rénovation énergétique ?"}'
```

À vérifier sur le flux :
- les trames `data-progress` précèdent le premier `text-delta` ;
- le texte est du **markdown** — aucun `{`, aucun `"reponse_redigee"` ;
- `data-meta` porte un `trace_id` et un `cost_usd` **non nuls**.

> **Le contrôle qui compte** : ouvrir la trace Langfuse correspondante et vérifier
> qu'elle contient bien la vingtaine de générations avec un coût agrégé non nul.
> Une trace vide signale que le contexte d'exécution n'a pas suivi le pipeline —
> une panne parfaitement silencieuse par ailleurs (cf. l'avertissement en tête de
> `api/runner.py`).

Puis :

```bash
# 4. Annulation : couper le curl en cours de flux, vérifier dans le journal
#    « Pipeline interrompu » puis la libération de la place.
sudo journalctl -u fisca-api -f | grep -i "interrompu\|cancelled"

# 5. Capacité : 4 requêtes simultanées avec MAX_CONCURRENT_PIPELINES=3
#    → la 4e doit répondre 429 capacity_exceeded, pas rester en attente.
```

---

## 7. Exploitation

```bash
sudo journalctl -u fisca-api -f                     # logs (JSON, une ligne par événement)
sudo journalctl -u fisca-api | grep '"level":"ERROR"'
sudo journalctl -u fisca-api | grep '"request_id":"a1b2"'   # tracer une requête
sudo systemctl restart fisca-api                    # drain gracieux (jusqu'à 180 s)
```

Chaque ligne de log porte `request_id`, `user_id`, `conversation_id` et
`trace_id` : partant d'une plainte utilisateur, on retrouve la trace Langfuse, et
inversement.

### Rotation du secret partagé

`API_SHARED_SECRET` accepte plusieurs valeurs séparées par des virgules, ce qui
permet de tourner sans coupure :

1. `API_SHARED_SECRET=ancien,nouveau` puis `systemctl restart fisca-api`
2. basculer le proxy sur `nouveau`
3. `API_SHARED_SECRET=nouveau` puis redémarrer

### Réglages de charge

| Variable | Défaut | Effet |
|---|---|---|
| `MAX_CONCURRENT_PIPELINES` | 3 | Places d'exécution. Chaque pipeline ouvre ~12 threads : ne pas augmenter sans réduire d'abord `SEARCH_MAX_WORKERS` / `SCRAPE_MAX_WORKERS`. |
| `WEB_CONCURRENCY` | 2 | Workers gunicorn. ~500 Mo de RSS chacun. |
| `PIPELINE_DEADLINE_S` | 600 | Budget d'une requête. Un run nominal tourne autour de 200-300 s. |
| `RATE_LIMIT_PER_HOUR` | 30 | Quota par utilisateur. En mémoire, donc **par worker** : diviser par `WEB_CONCURRENCY` pour le quota effectif, ou passer à Redis si le service est répliqué. |

### Rollback

```bash
cd /opt/fisca-api
sudo -u fisca git checkout <tag-précédent>
sudo -u fisca venv/bin/pip install -r requirements.txt
sudo systemctl restart fisca-api
```

---

## 8. Docker (alternative)

```bash
docker build -f deploy/Dockerfile -t fisca-api .
docker run --env-file /etc/fisca-api/env -p 127.0.0.1:8080:8080 fisca-api
```

Même recette qu'en section 6 ; l'image embarque un healthcheck sur `/health`.
