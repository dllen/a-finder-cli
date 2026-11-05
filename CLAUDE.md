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

# 策略进化（交易计划已下线，见「已下线功能」一节）
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

# 赞赏（打赏）功能（2026-11-05 加）

站点页脚有「☕ 赞赏支持」按钮 → 打开 Bootstrap 弹窗展示两张收款码：

    static/images/alipay-qr.jpeg   ← 支付宝  1156×1156
    static/images/wechat-qr.jpeg   ← 微信支付 387×387

组件：`templates/components/tip_modal.html`（`base.html` include，两页共用）
+ `static/css/app.css` 的 `.btn-tip` / `.tip-qr` / `.tip-badge-*`。

## ⚠️ 图片是**裁过的**，不是原海报（2026-11-05，用户要求不露昵称）

用户提供的是两张**竖版海报**（支付宝 1708×2560 / 微信 828×1124），底部印着
收款人昵称（「王二小(**鹏)」/「Faker(**鹏)」）。用户要求**不露昵称** ⇒
已裁成「**只有二维码 + 白边**」的正方形。

裁切方法与可验证性（没有 PIL，用 `sips` + numpy）：

    1. sips 转 BMP → 纯 numpy 解析（stdlib + numpy，不依赖 PIL）
    2. 用「某行/列的黑像素数 > 阈值」定位二维码边界框
       ⚠️ 阈值要收紧到**纯黑**（<60）：支付宝海报是深蓝背景（灰度≈106），
       用 110 会把整个蓝底当成二维码，检测出 1036×1813 的非方形框。
    3. 裁到「二维码 ± 白边」（支付宝 60px / 微信 25px），
       **边界落在二维码之外的白边里 ⇒ 二维码一个模块都没丢**。
    4. 回读校验：二维码边长与原始**完全一致**（1036 / 337），
       且留白区**深色像素数 = 0**（证明昵称确实被裁掉）。

`tests/test_tip_feature.py::TestQrImagesAreCropped` 锁死这条：
图片必须是正方形、边长 ≥300、且**不是**原海报尺寸（原尺寸是已知的白名单）。
占位测试已验证能抓到「把原海报塞回来」的回归。

## 两个坑（改这块前先读）

1. **`export_json.py` 必须拷 `static/images/`**。本地 Flask 直接读 `static/`，
   图片永远正常；静态站用的是导出时**拷贝**出来的 `site/static/`，漏拷就 404 ——
   **只在线上复现，本机测不出来**。所以 `tests/test_tip_feature.py` 里
   **真的跑一次 export** 校验图片落地 + 页面 `img src`（去掉 `?v=`）能解析。
2. **收款码按「宽度」控制，不要按高度限。**
   历史教训：**最初直接用原始长海报**（支付宝 1673×2507）配 `max-height:230px`，
   算下来真正的二维码只剩 **~85px**，手机根本扫不出来。
   现在图片已裁成正方形，`.tip-qr{width:100%;max-width:300px;height:auto}`
   + 弹窗 `modal-lg` + 小屏 `col-12 col-sm-6` 叠一列 ⇒ 二维码约 **267px**；
   每张还包了 `target="_blank"` 可点开原图。测试锁死「不许回退 max-height」。

## 图片别压缩

裁切后是 107 KB / 39 KB（合计约 146 KB），**不要再压** ——
二维码压糊就扫不出来。用 `loading="lazy" decoding="async"` 避免拖慢首屏
（弹窗默认隐藏，浏览器不会立刻加载）。

# 已下线功能：交易计划 / 持仓组合 / 决策台（2026-11-05，用户要求）

**用户原话**：「去掉 持仓组合 和 交易计划 功能，下线相关数据」。
澄清后追加确认：**决策台（/decide）一起下线**；数据「停止产出 + 清空表，保留表结构」。

## 现在站点只剩两个页面

| 页面 | 路由 | 状态 |
|---|---|---|
| 每日机会 | `/` | ✅ 保留 |
| 可转债 | `/cb` | ✅ 保留 |
| ~~交易计划~~ | ~~`/plan`~~ | ❌ 已删（路由 404） |
| ~~持仓组合~~ | ~~`/portfolio`~~ | ❌ 已删（路由 404） |
| ~~决策台~~ | ~~`/decide`~~ | ❌ 已删（路由 404） |

## 数据：清空但**保留表结构**

`db/migrations/2026_11_05_remove_plan_features.sql` 清空三张表：

    trade_events     → 0
    open_positions   → 0
    trade_plan      → 0   （清理前 43,627 行）

**为什么用迁移而不是手工 DELETE**：CI 的谱系库（Release `db-lineage` 上的
`hs300.db.gz`）也会执行这个迁移一次（`_applied_migrations` 记账），
否则 CI restore 回来的旧库会一直带着这些行 —— 删了本地等于白删。

**没有动的**：`daily_prices` / `daily_picks`（行情与选股，站点还在用）、
`pick_outcomes`（`/api/stats` 还在用）、`cb_*`（可转债还在）、
`real_positions` / `real_cb_positions`（D1 才是真相源，本地本就是空表）。

## 代码改动清单（恢复时按这个反向加回来）

