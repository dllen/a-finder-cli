# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`a-finder-cli` is an A-share (沪深300) stock picker, signal generator, backtester and trade-plan builder — both a CLI (`a-finder`) and a Flask web dashboard (`app.py`). Data sources: Eastmoney primary (constituents + `push2his` K-line) with Tencent `fqkline` 前复权 as auto fallback.

Python >=3.10, packaged with `uv` (`pyproject.toml` + `uv.lock`). Console script `a-finder` → `stock_cli:main`.

## Environment

```bash
uv venv && uv sync              # install
uv sync --reinstall             # recover if `a-finder` console-script missing
```

Direct (no uv): `python3 stock_cli.py <command>`.

## Common Commands

```bash
# Daily / interactive
uv run a-finder overview
uv run a-finder picks --top 5
uv run a-finder signals --code 600519
uv run a-finder ma-picks --top 20
uv run a-finder ma-picks --top 20 --ui        # Textual TUI
uv run a-finder buy-signals --top 30
uv run a-finder ui --top 10                   # TUI overview
uv run a-finder meta --code 600519 --db hs300.db

# Sync (Eastmoney → SQLite, default DB: hs300.db)
uv run a-finder sync-hs300-meta --db hs300.db
uv run a-finder sync-hs300-range --start 2025-01-01 --end 2026-03-12 --db hs300.db \
    --concurrency 6 --rate 8 --retries 4 --backoff 0.6
uv run a-finder sync-hs300-range ... --no-resume | --only-failed | --gap-fill | --retry-gaps

# Trade-plan / strategy evolution
uv run a-finder plan build [--capital 100000] [--backfill]
uv run a-finder evolve

# 可转债策略（独立转债仓，不走 plan_builder）
uv run a-finder cb-picks --top 20 [--dry-run] [--weights a=0.5,b=0.25,c=0.25]
uv run a-finder cb-add 113059 --shares 50 --price 108.5
uv run a-finder cb-positions
uv run a-finder cb-alerts [--json]

# Backtest (CLI variant)
python3 ma_backtest.py --db hs300.db --top 10 --days 240 [--tune|--walk-forward|--robust|--search-weights|--search-quota]

# Tests
uv run pytest tests/                           # full suite
uv run pytest tests/test_plan_builder.py -k share_lots   # single test / filter

# Web dashboard / lifecycle (unified entry: bash a-finder.sh help)
bash a-finder.sh web {start|stop|restart|status}      # Flask on http://127.0.0.1:8000 (env: DB/PORT/TOP)
bash a-finder.sh daemon {start|stop|restart|status} [cli-args]
bash a-finder.sh sync-range 2025-01-01 2026-09-07 hs300.db --gap-fill
bash a-finder.sh sync-incremental hs300.db 15 picks --limit 100
bash a-finder.sh site [db] [top] [out] [--push]        # 本地一键生成网站数据（同步+选股+计划+组合+导出+可选推送 gh-pages）
bash a-finder.sh cron                                  # launchd 入口（工作日 15:30 跑 site --push）
```

# CI 自动部署（deploy-site.yml）
- `.github/workflows/deploy-site.yml`：A 股交易日 15:30 北京（07:30 UTC）自动
  同步→选股→计划→组合→转债→导出→部署 gh-pages。用 chinese_calendar 判交易日，
  数据源多源降级（SourceHealthTracker），同步/转债失败容忍（缓存兜底）。
  手动兜底：GitHub Actions 页面的 workflow_dispatch。
- GitHub schedule 高负载会延迟/丢弃，有三重保险：备用 cron 07:45 UTC、
  Cloudflare Workers Cron 07:35 UTC 兜底发 workflow_dispatch（`worker/index.js` 的
  `scheduled` handler，需 secret `GITHUB_DISPATCH_TOKEN`——fine-grained PAT 仅授本仓库
  Actions: Read and write；手动验证：登录后 POST /api/decide/dispatch-test）、
  手动 workflow_dispatch。hs300.db 缓存 key 按 run_id 唯一（固定 key 会冻结缓存——
  actions/cache 不允许覆盖已存在的 key）。
- 本地 `a-finder.sh site --push` 已由 CI 接管；本地 cron 应停用，避免与 CI 双跑。

# CI 自动部署 Worker（deploy-worker.yml）
- `.github/workflows/deploy-worker.yml`：`worker/**` 或 `wrangler.toml` 推送到 main 时自动部署
  Cloudflare Worker（D1 建表幂等 → 可选同步 DECIDE_* secrets → wrangler deploy → health 自检）。
