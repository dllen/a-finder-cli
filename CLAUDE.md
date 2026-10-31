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
- **增量同步做了三件事保证「准 + 快」**：
  1. 缺口按**真实交易日历**判定（`chinese_calendar` → `market_calendar.trading_days` →
     `sync_service._build_missing_segments`）：只有 A 股真正开市的日期才算应有数据，
     长假不再反复回补；调休补班的周末不算（同步侧传 `include_known_weekend=True`，
     库里已有该日数据时才认）；相邻缺失点合并成一段。
     日历不可用时回退到「市场日历（库里已有日期）+ 尾部窗口」。
  2. 日常窗口只补最近 60 天（冷启动 400 天），并发/限流下调到 6/6、重试 2 次；
     同步后跑 `scripts/ci_check_data.py --prices-only` 校验，未落地就隔 120s 重试，
     最多 3 轮 —— 数据源 15:30 后才出数的情况能自动等到。
  3. `pick_history` 在同步后以 `--no-sync` 运行，避免内部再跑一遍增量同步（纯浪费配额）；
     计划日期对齐 `MAX(daily_prices.trade_date)` 而不是硬编码今天，绝不生成空计划。
- **数据门禁**：部署结束后再跑一次 `scripts/ci_check_data.py`（行情最新日/覆盖股票数/
  选股日期/计划日期），不达标则 workflow 变红（站点仍按最新可得数据发布，对用户可用）。
  部署/缓存/导出步骤带 `always()`，前面失败也照常发布。
- **补救跑 / 密集排班**：07:30–09:00 UTC 每 15 分钟一班（共 8 个 cron）。
  起因是实测 GitHub schedule 对本仓库不可靠：10-26 的 07:30 那班延迟到 14:27 UTC
  才跑，10-27 的三班**一次都没触发**。密集排班后，队列延迟只要影响其中一班即可；
  已发布当天的班由 freshness 步骤秒退（不浪费 CI 分钟与数据源配额）。
- **触发链的三个独立腿**（都不可靠时要互相兜底）：
  1. GitHub schedule（上面那 8 个 cron）
  2. Cloudflare Workers Cron 07:35 / 08:35 UTC 发 workflow_dispatch
     —— **需要仓库 secret `GITHUB_DISPATCH_TOKEN`**（fine-grained PAT，权限
     Actions: Read and write）；没配之前这条腿是空的
  3. 本机 launchd：`bash a-finder.sh fallback`（只有线上落后才本地跑并 push）
- **排查「页面数据没更新」的顺序**：① 查 Actions 里 deploy-site 有没有 run
  （没有 = schedule 没触发，GitHub 队列问题）→ ② 查 run 日志里 `数据来源` 与门禁结果
  → ③ 查线上 `data/dates.json` 的最新日期 → ④ 手动 `bash a-finder.sh fallback` 兜底。
- GitHub schedule 高负载会延迟/丢弃，有四重保险：备用 cron 07:45 UTC、
  Cloudflare Workers Cron **07:35 与 08:35 UTC 各一次**兜底发 workflow_dispatch
  （`worker/index.js` 的 `scheduled` handler，需 secret `GITHUB_DISPATCH_TOKEN`
  ——fine-grained PAT 仅授本仓库 Actions: Read and write；手动验证：
  POST /api/decide/dispatch-test）、手动 workflow_dispatch。
- **失败通知**：deploy-site 末尾有 `if: failure()` 步骤，配置仓库 secret `NTFY_TOPIC`
  （ntfy.sh 主题名，浏览器/App 订阅即可）后失败会推送；未配置时写 Step Summary 并保持
  run 变红。也可用 GitHub 自带「Notifications → Actions → 仅失败时发邮件」。
- `hs300.db` 缓存 key 按 `run_id` 唯一（固定 key 会冻结缓存 —— actions/cache 不允许
  覆盖已存在的 key）。