- **`app.py`**：删 `/plan` `/portfolio` `/decide` 三个页面路由 +
  `/api/plan/*`（5 个）、`/api/portfolio/*`（3 个）、`/api/decide/*`、`/api/holdings`、
  `/api/plan/build`；`/api/dashboard` 改成只返回 `last_refresh` + `cb`。
  771 → 349 行，只剩 12 个路由。
- **`export_json.py`**：停掉 `plan-*.json` / `plan-dates.json` / `decide-*.json` /
  `holdings.json` / `data/portfolio/` / `plan.html` / `portfolio.html` / `decide.html` 的产出。
  ⚠️ **仅「不再生成」是不够的** —— 老产物会留在 `site/` 被一起推上 gh-pages，
  站点等于没下线。所以 `_cleanup_stale_date_files()` 现在会**主动删除**这些
  （见 `OBSOLETE_DATA_GLOBS` / `OBSOLETE_DATA_DIRS` / `OBSOLETE_PAGES`）。
  实测一次导出清掉 **555 个**旧文件。
- **`cli_layer.py`**：删 `plan` 子命令（`build` / `build-all` 及其 handler）。
- **模板/前端**：删 `plan.html` `portfolio.html` `decide.html`；
  删 `static/js/{plan,portfolio,decide,decide_compare,decide_export}.js`；
  `nav.html` 只留两个入口；`api.js` 删对应函数。
- **`a-finder.sh`**：删 `cmd_plan` / `cmd_plan_all` / `cmd_daily_plan` / `cmd_portfolio`
  及其 dispatch；`cmd_site` / `cmd_refresh` 不再跑 `plan build*`。
- **`deploy-site.yml`**：`选股 + 计划 + 组合 + 可转债` 步骤改名，去掉三条 `plan build*`。
- **`scripts/audit_coverage.py`**：**关闭 L3（计划）体检**。
  ⚠️ 不改的话**每个交易日都会报「缺档位」**（表已清空）⇒ daily-audit 每晚必红。
- **`scripts/repair_gaps.py`**：删 `_plan_l3` / `_backfill_cmd`，
  `_plan_l1` / `_plan_l2` 不再连带 `plan build*`。
  `side_effects`（open_positions/trade_events 行数变化）机制**保留** ——
  将来任何会写这两张表的修复动作仍需靠它暴露「数字自己变了」。

## 测试

删除 9 个只测已删接口的文件（`test_api_portfolio` / `test_cli_plan` /
`test_cli_plan_portfolio` / `test_decide*` / `test_export_portfolio` /
`test_integration_plan` / `test_web_plan`）；改写 `test_audit_coverage`（L3 不再报）、
`test_repair_gaps`（无 L3 动作）、`test_export_static`（断言下线产物**不存在**）。
**823 passed**。

## 遗留 / 未做（恢复或后续处理时注意）

1. **`worker/index.js` 的 `/api/decide/*` 仍在线**（D1 里的 `real_positions` 是用户
   自己录入的真实持仓，与本次删除的派生数据无关）。站点已无页面调用它，
   但 API 本身仍可访问。要彻底下掉需改 `worker/**` 并重新部署 Worker。
2. **刷新按钮变成「无页面挂着」**：`/api/refresh` + 本机 `refresh_agent.py` 这套
   基础设施保留（它原本挂在决策台页面上）。`api.js` 仍导出 `fetchRefresh` 系列，
   将来重新接入时直接用。
3. **`cb.html` 的「刷新」按钮没有 `show_write_controls` 服务端守卫**
   （既有问题，与本次下线无关）：静态站靠 `api.js` 用 JS 隐藏 `.write-control`，
   属于「假隐藏」。`picks.html` 的写入控件是有服务端守卫的。
4. **库里的库内模块没删**（`plan_builder.py` / `decide_service.py` /
   `db_repository` 里的 plan 函数 / `shared_lib/plan_rows.py`）：
   它们不再被页面调用，但保留着可降低回滚成本。要彻底删需连带清理引用与测试。

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
  2. 日常窗口只补最近 60 天（冷启动 **450 天**，见「两谱系分叉」一节），并发/限流下调到 6/6、重试 2 次；
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
- ~~`hs300.db` 缓存 key 按 `run_id` 唯一~~ —— 2026-11-04 起谱系改走 Release 资产，
  不再有 key/restore-keys 语义，所以这条**只对历史方案有效**，别再照着改。
- **每周体检 + 自愈 + 备份**（`gap-heal.yml`，周日 10:00 北京）：`scripts/check_gaps.py`
  扫最近 30 个交易日覆盖度 → `sync-hs300-range --gap-fill` 补抓 → 再扫一次（仍有缺口则红）
  → 顺手把 `hs300.db` 压成 artifact（90 天），缓存丢失时可秒恢复不必重抓 450 天。
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
   冷启动 **450 天**窗口重建，别指望每天补一点）。
2. **Restore 必须在 freshness 之前**。判据要读自己的库，就得先把库恢复出来。
   顺序反了就只能读线上站点 —— 那正是事故本身。
3. **`skip_sync` 与「要不要发布」分开决策**。混成一个 skip 会导致：
   库已新鲜但线上缺当天 → 所有人都跳过 → 当天没人发布。
   所以另有 `site_stale`（线上缺当天就仍要发布）。
