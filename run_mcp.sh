#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

if [ -z "$REQUEST_STATE_SECRET" ]; then
    if [ -f ".env" ]; then
        export $(grep -v '^#' .env | xargs)
    fi
fi

if [ -z "$REQUEST_STATE_SECRET" ]; then
    echo "[run_mcp.sh] REQUEST_STATE_SECRET nao definido. Gerando chave temporaria de 32 bytes..."
    export REQUEST_STATE_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
fi

if [ -d ".venv" ]; then
    PYTHON_BIN=".venv/bin/python3"
else
    PYTHON_BIN="python3"
fi

export PYTHONPATH="$DIR/servidor-mcp:$PYTHONPATH"
exec "$PYTHON_BIN" "$DIR/servidor-mcp/main.py"
