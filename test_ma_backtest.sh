#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
DB="${1:-hs300.db}"
TOP="${2:-10}"
DAYS="${3:-240}"

if command -v uv >/dev/null 2>&1; then
  if [[ $# -ge 4 ]]; then
    uv run python3 "$ROOT_DIR/ma_backtest.py" --db "$DB" --top "$TOP" --days "$DAYS" "${@:4}"
  else
    uv run python3 "$ROOT_DIR/ma_backtest.py" --db "$DB" --top "$TOP" --days "$DAYS"
  fi
else
  if [[ $# -ge 4 ]]; then
    python3 "$ROOT_DIR/ma_backtest.py" --db "$DB" --top "$TOP" --days "$DAYS" "${@:4}"
  else
    python3 "$ROOT_DIR/ma_backtest.py" --db "$DB" --top "$TOP" --days "$DAYS"
  fi
fi