4. **谱系存储已换成 GitHub Release 资产，不再用 `actions/cache`（2026-11-04）**。
   原因：`actions/cache` 在本仓库**双向都不工作** —— `Post Save` 与 `Post Restore`
   一直 `skipped`，条目永久停在 10-27/10-29 两条 **5.2 MB** 的残库；
   实测缓存总用量才 **~200 MB / 10 GB**，所以**不是配额问题**。后果是
   gap-heal 拿到**空库**：体检 30 天全 0 只、补抓后历史深度 0 根，
   而步骤只显示一个 `failure`，根因完全不可见。
   - 存储：**Release tag `db-lineage`** 上的两个资产 —— `hs300.db.gz`（gzip）+
     `lineage-meta.json`（最新行情日 / 历史深度 / 交易日数 / `lineage_ok`）。
     单一名义资产 + **覆盖**语义，不再有「哪个 run 的 key 最新」这种隐式规则。
   - 读写：`scripts/db_lineage.py {restore,save,show}`（纯 stdlib + REST API，
     需要 `contents: write`，四个 workflow 都已改）。`restore` 无资产时返回 **rc=2**
     （= 正常冷启动，不要判红）。
   - **单调性守卫**：`decide_save()` 在写入前比对线上 meta —— 本地最新日比线上旧、
     或历史更浅，就**拒绝覆盖**。`actions/cache` 是无条件 last-writer-wins，
     谱系回退正是这么来的。
   - **不合格的库绝不覆盖谱系根**（等价于原来的 `degraded-` 前缀隔离）：
     各 workflow 把「体检干净 / `prices_ok` / `clean` / lag==0」作为 `--lineage-ok`。
   - 存之前仍要**瘦身**（`scripts/slim_db_for_cache.py`，四个 workflow 都有）：
     `trade_plan`（含索引）占全库 **94%**，而它是**派生表**（`daily_picks` +
     `daily_prices` 可重算，CI 每次跑 `plan build-all --backfill`）。
     **`daily_prices` / `daily_picks` 一行都不动** —— 行情必须全留，
     `pick_history` 按每只票 221 根 K 线过滤。窗口 ≥ 45 天（审计窗口 30 天 + 余量）。
   - ⚠️ 瘦身**必须原地**（文件名仍是 `hs300.db`）：下游与 `db_lineage save`
     都按固定文件名找库，换名字早晚漏一处 ⇒ 表现为「库里没数据」。
   - **⚠️ `trade_plan` 会被回补撑大（2026-11-05 实测）**：
     `plan build-all --backfill` **逐轮追加**、每轮用不同的 `params_hash`，
     于是同一个 `(日期, 档位, code)` 有**多条**行 —— 一个交易日能到 5000 行。
     本机实测 **1,276,354 行 / 499 MB**（264 个交易日 × 11 档位）。
     清理：`scripts/clean_trade_plan_rounds.py`（按**代码库自己的** canonical 规则
     `db_repository.canonical_plan_round` 保留每个 `(日期,档位)` 的 canonical 轮次）
     → **43,621 行 / 47 MB**（VACUUM 后）。
     - 安全性：canonical 轮次本来就是「行数最多」⇒ 删掉别的轮次**不改变**
       canonical 轮次是谁 ⇒ **读路径结果不变**（已对全部 2904 个组合校验 0 处不一致）；
       且它是**派生表**，丢了能由 `plan build-all --backfill` 重算。
     - ⚠️ **清理不能替代读路径去重**：下一轮回补又会长出多行，
       所以「库里少存 + 读时也去重」**两处都要做**。
     - ⚠️ 脚本与 `db_repository.canonical_plan_round` **必须同规则**，
       否则「脚本以为该留的被删了，读路径改选另一轮 ⇒ 前端数据悄悄变了」。
       有测试交叉校验（`tests/test_clean_trade_plan_rounds.py`）。
5. **本地兜底只在「CI 已经放弃」时动手**：过了 deadline（默认 17:00 北京）
   线上仍落后才算数。本地抢跑本身就在制造分叉。
- **（历史）缓存 key 必须带 run_id**：这条只适用于已经弃用的 `actions/cache` 方案
  （固定 key 会让缓存永久冻结，2026-10-26 的 `hs300-db` 就是这样）。
  2026-11-04 起谱系改走 Release 资产，**key 语义不复存在**；
  但仍保留「谱系孤儿 → 冷启动」的判据：`db_freshness` 的 rc=3 与
  `--min-history-bars 221`，落在新方案里就是「restore 后先体检再决定要不要重建」。
