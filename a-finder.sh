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
  # 优先用项目 venv 的解释器直接跑 stock_cli.py：
  # `uv run` 需要写 ~/.cache/uv，在受限环境（CI 沙箱/只读 HOME）会直接失败，
  # 曾导致整条 site 流水线在第 2 步就中断（2026-10-27 本地兜底因此没能推送）。
  if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    "$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/stock_cli.py" "$@"
  elif command -v uv >/dev/null 2>&1; then
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

# 跑一段内联 python（run_py 只接受脚本路径，不支持 -c）
run_pyc() {
  if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    "$ROOT_DIR/.venv/bin/python" -c "$1"
  else
    python3 -c "$1"
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
  # 可通过环境变量覆盖，供 CI 使用更短的窗口（更少请求 = 更少限流）：
  #   SYNC_DAYS=45 SYNC_RETRIES=2 SYNC_CONCURRENCY=6 SYNC_RATE=6
  #   SYNC_END_DATE=2026-10-26（默认今天；CI 显式传北京日期避免 UTC 跨日）
  local start end days="${SYNC_DAYS:-365}" retries="${SYNC_RETRIES:-1}"
  local concurrency="${SYNC_CONCURRENCY:-8}" rate="${SYNC_RATE:-8}"
  end="${SYNC_END_DATE:-$(date +%F)}"
  if ! start="$(date -d "$end -$days days" +%F 2>/dev/null)"; then
    start="$(date -v-"${days}"d -j -f "%Y-%m-%d" "$end" +%F 2>/dev/null)"
  fi
  if [[ -z "$start" ]]; then
    # 兜底：按秒换算
    start="$(python3 -c "import datetime as d,sys;print((d.date.fromisoformat('$end')-d.timedelta(days=$days)).isoformat())")"
  fi
  # 变量必须加花括号：macOS 自带 bash 3.2 会把紧跟的中文括号吞进变量名
  # （报 unbound variable，CI 的 bash 5 不会，但本地调试会踩）
  echo "增量区间: ${start} ~ ${end}（${days} 天, 并发 ${concurrency}, 重试 ${retries}）"
  run_cmd sync-hs300-meta --db "$db" || echo "元数据同步失败，继续执行"
  local sync_rc=0
  run_cmd sync-hs300-range --start "$start" --end "$end" --db "$db" \
    --gap-fill --concurrency "$concurrency" --rate "$rate" --retries "$retries" || sync_rc=$?
  if [[ "$sync_rc" -ne 0 ]]; then
    echo "增量行情同步失败（exit ${sync_rc}），继续执行后续步骤" >&2
  fi
  if [[ "$pick_mode" == "pick-history" ]]; then
    # 行情已在上一步同步完，这里 --no-sync 避免 pick_history 内部再跑一遍增量同步
    # （那是第二次全量请求，纯浪费配额、提升被限流概率）
    run_py "$ROOT_DIR/pick_history.py" --db "$db" --top "$top" --no-sync
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

push_site_to_ghpages() {
  local export_dir="$1"
  [[ -f "$export_dir/index.html" ]] || { echo "❌ $export_dir/index.html 缺失，跳过推送" >&2; exit 1; }
  cd "$ROOT_DIR"
  # 临时 git index：force-add 被忽略的 site/，构造 orphan commit 并 force-push 到 gh-pages。
  # 全程不动工作树与当前分支（site/ 是 gitignore 的，永远不进 main）。
  local tmp_index tree commit
  tmp_index="$(mktemp)"
  rm -f "$tmp_index" # mktemp 产生 0 字节文件，git 会误读为损坏 index；删掉让 git 自建空 index
  rm -rf "$export_dir/.git" # site/ 若残留嵌套 .git，git add 会把 site 当 submodule（gitlink）；删掉确保打包真实文件
  # 用 --work-tree 把 site/ 当作工作树根，把「内容」加到 index 根（对齐 CI 的
  # peaceiris publish_dir:./site），而非把 site/ 当子目录 —— 否则线上站点落在 /site/。
  GIT_INDEX_FILE="$tmp_index" git --work-tree="$export_dir" add -f .
  tree="$(GIT_INDEX_FILE="$tmp_index" git write-tree)"
  rm -f "$tmp_index"
  commit="$(git commit-tree "$tree" -m "update site data $(date +%F-%H:%M)")"
  git push --force origin "${commit}:gh-pages"
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

  # 计划日期对齐**库里真正的行情最新日**，而不是硬编码今天：
  # 同步失败时（数据源限流 / 当天数据未出）选股会落在库里的最大交易日，
  # 若此时按今天生成计划就查不到选股行 → 空计划（不报错，最难查的一类故障）。
  # CI 的 deploy-site.yml 早已这么修过（DATA_DATE），这里对齐。
  local plan_date
  # 用只读 URI 连接：库文件不存在时不新建空库（open_db 会新建空库，
  # 那样「有没有数据」永远查不出问题）。异常一律吞成空串，交给下面的
  # 统一报错分支处理 —— 不能让 python 的 traceback 打断流水线。
  plan_date="$(run_pyc "import sqlite3,sys;
try:
    c=sqlite3.connect('file:$db?mode=ro',uri=True);
    print(c.execute('SELECT MAX(trade_date) FROM daily_prices').fetchone()[0] or '')
except Exception:
    print('')")"
  if [[ -z "$plan_date" ]]; then
    echo "ERROR: 库里没有行情数据（$db 无 daily_prices 表或 MAX(trade_date) 为空），中止" >&2
    return 1
  fi
  # ⚠️ 2026-11-05：「交易计划」「持仓组合」已下线（用户要求），
  # 不再跑 plan build / build-all / --backfill（CLI 子命令也已删除）。
  echo "[2/2] 导出静态数据到 $out..."
  run_py "$ROOT_DIR/export_json.py" --db "$db" --out "$out"

  cp "$out/index.html" "$out/404.html"
  touch "$out/.nojekyll"
  echo "HTML: $(ls -1 "$out"/*.html 2>/dev/null | wc -l | tr -d ' ')  JSON: $(ls -1 "$out/data/"*.json 2>/dev/null | wc -l | tr -d ' ')"

  # ---- 发布前的单调性守卫（不可省）----
  # 2026-10-29 事故：孤儿库导出的 dates.json 也能通过「有没有数据」的校验，
  # 于是把比线上旧 16 天的站点推上去 —— 页面看着正常、数据停在半个月前，
  # 比空站点更隐蔽。拿**线上最新日**当下限：宁可这次不发布，也不发布更旧的。
  local live_latest live_src
  live_latest="$(curl -sf --max-time 20 "https://ycu.space/data/dates.json" \
    | run_pyc "import json,sys;d=json.load(sys.stdin).get('dates') or [''];print(d[0])" 2>/dev/null || echo "")"
  live_src="线上 data/dates.json"
  if [[ -z "$live_latest" ]]; then
    # 线上 HTTP 读不到时的兜底：读 gh-pages 分支上的 dates.json。
    #
    # ⚠️ 这里**绝不能**退回 plan_date —— 那是个真 bug（review 发现）：
    # plan_date 是 MAX(daily_prices.trade_date)，而产物的 dates.json 正是从
    # **同一个库**导出的 ⇒ 「产物 vs 自己」恒成立，守卫变成一句空话。
    # 2026-10-29 那种孤儿库发布陈旧站点的场景，恰好会在「线上 HTTP 被
    # Cloudflare 挡住 / 网络抖动」时完全失去保护。
    #
    # gh-pages 分支才是「已经真正发出去的东西」的记录，且本地 .git 里就有，
    # 不必再发一次网络请求。
    live_latest="$(git show origin/gh-pages:data/dates.json 2>/dev/null \
      | run_pyc "import json,sys;d=json.load(sys.stdin).get('dates') or [''];print(d[0])" 2>/dev/null || echo "")"
    live_src="origin/gh-pages:data/dates.json（线上 HTTP 不可达时的兜底）"
  fi
  if [[ -z "$live_latest" ]]; then
    # 两个来源都拿不到 ⇒ 没有可信下限 ⇒ **拒绝发布**（fail closed）。
    # 宁可这次不发（站点停在上一版），也不能在「不知道线上是什么」的情况下
    # 用可能更旧的数据覆盖它 —— 那正是这道守卫存在的理由。
    echo "ERROR: 无法确定线上最新日（线上 HTTP 与 origin/gh-pages 都读不到），拒绝发布" >&2
    echo "       排查：① 网络 / Cloudflare 是否正常 ② 先 git fetch origin gh-pages 再重试" >&2
    return 1
  fi
  echo "线上最新日: ${live_latest}（下限来源：${live_src}）"
  # 校验失败必须中止发布：守卫一旦形同虚设，旧数据就会被 force-push 上去 ——
  # 页面看着正常、数据却停在半个月前，比空站点更隐蔽。
  # verify_site_export.py 失败时自己会打印原因（缺文件 / 比线上旧），
  # 这里只负责「拦住 push」这一件事。
  if ! run_py "$ROOT_DIR/scripts/verify_site_export.py" --site "$out" --not-older-than "$live_latest"; then
    echo "ERROR: 导出产物比线上旧或不完整，已阻止发布（避免用旧数据覆盖更新的线上站点）" >&2
    return 1
  fi

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

  echo "[2/3] 补齐行情 $start ~ $end..."
  run_cmd sync-hs300-range --start "$start" --end "$end" --db "$db" \
    --gap-fill --concurrency 8 --rate 8 --retries 1 || echo "行情补齐失败，继续执行"

  # ⚠️ 2026-11-05：交易计划已下线，不再回补 plan（CLI 子命令已删除）。
  echo "[3/3] 导出网站数据到 $out..."
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

# ============================ portfolio (补齐 portfolio 数据) ============================

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

# ============================ refresh-agent (刷新按钮的本机端) ============================

cmd_refresh_agent() {
  # 用法: a-finder.sh refresh-agent {start|stop|status}
  #
  # 本机常驻进程：轮询 Worker 的刷新任务队列，领到就跑 `site --push`。
  # 仿 cmd_web 的模式（pid 文件 + nohup + 日志），别自创一套。
  local pid_file="$ROOT_DIR/.refresh-agent.pid"
  local log_file="$ROOT_DIR/logs/refresh-agent.log"
  local py
  if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    py="$ROOT_DIR/.venv/bin/python"
  else
    py="python3"
  fi
  local cmd="${1:-start}"
  case "$cmd" in
    start)
      local cur; cur="$(cat "$pid_file" 2>/dev/null || true)"
      if [[ -n "$cur" ]] && kill -0 "$cur" 2>/dev/null; then
        echo "already running (pid $cur)"; exit 0
      fi
      # 没有 secret 不给起：agent 起来也只会一直 401，还把日志刷满。
      # 用 ${VAR:-} 而不是 $VAR —— set -u 下裸变量未定义会直接退出（bash 3.2 尤其）。
      if [[ -z "${REFRESH_AGENT_SECRET:-}" ]]; then
        echo "ERROR: 请先设置 REFRESH_AGENT_SECRET（要与 Worker 的同名 secret 一致）" >&2
        echo "   例：export REFRESH_AGENT_SECRET=xxxx" >&2
        exit 1
      fi
      mkdir -p "$ROOT_DIR/logs"
      nohup "$py" "$ROOT_DIR/refresh_agent.py" >>"$log_file" 2>&1 &
      echo $! >"$pid_file"
      echo "started (pid $(cat "$pid_file"))  日志: $log_file"
      ;;
    stop)
      local cur; cur="$(cat "$pid_file" 2>/dev/null || true)"
      if [[ -n "$cur" ]] && kill -0 "$cur" 2>/dev/null; then
        kill "$cur"; echo "stopped"
      else
        echo "not running"
      fi
      rm -f "$pid_file"
      ;;
    status)
      local cur; cur="$(cat "$pid_file" 2>/dev/null || true)"
      if [[ -n "$cur" ]] && kill -0 "$cur" 2>/dev/null; then
        echo "running (pid $cur)"
      else
        echo "stopped"
      fi
      ;;
    *) echo "usage: a-finder.sh refresh-agent {start|stop|status}"; exit 1 ;;
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

