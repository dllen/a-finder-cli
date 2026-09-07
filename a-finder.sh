#!/usr/bin/env bash
# a-finder.sh — 项目统一入口，聚合了原 10 个 shell 脚本：
#   sync_range / sync_all / sync_incremental_pick / daily_plan / run_picks /
#   build_site_data / run_web / manage / test_ma_backtest / run_local_cron
# 用法: bash a-finder.sh <command> [args...]
# 完整手册: bash a-finder.sh help
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEFAULT_DB="hs300.db"
# 回补起点（系统 go-live 锚点）；与 .github/workflows/fetch-data.yml 保持一致。
BACKFILL_ANCHOR="2026-08-01"
TIERS="50000,100000,150000,200000,250000,300000,350000,400000,450000,500000"

# 优先 uv run a-finder，否则退回 python3 stock_cli.py
run_cmd() {
  if command -v uv >/dev/null 2>&1; then
    uv run a-finder "$@"
  else
    python3 "$ROOT_DIR/stock_cli.py" "$@"
  fi
}

# 跑项目内 python 文件（pick_history / ma_backtest / app / export_json），优先 venv
run_py() {
  if command -v uv >/dev/null 2>&1 && [[ -d "$ROOT_DIR/.venv" ]]; then
    "$ROOT_DIR/.venv/bin/python" "$@"
  else
    python3 "$@"
  fi
}

# ============================ sync ============================

cmd_sync_meta() {
  local db="${1:-$DEFAULT_DB}"
  run_cmd sync-hs300-meta --db "$db"
}

cmd_sync_range() {
  local start="${1:-2025-01-01}"
  local end="${2:-$(date +%F)}"
  local db="${3:-$DEFAULT_DB}"
  run_cmd sync-hs300-range --start "$start" --end "$end" --db "$db" "${@:4}"
}

cmd_sync_all() {
  local start="${1:-2025-01-01}"
  local end="${2:-$(date +%F)}"
  local db="${3:-$DEFAULT_DB}"
  run_cmd sync-hs300-meta --db "$db" || echo "元数据同步失败，继续执行"
  [[ "$start" > "$end" ]] && exit 0
  run_cmd sync-hs300-range --start "$start" --end "$end" --db "$db" "${@:4}"
}

cmd_sync_incremental() {
  # 用法: a-finder.sh sync-incremental [DB] [TOP] [pick-mode] [EXTRA...]
  local db="${1:-$DEFAULT_DB}"
  local top="${2:-10}"
  local pick_mode="${3:-pick-history}"
  # pick-mode 三选一校验（对齐原 sync_incremental_pick.sh）
  if [[ "$pick_mode" != "pick-history" && "$pick_mode" != "picks" && "$pick_mode" != "ma-picks" ]]; then
    echo "pick-mode 仅支持 pick-history / picks / ma-picks" >&2
    exit 1
  fi
  # 增量 K 线：区间 + gap-fill，并发；端点不可达时 fail-fast（10s 超时不重试）
  local start end
  end="$(date +%F)"
  if ! start="$(date -d "$end -1 year" +%F 2>/dev/null)"; then
    start="$(date -v-1y +%F)"
  fi
  run_cmd sync-hs300-meta --db "$db" || echo "元数据同步失败，继续执行"
  run_cmd sync-hs300-range --start "$start" --end "$end" --db "$db" \
    --gap-fill --concurrency 8 --rate 8 --retries 1 || echo "增量行情同步失败，继续执行"
  if [[ "$pick_mode" == "pick-history" ]]; then
    run_py "$ROOT_DIR/pick_history.py" --db "$db" --top "$top"
  else
    run_cmd "$pick_mode" --db "$db" --top "$top" "${@:4}"
  fi
}

# ============================ picks / plan ============================

cmd_picks() {
  # 用法: a-finder.sh picks [--no-sync]   环境变量 DB / TOP
  local db="${DB:-$DEFAULT_DB}"
  local top="${TOP:-10}"
  local sync_flag=""
  if [[ "${1:-}" == "--no-sync" ]]; then
    sync_flag="--no-sync"
  fi
  run_py "$ROOT_DIR/pick_history.py" --db "$db" --top "$top" $sync_flag
}

cmd_plan() {
  # 用法: a-finder.sh plan [DB] [DATE]
  local db="${1:-$DEFAULT_DB}"
  local date="${2:-$(date +%F)}"
  run_cmd plan build --db "$db" --date "$date"
}

cmd_plan_all() {
  # 用法: a-finder.sh plan-all [DB] [TIERS]
  local db="${1:-$DEFAULT_DB}"
  local tiers="${2:-$TIERS}"
  run_cmd plan build-all --db "$db" --strategy linyuan --tiers "$tiers"
}

