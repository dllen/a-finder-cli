#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
START="${1:-2025-01-01}"
END="${2:-$(date +"%Y-%m-%d")}"
DB="${3:-hs300.db}"
EXTRA=("${@:4}")

run_cmd() {
  if command -v uv >/dev/null 2>&1; then
    uv run a-finder "$@"
  else
    python3 "$ROOT_DIR/stock_cli.py" "$@"
  fi
}

run_cmd sync-hs300-meta --db "$DB"
if [[ "$START" > "$END" ]]; then
  exit 0
fi
run_cmd sync-hs300-range --start "$START" --end "$END" --db "$DB" "${EXTRA[@]}"