cmd_fallback() {
  # 用法: a-finder.sh fallback [--force] [--dry-run] [--deadline HH:MM]
  # 只在「CI 已经放弃了」时才本地跑并推送，避免与 CI 双跑、双推。
  local force=0 dry=0 deadline="17:00"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --force) force=1; shift ;;
      --dry-run) dry=1; shift ;;
      --deadline) deadline="$2"; shift 2 ;;
      *) shift ;;
    esac
  done

  local need=0 tmp
  tmp="$(mktemp)"
  # 用 curl 取（python urllib 会被 Cloudflare 拦成 403/404；curl 正常）
  if curl -sf --max-time 20 "https://ycu.space/data/dates.json" -o "$tmp"; then
    # deadline 门在 needs_fallback.py 里（过了 deadline 线上仍落后才算 CI 真没成）。
    # 这是避免双写分叉的关键：本地抢跑会让 CI 以为「已发布」而跳过同步。
    local fargs=(--dates-json "$tmp" --deadline "$deadline")
    [[ "$force" -eq 1 ]] && fargs+=(--force)
    python3 "$ROOT_DIR/scripts/needs_fallback.py" "${fargs[@]}" || need=$?
  else
    echo "⚠ 无法读取线上日期，无法判定 —— 按「不兜底」处理（拿不准就别发布）"
    need=2
  fi
  rm -f "$tmp"

  # 只有 need==1（明确「线上落后且已过 deadline」）才发布。
  # need==2（参数错/网络读不到）一律**不发布**：本地兜底只是兜底，
  # 在判据不可信时强行 push 会覆盖线上更好的版本，且会诱导 CI 判 skip。
  # 原实现把 2 也当「按需要兜底处理」，等于 deadline 这道门可能被绕过（fail-open）。
  if [[ "$need" -ne 1 && "$force" -eq 0 ]]; then
    if [[ "$need" -eq 0 ]]; then
      echo "✅ 线上已是最新（或还没到 deadline ${deadline}），本地不动作（避免与 CI 双跑）"
    else
      echo "⏭ 无法判定是否需要兜底（rc=${need}），本地不发布；确认后可用 --force 手动补跑"
    fi
    return 0
  fi
  if [[ "$dry" -eq 1 ]]; then
    echo "[dry-run] 需要兜底：将执行 site --push"
    return 0
  fi
  echo "▶ 线上落后且已过 deadline ${deadline}，本地兜底：site --push"
  cmd_site "$DEFAULT_DB" 20 site --push
}

