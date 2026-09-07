# a-finder.sh 使用手册

`a-finder.sh` 是项目统一入口，聚合了原 10 个 shell 脚本（`sync_range` / `sync_all` / `sync_incremental_pick` / `daily_plan` / `run_picks` / `build_site_data` / `run_web` / `manage` / `test_ma_backtest` / `run_local_cron`）。

> 权威手册以脚本内置为准：`bash a-finder.sh help`。本文件同步镜像，若不一致以脚本为准。

## 用法

```
bash a-finder.sh <command> [args...]
bash a-finder.sh help
```

## 同步

| 命令 | 说明 |
|---|---|
| `sync-meta [DB]` | 同步沪深300成分股元数据 |
| `sync-range START END [DB] [EXTRA...]` | 区间同步日 K（默认 START=2025-01-01, END=今天）。EXTRA 透传给 `a-finder sync-hs300-range`，如 `--gap-fill --concurrency 6 --rate 8` |
| `sync-all START END [DB] [EXTRA...]` | meta + range（默认 END=今天） |
| `sync-incremental [DB] [TOP] [pick-mode] [EXTRA...]` | 增量同步（meta + range `--gap-fill` 并发，端点不可达 fail-fast）+ 选股落库。pick-mode: `pick-history`（写 `daily_picks`）/ `picks` / `ma-picks` |

## 选股 / 计划

| 命令 | 说明 |
|---|---|
| `picks [--no-sync]` | 环境变量 `DB` / `TOP`。`pick_history.py` 落库；`--no-sync` 跳过增量同步 |
| `plan [DB] [DATE]` | 生成单 portfolio 交易计划（`plan build`） |
| `plan-all [DB] [TIERS]` | 批量生成多档位组合（默认 5W-50W 10 档） |
| `daily-plan [DB] [TOP] [pick-cmd] [EXTRA...]` | sync-incremental + plan build，一次性日流水 |

## 网站

| 命令 | 说明 |
|---|---|
| `site [DB] [TOP] [OUT] [--push]` | 全流程：sync-incremental → plan build → build-all → backfill → export_json。`--push` 把 `site/` orphan-push 到 gh-pages（不动工作树/当前分支） |
| `refresh [MONTHS] [DB] [OUT] [--push]` | 最近 MONTHS 个月（默认 3）一键重刷：行情补齐 → 计划回补 → 网站导出。计划回补只覆盖 `daily_picks` 已有日期；不重算历史选股（避免未来函数偏差） |
| `web {start\|stop\|restart\|status}` | 环境变量 `DB` / `PORT` / `TOP`。Flask `app.py`（端口 lsof 探活） |
| `daemon {start\|stop\|restart\|status} [cli-args]` | 后台跑 `a-finder` 子命令（默认 `overview`），PID 文件管理 |
| `cron` | launchd 入口：工作日 15:30 跑 `site --push`；周末（`date +%u >= 6`）跳过 |

## 回测

| 命令 | 说明 |
|---|---|
| `backtest [DB] [TOP] [DAYS] [EXTRA...]` | `ma_backtest.py`（默认 top=10 days=240）。EXTRA 如 `--tune` / `--walk-forward` / `--robust` |

## 示例

```bash
bash a-finder.sh sync-range 2025-01-01 2026-09-07 hs300.db --gap-fill
bash a-finder.sh sync-incremental hs300.db 20 pick-history
bash a-finder.sh picks --no-sync
bash a-finder.sh plan hs300.db 2026-09-07
bash a-finder.sh site hs300.db 20 site --push
bash a-finder.sh refresh 3 hs300.db site --push
DB=hs300.db PORT=8080 TOP=20 bash a-finder.sh web start
bash a-finder.sh daemon start overview
bash a-finder.sh backtest hs300.db 10 240 --tune
```

## 环境变量

- `DB`（默认 `hs300.db`）— `picks` / `web` 使用
- `TOP`（默认 `10`）— 选股数量
- `PORT`（默认 `8000`）— web 端口

## 常量

- `BACKFILL_ANCHOR=2026-08-01` — 系统回补 go-live 锚点（`site` / `cron` 使用；与 `.github/workflows/fetch-data.yml` 同步）
- `TIERS=50000,...,500000` — 10 个资金档位

## 安装定时任务（launchd）

```bash
cp com.a-finder.site-data.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.a-finder.site-data.plist
# 查看
launchctl print gui/$(id -u)/com.a-finder.site-data
# 卸载
launchctl unload ~/Library/LaunchAgents/com.a-finder.site-data.plist
```

日志：`logs/site-data.out.log` / `logs/site-data.err.log`。
