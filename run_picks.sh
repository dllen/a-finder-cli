#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
DB="${DB:-hs300.db}"
TOP="${TOP:-10}"
PORT="${PORT:-8000}"
SYNC_FLAG=""

if [[ "${1:-}" == "--no-sync" ]]; then
  SYNC_FLAG="--no-sync"
fi

if command -v uv >/dev/null 2>&1 && [[ -d "$ROOT_DIR/.venv" ]]; then
  PY="$ROOT_DIR/.venv/bin/python"
else
  PY="python3"
fi

echo "==> 选股并落库 (db=$DB top=$TOP)"
"$PY" "$ROOT_DIR/pick_history.py" --db "$DB" --top "$TOP" $SYNC_FLAG

echo "==> 启动本地服务 http://127.0.0.1:$PORT"
"$PY" "$ROOT_DIR/web_server.py" --db "$DB" --port "$PORT"