cmd_fallback_install() {
  # 生成 launchd plist 并打印安装命令（本脚本不直接写 ~/Library，避免权限问题）
  local agent_dir="$ROOT_DIR/deploy"
  local plist="$agent_dir/com.afinder.fallback.plist"
  mkdir -p "$agent_dir"
  cat > "$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.afinder.fallback</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$ROOT_DIR/a-finder.sh</string>
    <string>fallback</string>
  </array>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>17</integer><key>Minute</key><integer>10</integer></dict>
    <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>17</integer><key>Minute</key><integer>10</integer></dict>
    <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>17</integer><key>Minute</key><integer>10</integer></dict>
    <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>17</integer><key>Minute</key><integer>10</integer></dict>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>17</integer><key>Minute</key><integer>10</integer></dict>
  </array>
  <key>StandardOutPath</key><string>$ROOT_DIR/logs/fallback.log</string>
  <key>StandardErrorPath</key><string>$ROOT_DIR/logs/fallback.err.log</string>
  <key>RunAtLoad</key><false/>
</dict>
</plist>
PLIST
  chmod 644 "$plist"
  echo "已生成: $plist"
  echo
  echo "安装（一次性）："
  echo "  cp \"$plist\" ~/Library/LaunchAgents/"
  echo "  launchctl load ~/Library/LaunchAgents/com.afinder.fallback.plist"
  echo
  echo "检查 / 卸载："
  echo "  launchctl list | grep afinder"
  echo "  launchctl unload ~/Library/LaunchAgents/com.afinder.fallback.plist"
  echo
  echo "注意：launchd 只在机器开机时触发（睡眠会延后到唤醒）；"
  echo "      若 CI 已更新（线上日期已是最新），本任务什么都不做。"
}

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

