#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
DB="${1:-hs300.db}"
TOP="${2:-10}"
PICK_MODE="${3:-pick-history}"
EXTRA_SYNC=("${@:4}")

run_sync() {
  run_cmd sync-hs300-meta --db "$DB" || echo "元数据同步失败，继续执行"
  run_cmd sync-hs300 --db "$DB" --mode incremental || echo "增量行情同步失败，继续执行"
}

run_picks_to_db() {
  # pick_history.py 会把选股结果写入 daily_picks 表，供 export_json.py 使用
  python3 "$ROOT_DIR/pick_history.py" --db "$DB" --top "$TOP"
}

run_cmd() {
  if command -v uv >/dev/null 2>&1; then
    uv run a-finder "$@"
  else
    python3 "$ROOT_DIR/stock_cli.py" "$@"
  fi
}

if [[ "$PICK_MODE" != "pick-history" && "$PICK_MODE" != "picks" && "$PICK_MODE" != "ma-picks" ]]; then
  echo "pick_mode 仅支持 pick-history / picks / ma-picks"
  exit 1
fi

if [[ "$PICK_MODE" == "pick-history" ]]; then
  run_sync
  run_picks_to_db
else
  run_sync
  run_cmd "$PICK_MODE" --db "$DB" --top "$TOP"
fi