- 需要 GitHub Secrets：`CLOUDFLARE_API_TOKEN`（Workers Scripts:Edit + D1:Edit）、`CLOUDFLARE_ACCOUNT_ID`。
- `DECIDE_USER/PASSWORD/SECRET` 三个 GitHub Secrets 可选：wrangler deploy 不会删除已有 secrets，
  仅新建或轮换凭据时才配置（且必须三个同时设置，否则报错——防止空值静默覆盖）。
- 本地 `scripts/deploy-worker.sh` 仍可用于首次交互式部署或排障。

## Architecture

Entry: `stock_cli.py` → `cli_layer.build_parser/run_cli` (argparse, formatting, Textual UI). Most analysis subcommands take `stocks` (list of `Stock` from `domain_models`) preloaded from local SQLite via `market_data.build_market_from_db` or freshly via `market_data.build_market` (network), then scored through `analysis_service.get_scores`.

Layered modules (top-level flat modules + a few packages):

- **Data ingestion** — `akshare_data_provider.py`, `data_providers.py`, `market_data.py`. Network calls funnel through a single retry layer (network-only, exponential backoff + jitter; HTTP 4xx never retried). `sync_service.sync_hs300*` is the high-level entry; concurrent / rate-limited, with checkpointing table `sync_checkpoints` for resume.
- **Storage** — SQLite only. Schema bootstrap in `db_schema.ensure_schema`; additive changes live in `db/migrations/*.sql` and are applied once-per-file via the `_applied_migrations` table in `db_repository._run_migrations`. Always add new schema via migration files, never by editing `db_schema.py` for an existing DB. Domain row dataclasses (`PriceRow`, `StockMeta`, `FundamentalsRow`, etc.) live at the top of `db_repository.py`.
- **Selection / signals** — `candidate_rules` (形态 candidates), `signal_rules` (detectors), `decision_rules` (primary signal), `scoring` (score_stocks), `stock_strategies` (orchestration). Pluggable per-strategy detectors are in `strategies/` and registered via `strategies/__init__.STRATEGIES` (`箱体突破`, `新高突破`, `布林超卖反弹`, `KDJ低位金叉`, `量价齐升`); multi-factor strategies live alongside (`dividend_multi_factor`, `pharma_multi_factor`, `multi_factor_base`).
- **Backtest** — `ma_backtest.py` for the canonical MA-strategy backtest (also exposes `default_candidate_config`). Generic engine lives in `backtest/` (`engine`, `models`, `order_executor`, `cost_calculator`, `performance`).
- **Plan / risk** — `plan_builder.build_plan` builds the daily `trade_plan` rows. Capital tiers and risk constants are in `config.py`: `CAPITAL_TIERS = [50000, 100000, 150000, 200000, 250000, 300000, 350000, 400000, 450000, 500000]` (10 tiers), `DEFAULT_CAPITAL = 100000`, `MAX_POSITIONS = 8`, plus `RR_TARGET`, `MAX_SINGLE`, `MAX_TOTAL`, `SLIPPAGE`, `STOP_ATR_MULT`. `risk_manager.RiskManager` + `market_regime` are re-exported through `shared_lib` so `plan_builder` and `ma_backtest` share one implementation — never re-implement price/sizing logic locally.
- **Strategy evolution** — `evolution/` (`service`, `champion`, `allocator`, `labeling`, `attribution`). Mirrored to schema via `db/migrations/2026_08_27_strategy_evolution.sql`.
- **可转债** — `cb_data_provider.py`（akshare 多源降级）、`cb_repository.py`（`cb_` 表读写，写入用 SAVEPOINT 原子化）、`cb_pipeline.py`（编排）、`strategies/convertible.py`（粗筛/打分/预警）。表前缀 `cb_`，与股票体系完全隔离；转债整手 10 张（面值 100 元），风控独立于 `risk_manager.py`（`config.CB_RISK`：止损-8% / 止盈+20% / 单只15% / 10张一手）。分组打分：债底40%/股性30%/条款30%，缺失因子组内重新归一化。数据源：`bond_zh_cov`（全市场）→ `bond_zh_cov_info`（裸 6 位代码）→ `bond_cb_redeem_jsl`（强赎状态）。
- **日期筛选组件** — `templates/components/date_filter.html`（Jinja macro）+ `static/css/date-filter.css` + `static/js/date-filter.js`。四个页面统一用它：picks/plan 用完整版（`date_filter('d','日期')`），cb/decide 用 `compact=true`。能力：‹/› 步进交易日（选项按新→旧排列，方向在 JS 里换算）、`?d=YYYY-MM-DD` URL 状态（停留在最新时不写入参数）、键盘 ←/→/Home/End、加载/错误/成功三态类（`setDateFilterState`）。八态预览见 `docs/previews/date-filter.html`。新增页面用该组件时不要自建日期控件，否则又会出现多套日期交互。
- **Views** — `view_models` builds row tuples from `Stock`/`scores`; `formatter.format_table/format_lines` renders CLI tables. `pick_history.run_picks` (with `do_sync=False` flag) is what the web "重算榜单" button calls.
- **Web** — `app.py` (Flask, ~680 lines) serves Jinja2 templates from `templates/` and static assets from `static/`. Pages: picks (`/`), plan (`/plan`), portfolio (`/portfolio`), cb (`/cb`), decide (`/decide`). JS is Vanilla ES6 modules in `static/js/` (no jQuery). Bootstrap 5.3.3 and Chart.js 4.4.1 are vendored locally in `static/vendor/` (no CDN dependency). Dual deployment: Flask API mode and static JSON+HTML mode (gh-pages via `export_json.py`). Static asset URLs carry `?v=<git-sha>` cache-busting (Cloudflare serves `static/*.js` with `max-age=14400`).
- **10w 决策台** — `/decide` 按 `config.PORTFOLIO_10W`（8w 股 + 2w 债、≤8 只、单票 ≤10%、总仓位 ≤75%）展示今日执行清单，对照用户手工录入的真实持仓（`real_positions` / `real_cb_positions`，仓储 `real_position_repository.py`）高亮偏差。判定逻辑在 `decide_service.py`（纯函数，10 类偏差 × info/warn/danger）。**读取 `trade_plan` 的 10W 档位必须按 code 去重**——回补会用不同 `params_hash` 追加重复行。报告见 `docs/reports/10w-decision-system.md`。
- **持仓云端化（Worker + D1）** — 持仓的唯一真相源是 **Cloudflare D1**，由 `worker/index.js` 提供 `/api/decide/*`（读公开、写需登录的 HttpOnly Cookie）。**登录是双因子**：`DECIDE_USER` + `DECIDE_PASSWORD`，签名密钥 `DECIDE_SECRET`，三者都走 `wrangler secret put`；前端用 Bootstrap Modal（`templates/components/decide_login_modal.html`），不使用浏览器原生弹窗。本地 SQLite 同名表保留但不再写入。**对比逻辑有 Python 与 JS 两份实现**：`decide_service.py`（服务端/导出）与 `static/js/decide_compare.js`（前端实时算），由 `tests/test_decide_compare_parity.py` 用 node 跑同一 fixture 保证逐项一致；配置来源 `data/decide-config.json`。D1 建表见 `worker/schema.sql`，部署见 `wrangler.toml`（路由 `ycu.space/api/*`）与 `scripts/deploy-worker.sh`。
- **Shared plan/backtest** — `shared_lib/strategy.py` exposes `PlanRow`, `params_hash`, `compute_plan_prices` and re-exports `RiskManager`, `PositionConfig`, `default_candidate_config`. Treat it as the single source of truth — both `plan_builder` and `ma_backtest` import from here.