cmd_daily_plan() {
  # 用法: a-finder.sh daily-plan [DB] [TOP] [pick-cmd] [EXTRA...]
  local db="${1:-$DEFAULT_DB}"
  local top="${2:-20}"
  local pick_cmd="${3:-picks}"
  cmd_sync_incremental "$db" "$top" "$pick_cmd" "${@:4}"
  run_cmd plan build --db "$db" --date "$(date +%F)"
}

# ============================ site ============================

push_site_to_ghpages() {
  local export_dir="$1"
  [[ -f "$export_dir/index.html" ]] || { echo "❌ $export_dir/index.html 缺失，跳过推送" >&2; exit 1; }
  cd "$ROOT_DIR"
  # 临时 git index：force-add 被忽略的 site/，构造 orphan commit 并 force-push 到 gh-pages。
  # 全程不动工作树与当前分支（site/ 是 gitignore 的，永远不进 main）。
  local tmp_index tree commit
  tmp_index="$(mktemp)"
  rm -f "$tmp_index" # mktemp 产生 0 字节文件，git 会误读为损坏 index；删掉让 git 自建空 index
  GIT_INDEX_FILE="$tmp_index" git add -f "$export_dir"
  tree="$(GIT_INDEX_FILE="$tmp_index" git write-tree)"
  rm -f "$tmp_index"
  commit="$(git commit-tree "$tree" -m "update site data $(date +%F-%H:%M)")"
  git push --force origin "$commit:gh-pages"
  echo "✅ gh-pages 已更新 -> $commit"
}

cmd_site() {
  # 用法: a-finder.sh site [DB] [TOP] [OUT] [--push]
  local db="${1:-$DEFAULT_DB}"
  local top="${2:-20}"
  local out="${3:-site}"
  local push=0
  for a in "$@"; do
    if [[ "$a" == "--push" ]]; then push=1; fi
  done

  echo "=== 本地数据生成 ==="
  echo "db=$db top=$top out=$out push=$push"
  echo ""

  echo "[1/5] 增量同步 + 选股..."
  cmd_sync_incremental "$db" "$top" pick-history

  local plan_date; plan_date="$(date +%F)"
  echo "[2/5] 生成 $plan_date 交易计划..."
  run_cmd plan build --db "$db" --date "$plan_date"

  echo "[3/5] 构建 10 个资金档位组合..."
  run_cmd plan build-all --db "$db" --strategy linyuan --tiers "$TIERS"

  echo "[4/5] 回补历史组合（since ${BACKFILL_ANCHOR}）..."
  run_cmd plan build-all --backfill --since "$BACKFILL_ANCHOR" --db "$db"

  echo "[5/5] 导出静态数据到 $out..."
  run_py "$ROOT_DIR/export_json.py" --db "$db" --out "$out"

  cp "$out/index.html" "$out/404.html"
  touch "$out/.nojekyll"
  echo "HTML: $(ls -1 "$out"/*.html 2>/dev/null | wc -l | tr -d ' ')  JSON: $(ls -1 "$out/data/"*.json 2>/dev/null | wc -l | tr -d ' ')"

  if [[ "$push" == "1" ]]; then
    push_site_to_ghpages "$out"
  else
    echo "预览: cd $out && python3 -m http.server 8000"
    echo "推送: bash a-finder.sh site $db $top $out --push"
  fi
}

# ============================ refresh (最近 N 月一键重刷) ============================

cmd_refresh() {
  # 用法: a-finder.sh refresh [MONTHS] [DB] [OUT] [--push]
  # 一键重刷最近 MONTHS 个月：行情补齐 → 计划回补 → 网站导出（可选推送 gh-pages）。
  # 注意：计划回补只覆盖 daily_picks 已有日期；不重算历史选股（避免未来函数偏差）。
  local months="${1:-3}"
  local db="${2:-$DEFAULT_DB}"
  local out="${3:-site}"
  local push=0
  for a in "$@"; do
    if [[ "$a" == "--push" ]]; then push=1; fi
  done

  # 起点：今天 - MONTHS 个月（GNU / BSD date 双兼容）
  local start end
  end="$(date +%F)"
  if ! start="$(date -d "$end -$months months" +%F 2>/dev/null)"; then
    start="$(date -v-${months}m +%F)"
  fi

  echo "=== 最近 $months 个月数据重刷 ==="
  echo "start=$start end=$end db=$db out=$out push=$push"
  echo ""

  echo "[1/4] 刷新元数据..."
  run_cmd sync-hs300-meta --db "$db" || echo "元数据同步失败，继续执行"

  echo "[2/4] 补齐行情 $start ~ $end..."
  run_cmd sync-hs300-range --start "$start" --end "$end" --db "$db" \
    --gap-fill --concurrency 8 --rate 8 --retries 1 || echo "行情补齐失败，继续执行"

  echo "[3/4] 回补交易计划（since ${start}）..."
  run_cmd plan build-all --backfill --since "$start" --db "$db"

  echo "[4/4] 导出网站数据到 $out..."
  run_py "$ROOT_DIR/export_json.py" --db "$db" --out "$out"

  cp "$out/index.html" "$out/404.html"
  touch "$out/.nojekyll"
  echo "HTML: $(ls -1 "$out"/*.html 2>/dev/null | wc -l | tr -d ' ')  JSON: $(ls -1 "$out/data/"*.json 2>/dev/null | wc -l | tr -d ' ')"

  if [[ "$push" == "1" ]]; then
    push_site_to_ghpages "$out"
  else
    echo "预览: cd $out && python3 -m http.server 8000"
    echo "推送: bash a-finder.sh refresh $months $db $out --push"
  fi
}