- **⚠️「一条库能不能用」要看三个维度，缺一不可**（2026-11-01 查明）：
  **最新日**（够不够新）+ **覆盖只数**（全不全）+ **历史深度**（够不够算）。
  少一个维度就会出现「看起来很新、实际算不出东西」的库 ——
  实测 CI 缓存库最新日是最新的、覆盖也够，但历史只有 54 个交易日，
  而 `pick_history` 的 `build_market_from_db(min_days=221)` 按
  **每只票的 K 线根数**过滤 → 0 只票 → 0 选股 → 导出没有新日期
  → **站点停在 10-29，而行情其实已同步到 10-30**。
  - `scripts/db_freshness.py` 新增 `--min-history-bars`（CI 两个 workflow
    显式传 221）：历史深度不够 ⇒ 判**陈旧谱系**（rc=3）⇒ 冷启动。
  - **增量补缺口不会让历史变长** ⇒ 这类问题只能靠冷启动解开（默认不检查，CI 显式开启）。
  - **⚠️ 冷启动窗口必须 ≥ 450 天，400 天不够（2026-11-05 实测）**：
    实测 `2025-11-05~2026-11-04`（400 天）只拿到 **216 根** < 221 ⇒
    `db_freshness --min-history-bars 221` **永远判它「陈旧谱系」** ⇒
    这条谱系永远当不了谱系根，而且**它不报错**（同步成功、库有数据，
    只是永远不合格）。从 `2025-10-26` 起（374 天）实测 **222 根** ✓。
    ⇒ 三个 workflow 的冷启动窗口都设 450 天（deploy-site / daily-audit /
    gap-heal）。**别再退回 400 天**。
  - **⚠️⚠️ 迁移用了新 SQLite 语法 ⇒ CI 冷启动直接崩（2026-11-05，本轮最难的坑）**：
    `db/migrations/2026_10_24_convertible_bond_constraints.sql` 用了
    `ALTER TABLE ... ALTER COLUMN ... SET NOT NULL`（SQLite >= 3.53）。
    本机 sqlite 3.53.1 正常，但 **GitHub runner 的 python3.11 链接的 SQLite 更老** ⇒
    全新库上 `open_db()` 抛 `sqlite3.OperationalError: near ALTER: syntax error`
    ⇒ 同步 **3 秒**就退出 ⇒ 行情一行没抓 ⇒ 谱系永远建不起来。
    - **为什么本机永远复现不出来**：本地库的 `_applied_migrations` 早记过这个文件，
      **直接跳过** —— 只有「**全新空库**」才会踩到。排查这类问题时，
      **必须用 `rm -f x.db` 之后的全新库复现**，不能拿现有库当证据。
    - 修法：迁移文件可声明 `-- requires-sqlite: 3.53`；`_run_migrations` 解析它，
      版本不够时**跳过并记 warning**（照样记账），而不是让整条流水线崩。
      跳过是安全的：该迁移只为**存量库**补约束，全新库在 base 迁移里就带着约束建表。
    - 测试：`tests/test_migration_sqlite_gate.py`（含「模拟老 SQLite 不崩」）。
    - **推论**：以后任何新迁移若用到较新语法（`ALTER COLUMN` / `DROP COLUMN` /
      `RENAME COLUMN`），**必须**加 `-- requires-sqlite:` 声明，否则 CI 冷启动会静默失败。
    - 排查手段备忘：job 日志要 admin 权限（未认证 403），`artifact` 下载要认证，
      但 **run 页面的 Step Summary 与 annotation 不需要认证**（网页可读）——
      所以「把关键信息写进 `$GITHUB_STEP_SUMMARY` / `::error::`」是唯一可靠的观测通道。
      ⚠️ annotation 只显示**一行**，而 traceback 首行永远是 Traceback (most recent call last):
      ⇒ 必须打**最后几行**（异常类型与消息在末尾）。
  - **release 谱系已在 CI 真正跑通（2026-11-05 04:48 UTC 实证）**：
    `db-lineage` release 上有 `hs300.db.gz`（3.69 MB）+ `lineage-meta.json`，
    meta 为 `max_trade_date=2026-11-04 / codes=300 / min_bars_per_code=222 /
    lineage_ok=true`；下载解压后 `integrity_check=ok`、300 只、88,909 行、222 根。
    这就是「不再依赖 actions/cache」的实证。
    ⚠️ **上面那次是「跑通了但数据停在 11-04」** —— 又踩了两个「步骤 success
    但没做成」的坑，2026-11-05 13:28 的 run `42036072366` 才真正推到 11-05
    （`max_trade_date=2026-11-05 / 89,209 行 / 223 根`，下载解压后 integrity ok）。
    两个坑 + 一个隐性依赖，都值得单独记：

    - **坑 A：瘦身崩了，但 run 依然 success**。
      `slim_db_for_cache.py` 里我把 `deleted_rounds` 只写在 `if latest:` 分支内 ⇒
      CI restore 回来的谱系库 **trade_plan 是空表**（上次瘦身剩 0 行）⇒
      `if latest` 不成立 ⇒ **UnboundLocalError**。workflow 里是：

          if python3 scripts/slim_db_for_cache.py --db hs300.db; then ...
          else echo "::warning::瘦身失败 —— 仍存原库（上传更大但不会丢数据）"

      ⇒ **整个 run 依然 success**，只在 annotation 里留一句「瘦身失败」，
      极容易当成无害告警。**瘦身失败 = 谱系一直不瘦身**，不是小事。
      测试：`tests/test_slim_db_edge_shapes.py`（空 trade_plan / 缺列 都不许崩）。
    - **坑 B：当天永远不算缺口 ⇒ 当天永远补不进谱系**。
      `check_gaps.py` 之前是「**整天**排除当前日历日」（怕收盘前报假缺口，
      这个修复本身是对的，见下一条）。但 gap-heal 在**收盘后**跑（实测 20:34 北京）时，
      当天数据**已经有了**，却仍被排除 ⇒ 11-05 从不被当成缺口 ⇒ 谱系卡在 11-04。
      ⇒ 改成**按时间**判断：北京时间 **15:30** 之后当天就算期望
      （`DATA_READY_HOUR/MINUTE` + `_beijing_now()`）。
      **两头都要对**：收盘前排除（避免假缺口）、收盘后包含（否则当天永远补不进来）。
      测试：`tests/test_check_gaps.py::TestTodayIsTimeAware`（monkeypatch 固定时间）。
    - **坑 C（隐性）：`TZ` 漏配**。GitHub runner 默认 **UTC**，
      `dt.date.today()` / `bash date +%F` 取的是**系统本地时区** ⇒
      北京 00:00~08:00 跑时「今天」会算成**前一天**。
      `deploy-site.yml` 与 `daily-audit.yml` 早就设了 `TZ: Asia/Shanghai`，
      **`gap-heal.yml` 与 `weekly-evolve.yml` 漏了**（2026-11-05 补上 gap-heal）。
    - **坑 D（最隐蔽）：脚本方式运行时的 import 路径**。
      CI 用 `python3 scripts/xxx.py` 调用 ⇒ `sys.path[0]` 是 **`scripts/`** ⇒
      **仓库根不在导入路径上** ⇒ `from shared_lib.plan_rows import ...` 直接
      `ModuleNotFoundError`。而本机 `.venv` 是 **uv** 建的、**会把项目根塞进
      `sys.path`** ⇒ **pytest 全绿也测不出来**。
      ⇒ 脚本内加 `sys.path.insert(0, str(Path(__file__).resolve().parent.parent))`
      （与 `audit_coverage.py` / `db_freshness.py` / `repair_gaps.py` 一致）。
      测试：`tests/test_scripts_run_as_scripts.py` —— **必须用「干净解释器」**
      （`sys.path` 里没有仓库根，= CI 的情形）以 subprocess 跑脚本；
      用 venv 的 python 测等于没测。
  - **冷启动本身很快**（2026-11-05 实测，全新空库）：
    `sync-hs300-range --gap-fill` 450 天窗口 **41 秒**、80,262 行、300 只。
    腾讯 `newfqkline` 的 `count` 是「从 end_date 往回取 N 根」，
    count=400 一次就能拿到 242 根 ⇒ **不需要多次请求**。
    之前以为「400 天要抓很久」是误判 —— 慢的是 `sync-hs300-meta`
    （300 只逐个拉基本面，本机 ~3.7 分钟）。
  - **⚠️ 共用这份缓存的工作流有四个，改缓存/谱系逻辑时必须四个一起看**：
    | workflow | 何时写 | 谱系根守卫 |
    |---|---|---|
    | `deploy-site` | 交易日 15:30~17:30 北京 | `prices_ok` + 历史深度 |
    | `daily-audit` | 每天 21:00 北京 | 体检/复检干净 + 历史深度 |
    | `gap-heal` | 周日 10:00 北京 | 30 天无缺口 **+ 历史深度**（补抓前不够就扩到 450 天窗口） |
    | `weekly-evolve` | 周五 | 最新日 lag==0 **+ 历史深度** |
    只修其中两个等于没修：漏掉的那个会把短历史库存成**最新**条目，
    而 `restore-keys` 取最新 ⇒ 别的 workflow 下次又捡回短历史库。
    实测（2026-11-01）：gap-heal 跑完存的 artifact 与 10-29 的**逐字节同大小**，
    证明 90 天补抓没增加任何历史长度。
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
  - **同口径 ≠ 同字段（2026-11-04 新增 `tencent_bfq` 源）**：东财 `push2his`
    不可达时备源链退到新浪，而新浪 `getKLineData` **只给 OHLC + 成交量**，
    `amount`/`turnover`/`pct_change` 全缺 ⇒ 落库全是 `0.0` ⇒
    「量价齐升」策略的 `pct_change` 判据与回测的成交额口径**静默失效**
    （不报错，算出来全错）。腾讯 `newfqkline` 的 `bfq` 口径与东财 `fqt=0`
    **完全一致**（实测 300 只 × 2 日逐字段对得上）且字段齐全 ⇒ 单列为
    `tencent_bfq`，排在新浪**之前**（同口径下优先选字段全的）。
    ⚠️ **两个腾讯源口径不同，别互相顶替**：`tencent`(fqkline+qfq)=前复权 /
    `tencent_bfq`(newfqkline+bfq)=不复权。
    ⚠️ `tencent_bfq` 的**成交量单位按板块不同**：主板是「手」、科创板 688xxx
    是「股」（688008 给 46199662 = 461997 手）。库里统一存「手」⇒ **688 要 /100**。
    ⚠️ `newfqkline` 的 `count` 是「从 end_date **往回**取 N 根」，不是往后取 ——
    窗口给小了会**静默少一天**。
    ⚠️ 腾讯不给涨跌幅，`pct_change` 由**前收盘价**自算；`field[7]` 是**换手率**
    不是涨跌幅（000001 11-04 换手 0.74% 但跌 0.2549%）—— 搞混则动量选股全错。
    测试：`tests/test_tencent_bfq_source.py`。
  - **腾讯 `newfqkline` 在本机可用（2026-11-04）**：东财 `push2his`（K 线）被墙
    （`RemoteDisconnected`），但 `push2`（实时报价）通 ⇒ 用 `f47`/`f48`/`f168`/`f170`
    可交叉校验落库的 volume/amount/turnover/pct_change。

