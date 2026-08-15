#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
DB="${DB:-hs300.db}"
PORT="${PORT:-8000}"

if command -v uv >/dev/null 2>&1 && [[ -d "$ROOT_DIR/.venv" ]]; then
  PY="$ROOT_DIR/.venv/bin/python"
else
  PY="python3"
fi

echo "==> 启动每日选股结果服务 http://127.0.0.1:$PORT"
"$PY" "$ROOT_DIR/web_server.py" --db "$DB" --port "$PORT"
