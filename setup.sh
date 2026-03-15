#!/bin/bash
set -e
cd "$(dirname "$0")"

if [ ! -d "venv" ]; then
    echo "[INFO] Création du venv Python..."
    python3 -m venv venv
fi

source venv/bin/activate
echo "[INFO] Installation de multiversx-sdk..."
pip install --quiet --upgrade multiversx-sdk
echo "[OK] Environnement prêt. Pour activer : source venv/bin/activate"
