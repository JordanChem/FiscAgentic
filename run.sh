#!/bin/bash
# Démarrage local — API FastAPI (défaut) ou UI Streamlit de debug.
#
#   ./run.sh                    # API sur http://127.0.0.1:8080
#   API_PORT=8090 ./run.sh      # …sur un autre port
#   ./run.sh streamlit          # UI de debug sur http://localhost:8501
set -euo pipefail

MODE="${1:-api}"

if [ -d "venv" ]; then
    echo "✅ Activation de l'environnement virtuel..."
    # shellcheck disable=SC1091
    source venv/bin/activate
else
    echo "⚠️  Environnement virtuel non trouvé. Créez-en un avec : python3 -m venv venv"
fi

# Le .env n'est PAS parsé ici : `api/main.py` et `streamlit_app.py` appellent
# `load_dotenv()`. python-dotenv tolère les formats que bash refuse (`CLÉ = valeur`,
# valeurs avec espaces, guillemets), alors qu'un `source .env` échouerait dessus.
[ -f .env ] || echo "⚠️  Fichier .env non trouvé — voir .env.example."

# Contrôle des secrets via le même code que l'application (utils/api_keys).
python - <<'PY'
from dotenv import load_dotenv
# Chemin explicite : `load_dotenv()` sans argument remonte la pile d'appel pour
# localiser le .env, ce qui échoue quand le script est lu sur l'entrée standard.
load_dotenv(".env")
from utils.api_keys import get_api_keys, get_secret

manquantes = [n for n, v in zip(("OPENAI_API_KEY", "GOOGLE_API_KEY", "SERPAPI_API_KEY"),
                                get_api_keys()) if not v]
if not get_secret("SUPABASE_URL") or not get_secret("SUPABASE_KEY"):
    manquantes.append("SUPABASE_URL/SUPABASE_KEY")
print(f"⚠️  Secrets manquants : {', '.join(manquantes)}" if manquantes
      else "✅ Secrets présents.")
PY

case "$MODE" in
    api)
        HOST="${API_HOST:-127.0.0.1}"
        PORT="${API_PORT:-8080}"

        if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
            echo "❌ Le port $PORT est déjà utilisé par :" >&2
            lsof -nP -iTCP:"$PORT" -sTCP:LISTEN | tail -n +2 | awk '{print "     "$1" (pid "$2")"}' >&2
            echo "   Relancez avec un autre port :  API_PORT=8090 ./run.sh" >&2
            exit 1
        fi

        if [ -z "${API_SHARED_SECRET:-}" ]; then
            export API_SHARED_SECRET="dev-secret-local"
            echo "⚠️  API_SHARED_SECRET absent — secret de développement « dev-secret-local »."
        fi
        echo "🌐 API sur http://$HOST:$PORT   (Swagger : http://$HOST:$PORT/docs)"
        exec uvicorn api.main:app --reload --host "$HOST" --port "$PORT"
        ;;
    streamlit)
        echo "🌐 UI de debug sur http://localhost:8501"
        exec streamlit run streamlit_app.py
        ;;
    *)
        echo "Usage : ./run.sh [api|streamlit]" >&2
        exit 1
        ;;
esac
