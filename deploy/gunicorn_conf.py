"""
Configuration gunicorn du service.

    gunicorn api.main:app -c deploy/gunicorn_conf.py

Points non négociables :
- worker **uvicorn** (ASGI) : le service streame en SSE ;
- `timeout` très long : une requête légitime dure 1 à 5 minutes ;
- **peu** de workers : chaque requête ouvre déjà une douzaine de threads
  (agents spécialisés, recherche, scraping).
"""
import multiprocessing
import os

bind = f"{os.getenv('API_HOST', '127.0.0.1')}:{os.getenv('API_PORT', '8080')}"
worker_class = "uvicorn.workers.UvicornWorker"

# 2 workers par défaut : au-delà, la mémoire (≈500 Mo/worker avec litellm +
# trafilatura) et le nombre de threads deviennent le facteur limitant, pas le CPU.
workers = int(os.getenv("WEB_CONCURRENCY", min(2, multiprocessing.cpu_count())))

# Le pipeline dure plusieurs minutes : sans ce réglage, gunicorn tuerait le
# worker en pleine rédaction (défaut : 30 s).
timeout = int(os.getenv("GUNICORN_TIMEOUT", "900"))
graceful_timeout = int(os.getenv("GUNICORN_GRACEFUL_TIMEOUT", "120"))
keepalive = 75

max_requests = 200          # recycle les workers : borne les fuites de litellm
max_requests_jitter = 50

accesslog = "-"
errorlog = "-"
loglevel = os.getenv("LOG_LEVEL", "info").lower()
# Les logs applicatifs sont déjà en JSON structuré (api/logging_conf.py) ;
# on garde le journal d'accès gunicorn minimal pour ne pas doublonner.
access_log_format = '%(h)s "%(r)s" %(s)s %(b)s %(D)sµs'

proc_name = "fisca-api"
