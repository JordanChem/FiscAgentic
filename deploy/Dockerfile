# Image de l'API fiscale.
#
# La cible de production est un déploiement systemd sur le serveur du client
# (cf. DEPLOYMENT.md) ; cette image sert au développement, à la CI et à une
# éventuelle bascule vers un hébergement conteneurisé.
#
#   docker build -f deploy/Dockerfile -t fisca-api .
#   docker run --env-file .env -p 8080:8080 fisca-api

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# lxml (via trafilatura) a besoin d'un compilateur ; on l'installe puis on le
# retire pour ne pas embarquer la chaîne de build dans l'image finale.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libxml2-dev libxslt1-dev curl \
 && rm -rf /var/lib/apt/lists/*

# Couche de dépendances séparée : le code change bien plus souvent qu'elles.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && apt-get purge -y build-essential libxml2-dev libxslt1-dev \
 && apt-get autoremove -y

COPY api/ ./api/
COPY pipeline/ ./pipeline/
COPY services/ ./services/
COPY agents/ ./agents/
COPY utils/ ./utils/
COPY legal_scraper.py ./
COPY deploy/gunicorn_conf.py ./deploy/

# Utilisateur non privilégié.
RUN useradd --create-home --uid 10001 fisca && chown -R fisca:fisca /app
USER fisca

ENV API_HOST=0.0.0.0 API_PORT=8080
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8080/health || exit 1

CMD ["gunicorn", "api.main:app", "-c", "deploy/gunicorn_conf.py"]