# ============================ web (Flask app.py) ============================

cmd_web() {
  # 用法: a-finder.sh web {start|stop|restart|status}   环境变量 DB / PORT / TOP
  local db="${DB:-$DEFAULT_DB}"
  local port="${PORT:-8000}"
  local top="${TOP:-10}"
  local pid_file="$ROOT_DIR/.web.pid"
  local log_file="$ROOT_DIR/web.log"
  local py
  if command -v uv >/dev/null 2>&1 && [[ -d "$ROOT_DIR/.venv" ]]; then
    py="$ROOT_DIR/.venv/bin/python"
  else
    py="python3"
  fi
  local cmd="${1:-start}"
  case "$cmd" in
    start)
      local cur; cur="$(lsof -ti ":$port" 2>/dev/null || true)"
      if [[ -n "$cur" ]]; then echo "already running (pid $cur) http://127.0.0.1:$port"; exit 0; fi
      nohup "$py" "$ROOT_DIR/app.py" --db "$db" --port "$port" --top "$top" >>"$log_file" 2>&1 &
      echo $! >"$pid_file"
      echo "started http://127.0.0.1:$port (pid $(cat "$pid_file"))"
      ;;
    stop)
      local pid; pid="$(lsof -ti ":$port" 2>/dev/null || true)"
      if [[ -n "$pid" ]]; then kill "$pid"; echo "stopped"; else echo "not running"; fi
      rm -f "$pid_file"
      ;;
    restart) cmd_web stop; sleep 0.2; cmd_web start ;;
    status)
      local pid; pid="$(lsof -ti ":$port" 2>/dev/null || true)"
      if [[ -n "$pid" ]]; then echo "running (pid $pid) http://127.0.0.1:$port"; else echo "stopped"; fi
      ;;
    *) echo "usage: a-finder.sh web {start|stop|restart|status}"; exit 1 ;;
  esac
}

# ============================ daemon (a-finder CLI 后台) ============================

cmd_daemon() {
  # 用法: a-finder.sh daemon {start|stop|restart|status} [cli-args...]
  local pid_file="$ROOT_DIR/.a-finder.pid"
  local log_file="$ROOT_DIR/a-finder.log"
  local cmd="${1:-}"
  shift || true
  case "$cmd" in
    start)
      if [[ -f "$pid_file" ]] && kill -0 "$(cat "$pid_file")" >/dev/null 2>&1; then
        echo "already running"; exit 0
      fi
      local args=("$@")
      if [[ ${#args[@]} -eq 0 ]]; then args=("overview"); fi
      run_cmd "${args[@]}" >>"$log_file" 2>&1 &
      echo $! >"$pid_file"
      echo "started"
      ;;
    stop)
      if [[ -f "$pid_file" ]]; then
        local pid; pid="$(cat "$pid_file")"
        if [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1; then
          kill "$pid"; wait "$pid" >/dev/null 2>&1 || true; echo "stopped"
        else
          echo "not running"
        fi
        rm -f "$pid_file"
      else
        echo "not running"
      fi
      ;;
    restart) cmd_daemon stop; cmd_daemon start "$@" ;;
    status)
      if [[ -f "$pid_file" ]] && kill -0 "$(cat "$pid_file")" >/dev/null 2>&1; then
        echo "running"
      else
        echo "stopped"
      fi
      ;;
    *) echo "usage: a-finder.sh daemon {start|stop|restart|status} [cli-args]"; exit 1 ;;
  esac
}

# ============================ backtest ============================

cmd_backtest() {
  # 用法: a-finder.sh backtest [DB] [TOP] [DAYS] [EXTRA...]
  local db="${1:-$DEFAULT_DB}"
  local top="${2:-10}"
  local days="${3:-240}"
  run_py "$ROOT_DIR/ma_backtest.py" --db "$db" --top "$top" --days "$days" "${@:4}"
}

