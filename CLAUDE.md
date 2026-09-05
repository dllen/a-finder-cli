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

# Backtest (CLI variant)
python3 ma_backtest.py --db hs300.db --top 10 --days 240 [--tune|--walk-forward|--robust|--search-weights|--search-quota]

# Tests
uv run pytest tests/                           # full suite
uv run pytest tests/test_plan_builder.py -k share_lots   # single test / filter

# Web dashboard / lifecycle
bash run_web.sh                                # Flask on http://127.0.0.1:8000 (env: DB/PORT/TOP)
bash manage.sh {start|stop|restart|status} [cli-args]
bash sync_range.sh 2025-01-01 2026-03-12 hs300.db --gap-fill
bash sync_incremental_pick.sh hs300.db 15 picks --limit 100
```

## Architecture

Entry: `stock_cli.py` → `cli_layer.build_parser/run_cli` (argparse, formatting, Textual UI). Most analysis subcommands take `stocks` (list of `Stock` from `domain_models`) preloaded from local SQLite via `market_data.build_market_from_db` or freshly via `market_data.build_market` (network), then scored through `analysis_service.get_scores`.

Layered modules (top-level flat modules + a few packages):

- **Data ingestion** — `akshare_data_provider.py`, `data_providers.py`, `market_data.py`. Network calls funnel through a single retry layer (network-only, exponential backoff + jitter; HTTP 4xx never retried). `sync_service.sync_hs300*` is the high-level entry; concurrent / rate-limited, with checkpointing table `sync_checkpoints` for resume.
- **Storage** — SQLite only. Schema bootstrap in `db_schema.ensure_schema`; additive changes live in `db/migrations/*.sql` and are applied once-per-file via the `_applied_migrations` table in `db_repository._run_migrations`. Always add new schema via migration files, never by editing `db_schema.py` for an existing DB. Domain row dataclasses (`PriceRow`, `StockMeta`, `FundamentalsRow`, etc.) live at the top of `db_repository.py`.
- **Selection / signals** — `candidate_rules` (形态 candidates), `signal_rules` (detectors), `decision_rules` (primary signal), `scoring` (score_stocks), `stock_strategies` (orchestration). Pluggable per-strategy detectors are in `strategies/` and registered via `strategies/__init__.STRATEGIES` (`箱体突破`, `新高突破`, `布林超卖反弹`, `KDJ低位金叉`, `量价齐升`); multi-factor strategies live alongside (`dividend_multi_factor`, `pharma_multi_factor`, `multi_factor_base`).
- **Backtest** — `ma_backtest.py` for the canonical MA-strategy backtest (also exposes `default_candidate_config`). Generic engine lives in `backtest/` (`engine`, `models`, `order_executor`, `cost_calculator`, `performance`).
- **Plan / risk** — `plan_builder.build_plan` builds the daily `trade_plan` rows. Capital tiers and risk constants are in `config.py`: `CAPITAL_TIERS = [50000, 100000, 200000, 300000, 500000]`, `DEFAULT_CAPITAL = 100000`, plus `RR_TARGET`, `MAX_SINGLE`, `MAX_TOTAL`, `SLIPPAGE`, `STOP_ATR_MULT`. `risk_manager.RiskManager` + `market_regime` are re-exported through `shared_lib` so `plan_builder` and `ma_backtest` share one implementation — never re-implement price/sizing logic locally.
- **Strategy evolution** — `evolution/` (`service`, `champion`, `allocator`, `labeling`, `attribution`). Mirrored to schema via `db/migrations/2026_08_27_strategy_evolution.sql`.
- **Views** — `view_models` builds row tuples from `Stock`/`scores`; `formatter.format_table/format_lines` renders CLI tables. `pick_history.run_picks` (with `do_sync=False` flag) is what the web "重算榜单" button calls.
- **Web** — `app.py` (Flask) serves `static/` + a single-page dashboard (`static/dashboard.js`, `static/common.js`, `static/data-source.js`) and JSON API. `web_server.py` is a separate legacy `http.server` dashboard reading `site/data/*.json` produced by `export_json.py` for the static export path. The web app pulls `daily_picks` (picks page) and `trade_plan` (plan page); summary aggregates are always over the full plan, not the filtered view.
- **Shared plan/backtest** — `shared_lib/strategy.py` exposes `PlanRow`, `params_hash`, `compute_plan_prices` and re-exports `RiskManager`, `PositionConfig`, `default_candidate_config`. Treat it as the single source of truth — both `plan_builder` and `ma_backtest` import from here.

## Conventions

- Configurable defaults only via `config.py` (capital tiers, risk constants, sync sleep). New tier values require updating `config.CAPITAL_TIERS` and the CLI validator.
- SQLite is the only DB; no ORM. Repository code in `db_repository.py` is the only place that issues raw SQL for domain tables — CLI/UI layers go through it.
- Logs land in `logs/fetch_success.log` and `logs/fetch_failed.log` during sync; web log is `web.log`; daemonized CLI log is `a-finder.log` (via `manage.sh`).
- New strategies: drop a detector in `strategies/`, register in `strategies/__init__.STRATEGIES`. For factor strategies, extend `multi_factor_base.py`.
- New schema columns: add a `db/migrations/<YYYY_MM_DD>_<name>.sql` file; do not edit `db_schema.py` for already-deployed DBs.

## Notes for Future Sessions

- Persistent memory is mandatory (see top-level project instructions). Before non-trivial work, `icm recall "<query>" -t context-a-finder-cli`; after architecture/decision/error/taste events, `icm store` immediately under the matching topic.
- Bash output should go through `rtk` (see top-level RTK rules); e.g. `rtk git status`, `rtk pytest tests/`.
- Known resolved errors live in `.learnings/ERRORS.md` (sync transient failures, `bash nounset` array expansion, `ruff` not installed, console-script missing). Check there before fixing seemingly novel failures — the fix pattern is usually already documented.
- The Chinese design doc at `system-design.md` is aspirational product/market context, not implementation spec.
