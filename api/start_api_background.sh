#!/bin/bash

# Script pour lancer l'API en arrière-plan si elle n'est pas déjà lancée

API_DIR="/Users/jeen.nico/Desktop/BCRapp/api"
PID_FILE="$API_DIR/api.pid"
LOG_FILE="$API_DIR/api.log"

# Fonction pour vérifier si l'API est déjà lancée
is_api_running() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p $PID > /dev/null 2>&1; then
            # Vérifier que c'est bien notre API
            if lsof -Pi :8000 -sTCP:LISTEN -t > /dev/null 2>&1; then
                return 0
            fi
        fi
    fi
    return 1
}

# Vérifier si l'API est déjà lancée
if is_api_running; then
    echo "✅ API déjà lancée"
    exit 0
fi

echo "🚀 Lancement de l'API BCR..."

cd "$API_DIR"

# Créer venv si nécessaire
if [ ! -d "venv" ]; then
    echo "📦 Création environnement virtuel..."
    python3 -m venv venv
    source venv/bin/activate
    pip install -q -r requirements.txt
else
    source venv/bin/activate
fi

# Lancer l'API en arrière-plan
nohup python main.py > "$LOG_FILE" 2>&1 &
API_PID=$!

# Sauvegarder le PID
echo $API_PID > "$PID_FILE"

echo "✅ API lancée (PID: $API_PID)"
echo "📝 Logs: $LOG_FILE"

# Attendre que l'API soit prête
sleep 3

# Vérifier que l'API répond
if curl -s http://localhost:8000/health > /dev/null 2>&1; then
    echo "✅ API opérationnelle sur http://localhost:8000"
else
    echo "⚠️  API démarrée mais pas encore prête"
fi

exit 0