# 每日巡检与自动补齐（daily-audit.yml）
- 每晚 21:00 北京（13:00 UTC，周一到周五）独立跑一次体检。与 deploy-site 是各自独立的
  workflow，**失败互不影响**（一边红了不拖累另一边），但**共用同一份 hs300.db 谱系**
  （Release `db-lineage` 的 `hs300.db.gz`）——「解耦」指失败隔离，不是数据隔离。
  ⚠️ 写者仍是四个，改谱系逻辑必须四个一起看（见「两谱系分叉」里的那张表）。
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
    东财/新浪 `fqt=0` 不复权，混写会在除权日造出假跳空）→ **按已有行的口径再分**：
    - 已有行**全是不复权**（`Gap.bases == ["none"]`）⇒ 这个半截天是
      **同步被中断**造成的（并发处理 300 只时被打断，前面写进去了后面没写完；
      实测只数单调递减 143→140→137→…→65 正是这个形状）。补齐剩下的票
      **不会混口径** ⇒ 走**宽窗**，自动补齐。
    - 已有行含**前复权**（`qfq`）或**混了两种** ⇒ 补不复权的行会混口径 ⇒
      窄窗 `MAX_PRICE_DAYS = 7` 个交易日，超期只报警。
    - 口径**不可得**（旧报告没有 `bases`）⇒ 保守按上面那条处理。
  - 分类依据是 `Gap.codes`（当天实际只数）与 `Gap.bases`（当天已有行用到的
    口径集合）。**报告里没有这两个字段（旧报告）→ 按部分缺口 + 口径不可得
    处理**（保守：宁可少修，不可混口径）。
  - 窗口按**交易日**算，不是日历日：国庆 8 个日历日只有 5 个交易日，
    日历日窗口在长假后必然错判。日历不可用 → 一律当超窗（只报警）。
  - **为什么必须改**：2026-10-30 巡检报「无法自动修复的缺口」L1 10-14~10-30
    共 13 天。旧实现对所有 L1 缺口统一用 7 天日历窗 → 这 13 天**永久超窗** →
    每晚重复报警、永远不修、run 永远红。而且**只放宽窗口还不够**：10-23~10-30
    那天是**尝试过但补抓失败**（东财不可达；10-29 的数据只有东财有，
    腾讯/新浪只到 10-28）—— 补抓能不能成功取决于数据源，不是窗口。