──────── 选股 ────────
  picks [--no-sync]            环境变量 DB / TOP。pick_history.py 落库。
  # ⚠️ 2026-11-05：「交易计划」「持仓组合」已下线（用户要求），
  # plan / plan-all / daily-plan / portfolio 四个子命令已删除。

──────── 网站 ────────
  site [DB] [TOP] [OUT] [--push]
      全流程：sync-incremental → export_json。
      --push 把 site/ orphan-push 到 gh-pages。
  refresh [MONTHS] [DB] [OUT] [--push]
      最近 MONTHS 个月（默认 3）一键重刷：行情补齐 → 网站导出。
      不重算历史选股（避免未来函数偏差）。
  web {start|stop|restart|status}    环境变量 DB / PORT / TOP。Flask app.py。
  refresh-agent {start|stop|status}
      刷新按钮的本机端：轮询 Worker 任务队列，领到就跑 site --push。
      需环境变量 REFRESH_AGENT_SECRET（与 Worker 同名 secret 一致）。
      日志 logs/refresh-agent.log（agent 自身）与 logs/refresh.log（流水线）。
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

  site)            shift; cmd_site "$@" ;;
  refresh)         shift; cmd_refresh "$@" ;;

  web)             shift; cmd_web "$@" ;;
  refresh-agent)   shift; cmd_refresh_agent "$@" ;;
  daemon)          shift; cmd_daemon "$@" ;;
  backtest)        shift; cmd_backtest "$@" ;;
  fallback)
    shift || true
    cmd_fallback "$@"
    ;;
  fallback-install)
    cmd_fallback_install
    ;;
  cron)            shift; cmd_cron "$@" ;;
  help|--help|-h)  print_help ;;
  *) echo "unknown command: ${1:-}" >&2; echo "run 'bash a-finder.sh help' for usage" >&2; exit 1 ;;
esac