## Conventions

- Configurable defaults only via `config.py` (capital tiers, risk constants, sync sleep). New tier values require updating `config.CAPITAL_TIERS` and the CLI validator.
- SQLite is the only DB; no ORM. Repository code in `db_repository.py` is the only place that issues raw SQL for domain tables — CLI/UI layers go through it.
- Logs land in `logs/fetch_success.log` and `logs/fetch_failed.log` during sync; web log is `web.log`; daemonized CLI log is `a-finder.log` (via `a-finder.sh daemon`).
- New strategies: drop a detector in `strategies/`, register in `strategies/__init__.STRATEGIES`. For factor strategies, extend `multi_factor_base.py`.
- New schema columns: add a `db/migrations/<YYYY_MM_DD>_<name>.sql` file; do not edit `db_schema.py` for already-deployed DBs.

## Notes for Future Sessions

- Persistent memory is mandatory (see top-level project instructions). Before non-trivial work, `icm recall "<query>" -t context-a-finder-cli`; after architecture/decision/error/taste events, `icm store` immediately under the matching topic.
- Bash output should go through `rtk` (see top-level RTK rules); e.g. `rtk git status`, `rtk pytest tests/`.
- Known resolved errors live in `.learnings/ERRORS.md` (sync transient failures, `bash nounset` array expansion, `ruff` not installed, console-script missing). Check there before fixing seemingly novel failures — the fix pattern is usually already documented.
- The Chinese design doc at `system-design.md` is aspirational product/market context, not implementation spec.
