#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
DB="${1:-hs300.db}"
TOP="${2:-20}"
PICK_CMD="${3:-picks}"
EXTRA_SYNC=("${@:4}")

run_cmd() {
  if command -v uv >/dev/null 2>&1; then
    uv run a-finder "$@"
  else
    python3 "$ROOT_DIR/stock_cli.py" "$@"
  fi
}

# 1) 同步 + 选股
bash "$ROOT_DIR/sync_incremental_pick.sh" "$DB" "$TOP" "$PICK_CMD" "${EXTRA_SYNC[@]}"

# 2) 生成今日 plan
PLAN_DATE="$(date +%Y-%m-%d)"
run_cmd plan --db "$DB" --date "$PLAN_DATE"