#!/bin/bash

# Script pour arrêter l'API proprement

API_DIR="/Users/jeen.nico/Desktop/BCRapp/api"
PID_FILE="$API_DIR/api.pid"

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if ps -p $PID > /dev/null 2>&1; then
        echo "🛑 Arrêt de l'API (PID: $PID)..."
        kill $PID
        rm "$PID_FILE"
        echo "✅ API arrêtée"
    else
        echo "⚠️  Processus introuvable, nettoyage du fichier PID"
        rm "$PID_FILE"
    fi
else
    echo "ℹ️  API non lancée"
fi

# Tuer tous les processus Python qui écoutent sur le port 8000
PIDS=$(lsof -ti :8000)
if [ ! -z "$PIDS" ]; then
    echo "🧹 Nettoyage des processus sur le port 8000..."
    kill -9 $PIDS 2>/dev/null
    echo "✅ Port 8000 libéré"
fi

exit 0
