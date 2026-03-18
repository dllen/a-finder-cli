#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
DB="${1:-hs300.db}"
TOP="${2:-10}"
PICK_CMD="${3:-ma-picks}"
EXTRA_SYNC=("${@:4}")

run_cmd() {
  if command -v uv >/dev/null 2>&1; then
    uv run a-finder "$@"
  else
    python3 "$ROOT_DIR/stock_cli.py" "$@"
  fi
}

if ! [[ "$TOP" =~ ^[1-9][0-9]*$ ]]; then
  echo "top 必须为正整数"
  exit 1
fi

if [[ "$PICK_CMD" != "picks" && "$PICK_CMD" != "ma-picks" ]]; then
  echo "pick_cmd 仅支持 picks 或 ma-picks"
  exit 1
fi

if ! run_cmd sync-hs300-meta --db "$DB"; then
  echo "元数据同步失败，继续执行增量行情同步"
fi
if ! run_cmd sync-hs300 --db "$DB" --mode incremental "${EXTRA_SYNC[@]}"; then
  echo "增量行情同步失败，继续执行选股"
fi
run_cmd "$PICK_CMD" --db "$DB" --top "$TOP"