# ============================ cron (launchd 入口) ============================

cmd_cron() {
  # launchd 调用：工作日 15:30 触发；周末（date +%u >= 6）跳过。
  if [[ "$(date +%u)" -ge 6 ]]; then
    echo "$(date +%F) 周末，跳过 site data 构建"
    exit 0
  fi
  cmd_site "$DEFAULT_DB" 20 site --push
}

# ============================ help ============================

print_help() {
  cat <<'EOF'
a-finder.sh — A-share 数据 / 选股 / 计划 / 网站 / 服务 统一入口

用法:
  bash a-finder.sh <command> [args...]
  bash a-finder.sh help                    # 本手册

──────── 同步 ────────
  sync-meta [DB]
      同步沪深300成分股元数据。
  sync-range START END [DB] [EXTRA...]
      区间同步日 K（默认 START=2025-01-01, END=今天）。
      EXTRA 透传给 a-finder，例如 --gap-fill --concurrency 6 --rate 8。
  sync-all START END [DB] [EXTRA...]
      meta + range（默认 END=今天）。
  sync-incremental [DB] [TOP] [pick-mode] [EXTRA...]
      增量同步（meta + range --gap-fill 并发）+ 选股落库。
      pick-mode: pick-history（写 daily_picks）/ picks / ma-picks。

──────── 选股 / 计划 ────────
  picks [--no-sync]            环境变量 DB / TOP。pick_history.py 落库。
  plan [DB] [DATE]             生成单 portfolio 交易计划（plan build）。
  plan-all [DB] [TIERS]        批量生成多档位组合（默认 5W-50W 10 档）。
  daily-plan [DB] [TOP] [pick-cmd] [EXTRA...]
      sync-incremental + plan build，一次性日流水。

──────── 网站 ────────
  site [DB] [TOP] [OUT] [--push]
      全流程：sync-incremental → plan build → build-all → backfill
      → export_json。--push 把 site/ orphan-push 到 gh-pages。
  refresh [MONTHS] [DB] [OUT] [--push]
      最近 MONTHS 个月（默认 3）一键重刷：行情补齐 → 计划回补 → 网站导出。
      计划回补只覆盖 daily_picks 已有日期；不重算历史选股（避免未来函数偏差）。
  web {start|stop|restart|status}    环境变量 DB / PORT / TOP。Flask app.py。
  daemon {start|stop|restart|status} [cli-args]
      后台跑 a-finder 子命令（默认 overview），PID 文件管理。
  cron                          launchd 入口：工作日 15:30 跑 site --push。

──────── 回测 ────────
  backtest [DB] [TOP] [DAYS] [EXTRA...]
      ma_backtest.py（默认 top=10 days=240）。

──────── 示例 ────────
  bash a-finder.sh sync-range 2025-01-01 2026-09-07 hs300.db --gap-fill
  bash a-finder.sh sync-incremental hs300.db 20 pick-history
  bash a-finder.sh picks --no-sync
  bash a-finder.sh plan hs300.db 2026-09-07
  bash a-finder.sh site hs300.db 20 site --push
  bash a-finder.sh refresh 3 hs300.db site --push
  DB=hs300.db PORT=8080 TOP=20 bash a-finder.sh web start
  bash a-finder.sh daemon start overview
  bash a-finder.sh backtest hs300.db 10 240 --tune

环境变量: DB (默认 hs300.db) / TOP / PORT (默认 8000)
回补锚点 BACKFILL_ANCHOR=2026-08-01（见脚本顶部，与 fetch-data.yml 同步）
EOF
}

# ============================ dispatcher ============================

case "${1:-help}" in
  sync-meta)       shift; cmd_sync_meta "$@" ;;
  sync-range)      shift; cmd_sync_range "$@" ;;
  sync-all)        shift; cmd_sync_all "$@" ;;
  sync-incremental) shift; cmd_sync_incremental "$@" ;;
  picks)           shift; cmd_picks "$@" ;;
  plan)            shift; cmd_plan "$@" ;;
  plan-all)        shift; cmd_plan_all "$@" ;;
  daily-plan)      shift; cmd_daily_plan "$@" ;;
  site)            shift; cmd_site "$@" ;;
  refresh)         shift; cmd_refresh "$@" ;;
  web)             shift; cmd_web "$@" ;;
  daemon)          shift; cmd_daemon "$@" ;;
  backtest)        shift; cmd_backtest "$@" ;;
  cron)            shift; cmd_cron "$@" ;;
  help|--help|-h)  print_help ;;
  *) echo "unknown command: ${1:-}" >&2; echo "run 'bash a-finder.sh help' for usage" >&2; exit 1 ;;
esac
