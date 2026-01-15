#!/bin/bash
# Script de démarrage de l'application Streamlit

echo "🚀 Démarrage de l'Assistant Fiscal Intelligent..."
echo ""

# Activation de l'environnement virtuel si présent
if [ -d "venv" ]; then
    echo "✅ Activation de l'environnement virtuel..."
    source venv/bin/activate
else
    echo "⚠️  Environnement virtuel non trouvé. Créez-en un avec : python3 -m venv venv"
fi

# Vérification des variables d'environnement
if [ -f .env ]; then
    echo "✅ Fichier .env trouvé, chargement des variables d'environnement..."
    export $(cat .env | grep -v '^#' | xargs)
else
    echo "⚠️  Fichier .env non trouvé. Utilisez les secrets Streamlit ou définissez les variables d'environnement."
fi

# Vérification des clés API
if [ -z "$OPENAI_API_KEY" ] || [ -z "$GOOGLE_API_KEY" ] || [ -z "$SERPAPI_API_KEY" ]; then
    echo "⚠️  Attention : Certaines clés API ne sont pas définies."
    echo "   Assurez-vous de configurer :"
    echo "   - OPENAI_API_KEY"
    echo "   - GOOGLE_API_KEY"
    echo "   - SERPAPI_API_KEY"
    echo ""
fi

# Démarrage de Streamlit
echo "🌐 Lancement de l'application..."
streamlit run app.py