- **每周体检 + 自愈 + 备份**（`gap-heal.yml`，周日 10:00 北京）：`scripts/check_gaps.py`
  扫最近 30 个交易日覆盖度 → `sync-hs300-range --gap-fill` 补抓 → 再扫一次（仍有缺口则红）
  → 顺手把 `hs300.db` 压成 artifact（90 天），缓存丢失时可秒恢复不必重抓 400 天。
- **本地兜底（可选，第三条腿）**：`bash a-finder.sh fallback` 过了 **deadline（默认 17:00
  北京）** 且线上仍落后，才本地跑 `site --push`（`scripts/needs_fallback.py --deadline`）；
  `bash a-finder.sh fallback-install` 生成 launchd plist（工作日 17:10），按打印的命令
  装到 `~/Library/LaunchAgents`。站点域名是 **ycu.space**（github.io 路径已 404）。
  手动早于 deadline 补跑用 `--force` 绕过。**deadline 是防双写分叉的关键**：CI 在
  15:30–17:00 有 8 个班次，本地抢跑会让 CI 以为「已发布」而跳过同步。
- 本地 `a-finder.sh site --push` 已由 CI 接管；本地 cron（无条件跑）应停用，
  用上面的 `fallback`（有条件）替代，避免与 CI 双跑。
- **本地 hs300.db 与 CI 的库会分叉，这是预期行为**：CI 每天跑 `plan build-all` 所以档位
  （10W…50W）行是最新的；本地只跑 `plan build`（写 `default` 档），档位行可能停在很久以前。
  排查「组合页/日期筛选看不到近期日期」时先看本地库的 `MAX(plan_date) WHERE portfolio!='default'`，
  不要当成 bug。线上数据以 CI 为准，本地只用于调试。
- **⚠️ 但「线上站点由 CI 发布」是错的（2026-10-29 查明）**：`git log origin/gh-pages` 只有
  一个提交 `update site data 2026-10-27-20:26` —— 这个 message 格式来自
  `a-finder.sh:157` 的 `git commit-tree`（**本地**发布路径）；CI 的
  `peaceiris/actions-gh-pages` 用的是 `"site: 自动更新 <date>"`，格式不同。
  ⇒ **站点一直由本地库发布，CI 缓存库是另一条谱系**，两边独立漂移。
  更坑的是 `export_json.py` 只从 `daily_picks`/`trade_plan` 出 JSON、**不看
  `daily_prices`**，所以「站点页面正常」**不能**证明 CI 库的行情齐全。
  推论：排查数据问题要分别看两个库，别拿站点当 CI 库的健康证明。

## 两谱系分叉：已修的根因与现在的不变量（2026-10-29）

根因只有一句话：**CI 的同步判据读错了对象** —— 它读「线上站点有没有今天」，
但站点可以被本地发布，而 `dates.json` 又只反映 `daily_picks`/`trade_plan`、
**不看 `daily_prices`**。于是本地先发布 → CI 判 skip → 跳过同步也跳过缓存保存
→ CI 行情谱系冻结（断在 2026-10-13），两个库持续分叉。

现在的不变量（改这块代码前先读懂，别再退回）：

1. **同步判据 = CI 自己的库**。`deploy-site.yml` 的 freshness 用
   `scripts/db_freshness.py --db hs300.db`，落后按**交易日**算（不是自然日）。
   退出码：0 新鲜 / 1 可增量补 / 2 无库 / 3 **陈旧谱系**（落后 > 3 个交易日 →
   冷启动 400 天窗口重建，别指望每天补一点）。
2. **Restore 必须在 freshness 之前**。判据要读自己的库，就得先把库恢复出来。
   顺序反了就只能读线上站点 —— 那正是事故本身。
3. **`skip_sync` 与「要不要发布」分开决策**。混成一个 skip 会导致：
   库已新鲜但线上缺当天 → 所有人都跳过 → 当天没人发布。
   所以另有 `site_stale`（线上缺当天就仍要发布）。
