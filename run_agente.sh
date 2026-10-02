#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

if [ -d ".venv" ]; then
    PYTHON_BIN=".venv/bin/python3"
else
    PYTHON_BIN="python3"
fi

export PYTHONPATH="$DIR/agente:$PYTHONPATH"
exec "$PYTHON_BIN" "$DIR/agente/main.py"
