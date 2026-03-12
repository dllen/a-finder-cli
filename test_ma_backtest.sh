#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
DB="${1:-hs300.db}"
TOP="${2:-10}"
DAYS="${3:-240}"
EXTRA=("${@:4}")

if command -v uv >/dev/null 2>&1; then
  uv run python3 "$ROOT_DIR/ma_backtest.py" --db "$DB" --top "$TOP" --days "$DAYS" "${EXTRA[@]}"
else
  python3 "$ROOT_DIR/ma_backtest.py" --db "$DB" --top "$TOP" --days "$DAYS" "${EXTRA[@]}"
fi