4. **没同步就不重复存缓存**。8 班/天 × 200MB × 7 天保留 ≈ 11GB，会打爆
   `actions/cache` 的 10GB 配额，淘汰掉别的重要条目。
5. **本地兜底只在「CI 已经放弃」时动手**：过了 deadline（默认 17:00 北京）
   线上仍落后才算数。本地抢跑本身就在制造分叉。
- **缓存 key 必须永远带 run_id**：`actions/cache` 不允许覆盖已存在的 key，固定 key
  会让缓存**永久冻结**（2026-10-26 的 `hs300-db` 就是这样）。改 key 后还要确认
  旧条目是否仍能被 `restore-keys` 命中 —— `'hs300-db'.startswith('hs300-db-')` 是
  **False**，所以 10-26 17:49 改 key 的那一刻旧条目直接变成不可达的孤儿，
  CI 行情谱系断在 2026-10-13（详见 `.learnings/ERRORS.md`）。
  恢复缓存后要加**新鲜度校验**：`MAX(daily_prices.trade_date)` 落后太多就判定
  谱系孤儿、走冷启动重建，别指望增量补。
- **数据源能力边界**：**10-29 只存在于东财**（腾讯 qfqday / 新浪都只到 10-28）。
  东财被限流时备源链会静默少一天。审计的 L1 判据是 `>=280 只`，
  280~299 只的「半截天」**不会被拦**。补完要验 `COUNT(DISTINCT code)=300`，
  尤其当日行情。
- **复权口径必须显式记录（2026-10-30）**：东财(`fqt=0`)/新浪是**不复权**，
  腾讯(`fqkline qfq`)是**前复权**。东财不可达时链会退到腾讯 ⇒ **前复权价格被
  静默写进不复权的表**，除权日留下一段假跳空，污染 MA/动量/回测窗口与当天计划。
  - 口径登记在 `data_providers.SOURCE_BASIS`（**唯一真相源**，新增源必须同步登记；
    未登记的源一律当「非首选口径」处理 —— 宁可漏抓也不错写）。
  - `fetch_daily_kline_smart` 返回 `(rows, name, source, basis)`，
    **且必须把 basis 盖到每一行上**（`row.adj_basis = basis`）。
    ⚠️ 只放进返回值是不够的：`PriceRow.adj_basis` 默认 `"none"` ⇒ 不盖的话
    qfq 行会带着 `"none"` 落库 ⇒ **库里说「没混口径」，实际已经混了** ——
    比没有这一列更隐蔽。测试：`tests/test_price_adj_basis.py::TestBasisStampedOnRows`。
  - 落库列 `daily_prices.adj_basis`（migration `2026_10_30_daily_prices_adj_basis.sql`）。
    检测：`db_repository.get_adj_basis_breakdown(conn)`。
  - ⚠️ `akshare_data_provider.fetch_daily_kline_akshare` 用 `adjust="qfq"`
    （**也是前复权**），目前**没有调用方**。哪天接进链路，**必须**显式标
    `adj_basis="qfq"`，否则默认的 `"none"` 会错标。

# 每日巡检与自动补齐（daily-audit.yml）
- 每晚 21:00 北京（13:00 UTC，周一到周五）独立跑一次体检。与 deploy-site 是各自独立的
  workflow，**失败互不影响**（一边红了不拖累另一边），但**共用 hs300.db 缓存**
  （`hs300-db-*` 前缀，restore-keys 互相兜底）——「解耦」指失败隔离，不是数据隔离。
- **体检基准**：`market_calendar.trading_days` 算出的真实交易日（近 30 天），
  **完全独立于库内数据**。这是关键 —— 缺口判定若以「库的最后一天」为基准，
  整天漏抓的那天在基准里不可见，永远查不出来（2026-10-26 实际踩过）。