- **陈旧谱系自愈**（2026-10-30 加）：`daily-audit.yml` 在体检**之前**跑
  `scripts/db_freshness.py`（rc=3 = 落后 > 3 个交易日 = 陈旧谱系）→ 弃库
  (`rm -f hs300.db`) → 450 天冷启动同步 → 再体检。谱系断了太久时在烂谱系上
  打补丁必然被限流打断、缺口又变成「超窗只报警」⇒ 永远红。
  冷启动同步要**放在体检之前**：谱系缺 450 天会报出 450 天的 L1 缺口，
  逐日补抓要几百次请求，必然被限流。
- **通知必须点名正确的产物**（2026-10-30 改）：本地库与 CI 缓存库
  （Actions Cache 里的 hs300.db）是两条独立漂移的谱系。旧的建议
  「手动跑 `bash a-finder.sh fallback`」指向的是**本地库** —— 本地库健康时
  跑它毫无作用，真正坏掉的缓存条目一直坏着。现在按层分开给建议：
  L1 → 重建 **CI 缓存库**（删 Actions 缓存条目 / dispatch deploy-site）；
  L2/L3 → 在 CI 缓存库上跑 `scripts/repair_gaps.py`（纯重算）；
  恒定一行提示检查东财是否限流。
  同理，通知的「原因」要**按类别分组**（`repair_failed` / `missing_lineage` /
  `partial_basis_risk` / `partial_uniform_out_of_window` / `partial_unknown`），
  不能把「尝试过但失败」与「压根没试」混成一句 ——
  混了会把真正在窗内、真正失败的那几天掩盖掉（2026-10-30 实际就掩盖了）。
  `partial_uniform_out_of_window` 是「口径一致但超了自动修复窗」——
  它**没有**混口径风险，人工补抓一下即可，别和真有风险的混在一起说。
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
- **⚠️「退出码 0」不等于「做成了」（2026-10-31）**：GitHub Actions 里
  **没有 `always()` 的步骤，在上游失败时不会跑**，它的 output 是**空字符串**
  （不是 '0' 也不是 '1'）。判据写成 `== '1'` 时，空字符串被判成「没发生」
  而不是「没跑成」—— 方向正好相反，最危险。
  - 体检/修复/复检三步都带 `always()`；红灯条件必须覆盖
    「audit_rc 为空」「recheck_rc == '2'」「cold_sync_failed」。
    **冷启动彻底失败曾经是绿的 run**（audit_rc 为空 ⇒ 旧条件全假）。
  - `pick_history` 一条榜单都没产出时必须 `raise SystemExit(1)`：
    「无可用行情数据」曾走 early-return、什么都不写却退出 0 ⇒
    修复脚本据此报告「L2 已修复」，复检还是报 L2 缺，
    「一轮收敛」要跑第二轮才成立，中间那轮看起来是绿的、日志里全是 ✓。
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
- **唯一存储**：GitHub Release `db-lineage` 里的 `hs300.db.gz`（2026-11-04 起；
  之前是 `actions/cache`，因双向失效已弃用，见上面「谱系存储」一节）。
  站点只发布它消费的 JSON（picks/plan/portfolio/decide），**原始行情不进 gh-pages**。
