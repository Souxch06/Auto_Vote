#!/usr/bin/env bash
# Lance le bot en boucle (ou --once / detect / status via "$@").
#
#   ./run.sh                 # boucle 24h/24
#   ./run.sh --once          # une passe puis arrêt
#   ./run.sh detect --save   # détecte les sites de ta page serveur
#
# Le venv est créé à la racine du repo (partagé avec le sidecar solveur).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
VENV="${VENV:-$ROOT/.venv}"

if [[ ! -x "$VENV/bin/python" ]]; then
  echo "[run.sh] création du venv $VENV"
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install --upgrade pip
  echo "[run.sh] installation des dépendances (solveur + bot)"
  "$VENV/bin/pip" install -r "$ROOT/requirements.txt"
  [[ -f "$HERE/requirements.txt" ]] && "$VENV/bin/pip" install -r "$HERE/requirements.txt"
fi

# Le bot doit importer le package dans ce répertoire
cd "$HERE"
exec "$VENV/bin/python" -m autovote_bot run --config "${CONFIG:-config.json}" "$@"