- **三层判定**：L1 行情（该日 < 280 只）、L2 选股（该日无行）、L3 计划（任一应有档位无行）。
  L1 缺时只报 L1，派生的 L2/L3 记入 suppressed，避免噪声。
  L2/L3 不设行数阈值 —— 选股数量天然波动（55~77 条），设阈值会大量误报。
- **修复边界**：L2/L3 自动补；**L1 分两类，两个窗口**（2026-10-30 改，见下）；
  转债永不自动补（数据源只有实时快照）。
  L2/L3 的「纯计算」只表示**不需要外部数据源**，不是**无副作用**：L3 以
  `paper_trade=True` 执行，会写 `open_positions` / `trade_events`（实测补 10-26 时
  open_positions 该日建仓 1→8、trade_events +7）。
- **⚠️ L1 修复窗口：零行 vs 部分，两个窗口、按交易日算**（2026-10-30 改，改前是
  「所有 L1 缺口统一 7 个**日历日**」，后果见下）：
  - **零行缺口**（当天 0 只 = 那天**从没抓过**，库里根本没有那一段序列，
    **没有序列可污染**）→ 宽窗 `MISSING_DAYS_CAP = 60` 个交易日，自动补抓。
  - **部分缺口**（当天 1~279 只 = 抓了但没抓全，当天**已有**某一口径的行，
    补进来的行可能来自**另一个复权口径**的备源 —— 腾讯 `qfq` 前复权 vs
    东财/新浪 `fqt=0` 不复权，混写会在除权日造出假跳空）→ 窄窗
    `MAX_PRICE_DAYS = 7` 个交易日，超期只报警。
  - 分类依据是 `Gap.codes`（体检报告落盘当天实际只数）。**报告里没有 `codes`
    （旧报告）→ 按部分缺口处理**（保守：宁可少修，不可混口径）。
  - 窗口按**交易日**算，不是日历日：国庆 8 个日历日只有 5 个交易日，
    日历日窗口在长假后必然错判。日历不可用 → 一律当超窗（只报警）。
  - **为什么必须改**：2026-10-30 巡检报「无法自动修复的缺口」L1 10-14~10-30
    共 13 天。旧实现对所有 L1 缺口统一用 7 天日历窗 → 这 13 天**永久超窗** →
    每晚重复报警、永远不修、run 永远红。而且**只放宽窗口还不够**：10-23~10-30
    那天是**尝试过但补抓失败**（东财不可达；10-29 的数据只有东财有，
    腾讯/新浪只到 10-28）—— 补抓能不能成功取决于数据源，不是窗口。
- **陈旧谱系自愈**（2026-10-30 加）：`daily-audit.yml` 在体检**之前**跑
  `scripts/db_freshness.py`（rc=3 = 落后 > 3 个交易日 = 陈旧谱系）→ 弃库
  (`rm -f hs300.db`) → 400 天冷启动同步 → 再体检。谱系断了太久时在烂谱系上
  打补丁必然被限流打断、缺口又变成「超窗只报警」⇒ 永远红。
  冷启动同步要**放在体检之前**：谱系缺 400 天会报出 400 天的 L1 缺口，
  逐日补抓要几百次请求，必然被限流。
- **通知必须点名正确的产物**（2026-10-30 改）：本地库与 CI 缓存库
  （Actions Cache 里的 hs300.db）是两条独立漂移的谱系。旧的建议
  「手动跑 `bash a-finder.sh fallback`」指向的是**本地库** —— 本地库健康时
  跑它毫无作用，真正坏掉的缓存条目一直坏着。现在按层分开给建议：
  L1 → 重建 **CI 缓存库**（删 Actions 缓存条目 / dispatch deploy-site）；
  L2/L3 → 在 CI 缓存库上跑 `scripts/repair_gaps.py`（纯重算）；
  恒定一行提示检查东财是否限流。
  同理，通知的「原因」要**按类别分组**（`repair_failed` / `missing_lineage` /
  `partial_basis_risk`），不能把「尝试过但失败」与「压根没试」混成一句 ——
  混了会把真正在窗内、真正失败的那几天掩盖掉（2026-10-30 实际就掩盖了）。