- **Worker 只做三件事**（第三件 2026-11-03 加，见「刷新按钮」一节）：
  1. `/api/decide/*`（决策台持仓，D1 仅 `real_positions` / `real_cb_positions` 两张表）
  2. 07:35 / 08:35 UTC 兜底 dispatch cron
  3. `/api/refresh*`（刷新任务队列，D1 仅 `refresh_jobs` 一张状态表）
  **三个职责都不做计算**：行情/选股/计划全在本机 Python 里。
  不要再加第四个职责。
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


# 刷新按钮（本机唯一发布者，2026-11-03）
- 决策台（`/decide`）登录后有「刷新数据」按钮 → Worker `POST /api/refresh`
  → D1 `refresh_jobs` 排队 → 本机 `refresh_agent.py` 轮询领任务 →
  `bash a-finder.sh site --push` → push gh-pages → Cloudflare 同步 Assets → 页面轮询看结果。
- **触发方向是拉模式**：Worker 在边缘够不到本机 `127.0.0.1`，
  由本机轮询 `/api/refresh/next` 领任务（`X-Agent-Secret` 头认证）。
  agent **只做出站请求，不监听任何端口**。
- **gh-pages 的唯一日常写者是本机**：`deploy-site.yml` 已删掉
  `peaceiris/actions-gh-pages` 步骤（CI 仍同步+计算+维护缓存谱系，
  但**不发布**）。`daily-audit.yml` / `gap-heal.yml` 仍保留发布权
  （极低频，只在修复缺口时）。
- **两套认证故意分开**：前端端点用登录 cookie（`decide_auth`），
  agent 端点用独立的 `REFRESH_AGENT_SECRET` 头。共用会让一个泄露
  同时丢掉登录态和 agent 通道。
- **发布前有单调性守卫**：`cmd_site` 用 `verify_site_export.py --not-older-than`
  比对线上最新日，比线上旧就**不发布**（2026-10-29 事故的直接教训）。
  线上 HTTP 读不到时兜底读 `origin/gh-pages:data/dates.json`，
  **两个来源都拿不到就 fail closed**（拒绝发布）。
  ⚠️ 兜底**不能**退回 `plan_date` —— 它与产物同源，比较恒真，守卫形同虚设。
- **`cmd_site` 的计划日期对齐 `MAX(daily_prices.trade_date)`**（不是今天）——
  同步失败时按今天生成计划会产出**空计划**。CI 早已这么修，本机这次跟上。
- **过期清理**：`refresh_jobs` 的 pending>10min / running>60min 自动置 `failed`。
  建任务时先清理再用**一条原子 `INSERT ... WHERE NOT EXISTS`** 抢占。
  少了过期清理，agent 崩一次就让刷新永远 409（整个功能报废）。
- **agent 必须带自定义 User-Agent**：Cloudflare 会直接 403 掉 urllib
  默认的 `Python-urllib/3.11`，请求**到不了 Worker**。
  表现是「secret 配对了却一直 403」，极难联想（curl 测同一端点却正常）。
- 运维：`bash a-finder.sh refresh-agent {start|stop|status}`，
  日志 `logs/refresh.log`（流水线）与 `logs/refresh-agent.log`（agent 自身）。
  耗时 15~35 分钟（行情同步 + 历史回补），按钮会轮询显示阶段。
- 部署前必须配好的 secret：`REFRESH_AGENT_SECRET`
  （`wrangler secret put` + 本机 `export` 同值；`deploy-worker.yml` 已接入同步）。
  没配的话 agent 端点一律 401，前端会一直 pending。

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
- **Web** — `app.py` (Flask, ~350 lines) serves Jinja2 templates from `templates/` and static assets from `static/`. Pages: picks (`/`), cb (`/cb`)。**（2026-11-05 起 `plan` / `portfolio` / `decide` 三个页面已下线，见下节）** JS is Vanilla ES6 modules in `static/js/` (no jQuery). Bootstrap 5.3.3 and Chart.js 4.4.1 are vendored locally in `static/vendor/` (no CDN dependency). Dual deployment: Flask API mode and static JSON+HTML mode (gh-pages via `export_json.py`). Static asset URLs carry `?v=<git-sha>` cache-busting (Cloudflare serves `static/*.js` with `max-age=14400`).
- ~~**10w 决策台**~~ — **已于 2026-11-05 下线**（见「已下线功能」一节）。以下是当时的设计记录，仅供恢复时参考：`/decide` 按 `config.PORTFOLIO_10W`（8w 股 + 2w 债、≤8 只、单票 ≤10%、总仓位 ≤75%）展示今日执行清单，对照用户手工录入的真实持仓（`real_positions` / `real_cb_positions`，仓储 `real_position_repository.py`）高亮偏差。判定逻辑在 `decide_service.py`（纯函数，10 类偏差 × info/warn/danger）。**读取 `trade_plan` 的 10W 档位必须按 code 去重**——回补会用不同 `params_hash` 追加重复行。
  ⚠️ **这条以前只做到一半（2026-11-05 修）**：去重键写的是 `(code, action)`，
  而 `buy` 与 `hold` 是**不同的 action** ⇒ 同一只票既能 `buy` 又能 `hold` 时两个键都不同
  ⇒ **都能通过去重 ⇒ 组合页上同一只票渲染两行**（实测线上 5W 重复 3 只、50W 重复 4 只）。
  现在统一走 `shared_lib/plan_rows.dedupe_plan_rows`：**每个 code 只留一行**，
  多行时按 `ACTION_PRIORITY = {buy:0, hold:1, exit:2}` 保留更可执行的那条。
  `export_json.py` 与 `app.py` **各有一份读路径** ⇒ 规则必须共用这个模块，否则两份必然漂移。报告见 `docs/reports/10w-decision-system.md`。