- **补计划的真实语义**：不是「只补缺失日期、绝不重算」。`plan build-all --backfill
  --since <最早缺口日>` 会按缺口日**往后重放**所有日期：完整日期命中已有
  `params_hash` 走 `build_plan` 的缓存短路，结果不变；但**部分缺失**的日期会用当前
  `open_positions` 重建，补出的历史计划反映的是重建时的持仓状态，不是当时的快照
  （实测 10-26 重建后 trade_plan 行数与原始不相等）。要原样快照只能靠备份，不能靠重放。
- **L2 缺口必然连带 L3**：选股没有 → 当天计划也无从生成。`repair_gaps.py` 的 L2 动作
  因此同时带 `pick_history` + `plan build` + 一次 `plan build-all --backfill`（日期取
  L2∪L3 并集，命令不重复），否则「补完选股、计划立刻成为缺口」，复检仍 rc=1，要跑
  第二轮才收敛（Task 7 端到端实测）。
- **审计 rc=0 只证明「有数据」，不证明「每只都在」**：L1 判据是 >= 280 只，
  所以部分抓取（280~299）不会被拦，选股会在不完整票池上算 —— 「290 只因为停牌」
  与「290 只因为同步漏了 10 只」用单一阈值无法区分。彻底失败（同步 0 行）会被复检
  抓到。同理「一轮收敛」= 补齐到无缺口，**不是**可逆还原（重建后行数与删除前不等）。
- **修完必须复检**：否则会重演「补了行情但选股没补上却以为修好了」。
- **通知**：三种结局三种措辞 —— ✅ 通过（每日心跳）/ 🛠 已自动修复 / 🚨 需人工。
  心跳是刻意设计的：静默失败最难排查，「今天没收到消息」本身就是信号。
- **手工使用**：
  ```bash
  python3 scripts/audit_coverage.py --db hs300.db            # 体检
  python3 scripts/repair_gaps.py --db hs300.db --dry-run     # 看会修什么
  python3 scripts/repair_gaps.py --db hs300.db               # 真修
  ```
  **非 21:00 时段手工跑，建议显式传 `--today <上一个交易日>`**：体检默认把「今天」
  也算进应有交易日，跨零点后（如 00:06）当天还没开盘，会报
  `[L1] 今天 行情只有 0 只` 这种假缺口。workflow 里已显式传 `--today`，不受影响。

# 行情数据路径（GH 直连，Cloudflare 不存行情）
- **唯一存储**：GitHub Actions 的 SQLite 缓存（`hs300.db`，actions/cache，10 GB 免费）。
  站点只发布它消费的 JSON（picks/plan/portfolio/decide），**原始行情不进 gh-pages**。
- **Worker 只做两件事**：`/api/decide/*`（决策台持仓，D1 仅 `real_positions` /
  `real_cb_positions` 两张表）与 07:35 / 08:35 UTC 兜底 dispatch cron。
  不要再加第三个职责。
- **不要再把行情写进 D1**：免费版行写入仅 10 万/天，一次 650 天回填 19.5 万行就打爆，
  打爆后的表现是「数据写进去了但游标不推进」，极难排查（2026-10-26 折腾数小时）。
- **已废弃的两条路线**（设计文档留在 `docs/superpowers/specs/`，需要时再查）：
  - Worker 抓取 + D1 存储（2026-10-26）：受 CPU 10ms + D1 行配额双重限制
  - R2 + JSON（2026-10-27）：R2 未开通，用户只用免费额度
- **数据源能力**：东财从 Cloudflare 不可达（实测 520/502），但 **GitHub Actions 可以直连**；
  腾讯 `newfqkline`（`bfq` 不复权）可作为降级源，缺 `amount/turnover`。
- **同步的三个已修缺陷**（别退回去）：
  1. 应有交易日必须来自 `chinese_calendar`，不能只靠「库里已有日期」——否则整天漏抓的
     日子永远补不上（例：10-26 缺、10-27 已入库）
  2. gap-fill 模式不能被 `sync_checkpoints` 短路，否则重试空转
  3. 纯周末缺口段要丢弃（部分数据源对纯非交易日区间返回 501）


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
- **可转债** — 转债数据源只有**实时快照**（`ak.bond_zh_cov()` 无历史日接口），
  所以转债榜单**只能有"当天"这一份**；用 `--date` 指定历史日期会被拒绝
  （`cb_pipeline.build_cb_picks` 返回 warning），因为把今天的快照挂到别的日期下
  是错标数据，比缺失更糟。榜单日期取「库里最后一个交易日」而不是 `today()`
  （否则周末跑会写出 2026-10-25 这种不存在的交易日）。
- **可转债** — `cb_data_provider.py`（akshare 多源降级）、`cb_repository.py`（`cb_` 表读写，写入用 SAVEPOINT 原子化）、`cb_pipeline.py`（编排）、`strategies/convertible.py`（粗筛/打分/预警）。表前缀 `cb_`，与股票体系完全隔离；转债整手 10 张（面值 100 元），风控独立于 `risk_manager.py`（`config.CB_RISK`：止损-8% / 止盈+20% / 单只15% / 10张一手）。分组打分：债底40%/股性30%/条款30%，缺失因子组内重新归一化。数据源：`bond_zh_cov`（全市场）→ `bond_zh_cov_info`（裸 6 位代码）→ `bond_cb_redeem_jsl`（强赎状态）。
- **日期筛选组件** — `templates/components/date_filter.html`（Jinja macro）+ `static/css/date-filter.css` + `static/js/date-filter.js`。五个页面统一用它：picks/plan 用完整版（`date_filter('d','日期')`），cb/decide/portfolio 用 `compact=true`。portfolio 的日期来源是 `/api/portfolio/dates`（只列档位组合真有 `trade_plan` 行的日期；必须排除 `default` 档，否则选中后「当日计划」恒为空）。组合 PnL 支持 `as_of`：`/api/portfolio/summary|detail?date=YYYY-MM-DD` 会按该日重建（`db_repository.rebuild_positions_as_of` 用 trade_events 做移动平均成本还原持仓，价格取该日或更早最近收盘）；不传 date 仍是原「当前状态」计算。能力：‹/› 步进交易日（选项按新→旧排列，方向在 JS 里换算）、`?d=YYYY-MM-DD` URL 状态（停留在最新时不写入参数）、键盘 ←/→/Home/End、加载/错误/成功三态类（`setDateFilterState`）。八态预览见 `docs/previews/date-filter.html`。新增页面用该组件时不要自建日期控件，否则又会出现多套日期交互。
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
- **新增 Cloudflare 资源前先核对免费额度与 token 权限**：给 `wrangler.toml` 加资源绑定
  （R2 / routes / 新 D1 表）需要 token 具备对应权限，否则 `wrangler deploy` 直接失败，
  而 CI 只显示一条报错（2026-10-26 因 `routes` 缺 Zone 权限，部署连续失败数小时）。

## Notes for Future Sessions

- Persistent memory is mandatory (see top-level project instructions). Before non-trivial work, `icm recall "<query>" -t context-a-finder-cli`; after architecture/decision/error/taste events, `icm store` immediately under the matching topic.
- Bash output should go through `rtk` (see top-level RTK rules); e.g. `rtk git status`, `rtk pytest tests/`.
- Known resolved errors live in `.learnings/ERRORS.md` (sync transient failures, `bash nounset` array expansion, `ruff` not installed, console-script missing). Check there before fixing seemingly novel failures — the fix pattern is usually already documented.
- The Chinese design doc at `system-design.md` is aspirational product/market context, not implementation spec.