- **持仓云端化（Worker + D1）** — ⚠️ **2026-11-05：决策台下线后，这条链路只剩 Worker 侧的 API 在跑，站点已无页面使用它**（`/api/refresh` 刷新队列仍在用）。恢复决策台时再按下面的记录接回来。持仓的唯一真相源是 **Cloudflare D1**，由 `worker/index.js` 提供 `/api/decide/*`（读公开、写需登录的 HttpOnly Cookie）。**登录是双因子**：`DECIDE_USER` + `DECIDE_PASSWORD`，签名密钥 `DECIDE_SECRET`，三者都走 `wrangler secret put`；前端用 Bootstrap Modal（`templates/components/decide_login_modal.html`），不使用浏览器原生弹窗。本地 SQLite 同名表保留但不再写入。**对比逻辑有 Python 与 JS 两份实现**：`decide_service.py`（服务端/导出）与 `static/js/decide_compare.js`（前端实时算），由 `tests/test_decide_compare_parity.py` 用 node 跑同一 fixture 保证逐项一致；配置来源 `data/decide-config.json`。D1 建表见 `worker/schema.sql`，部署见 `wrangler.toml`（路由 `ycu.space/api/*`）与 `scripts/deploy-worker.sh`。
- **Shared plan/backtest** — `shared_lib/strategy.py` exposes `PlanRow`, `params_hash`, `compute_plan_prices` and re-exports `RiskManager`, `PositionConfig`, `default_candidate_config`. Treat it as the single source of truth — both `plan_builder` and `ma_backtest` import from here.

## Conventions

- Configurable defaults only via `config.py` (capital tiers, risk constants, sync sleep). New tier values require updating `config.CAPITAL_TIERS` and the CLI validator.
- SQLite is the only DB; no ORM. Repository code in `db_repository.py` is the only place that issues raw SQL for domain tables — CLI/UI layers go through it.
- Logs land in `logs/fetch_success.log` and `logs/fetch_failed.log` during sync; web log is `web.log`; daemonized CLI log is `a-finder.log` (via `a-finder.sh daemon`).
- New strategies: drop a detector in `strategies/`, register in `strategies/__init__.STRATEGIES`. For factor strategies, extend `multi_factor_base.py`.
- New schema columns: add a `db/migrations/<YYYY_MM_DD>_<name>.sql` file; do not edit `db_schema.py` for already-deployed DBs.
- **改 workflow 的 `run:` 块后必须验 YAML**：`run: |` 是块标量，
  **缩进 0 的行会直接截断它** ⇒ 整个 workflow 文件变非法 YAML、根本加载不了。
  内联 Python 用**单行** `python3 -c "..."`，或 heredoc 但每行都缩进到与
  run 块同级（YAML 会统一剥掉公共缩进）。校验：
  `python3 -c "import yaml;yaml.safe_load(open('.github/workflows/x.yml'))"`
  + 把每个 `run:` 块喂给 `bash -n`。（2026-11-01 实际踩过，已修回。）
- **新增 Cloudflare 资源前先核对免费额度与 token 权限**：给 `wrangler.toml` 加资源绑定
  （R2 / routes / 新 D1 表）需要 token 具备对应权限，否则 `wrangler deploy` 直接失败，
  而 CI 只显示一条报错（2026-10-26 因 `routes` 缺 Zone 权限，部署连续失败数小时）。

## Notes for Future Sessions

- Persistent memory is mandatory (see top-level project instructions). Before non-trivial work, `icm recall "<query>" -t context-a-finder-cli`; after architecture/decision/error/taste events, `icm store` immediately under the matching topic.
- Bash output should go through `rtk` (see top-level RTK rules); e.g. `rtk git status`, `rtk pytest tests/`.
- Known resolved errors live in `.learnings/ERRORS.md` (sync transient failures, `bash nounset` array expansion, `ruff` not installed, console-script missing). Check there before fixing seemingly novel failures — the fix pattern is usually already documented.
- The Chinese design doc at `system-design.md` is aspirational product/market context, not implementation spec.
