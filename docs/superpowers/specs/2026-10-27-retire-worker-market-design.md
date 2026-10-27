# 退役 Worker 行情层，回到「GH 直连 + SQLite 缓存」— 设计文档

日期：2026-10-27
状态：待评审
取代：[`2026-10-27-r2-json-storage-design.md`](2026-10-27-r2-json-storage-design.md)（R2 未开通，方案作废）
相关：[worker/index.js](../../../worker/index.js) · [worker/schema.sql](../../../worker/schema.sql) · [wrangler.toml](../../../wrangler.toml) · [deploy-site.yml](../../../.github/workflows/deploy-site.yml) · [sync_service.py](../../../sync_service.py)

## 目标

把行情数据路径收敛回最简单形态，并让**所有 Cloudflare 资源都落在免费额度内**（不额外付费、不开通 R2）：

```
GitHub Actions ──直连东财/腾讯──▶ SQLite（Actions cache，10 GB 免费）
        │
        └──▶ export_json.py ──▶ gh-pages 静态 JSON（站点唯一数据源）

Cloudflare Worker 只做两件事：
  1. /api/decide/*   决策台持仓读写（D1，几百行，永不超配额）
  2. 07:35 cron      兜底触发 GitHub Actions（解决 schedule 被丢弃）
```

## 非目标

- 不改任何选股/计划/组合/转债的 Python 计算
- 不改站点前端与 `export_json.py` 的输出结构
- 不做盘中实时、不做行情公开 API

## 为什么退役（三条硬事实）

1. **R2 未开通**：原计划把 JSON 存 R2，需要后台开通（绑卡验证），用户明确不开通。
2. **D1 免费行写入仅 10 万/天**：一次 650 天历史回填 = 19.5 万行，必然打爆；打爆后的表现是「数据写进去了但游标不推进」，错误极不直观（2026-10-26 排查了数小时）。
3. **东财从 Cloudflare 不可达**（实测 HTTP 520/502），Worker 只能拿到腾讯的当日 OHLCV，既存不了历史、也没有比 GH 更强的能力。

结论：Worker 存行情这件事**收益为零、成本远超免费额度**。而 GitHub Actions 本来就能直连数据源、缓存 10 GB、发布静态站 —— 这套组合已经跑了数月，免费且够用。

## 删除清单

| 类别 | 内容 | 备注 |
|---|---|---|
| Worker 代码 | `worker/market/` 整个目录（现有 `api.js` / `repo.js` / `runner.js` / `jobs.js` / `sources.js`） | 为 D1 存储而写，随之退役 |
| Worker 接线 | `worker/index.js` 中 market 路由分发（`handleMarketApi`）与 `scheduled` 里的 `runMarketSync` 调用 | 保留 dispatch 分支 |
| cron | `wrangler.toml` 去掉 `* * * * *`，只留 `35 7 * * *` | 没有每分钟要跑的事 |
| D1 表 | `market_prices` / `market_symbols` / `sync_jobs` / `worker_heartbeat` | `market_prices` 现有约 19.5 万行 |
| 脚本 | `scripts/pull_from_d1.py` / `scripts/seed_d1.py` | 无 D1 行情可拉/可灌 |
| workflows | `.github/workflows/d1-seed.yml` / `d1-backfill.yml` | 同上 |
| 测试 | `tests/test_worker_market_{sources,jobs,repo,api}.mjs`、`test_pull_from_d1.py`、`test_seed_d1.py`、`test_worker_market_parity.py` | 随实现删除 |
| 文档 | CLAUDE.md 的「Worker 抓取行情」整节 | 替换为一行说明 |

**保留不动**：`real_positions` / `real_cb_positions` 两张表、`/api/decide/*` 全部端点、`DISPATCH_WORKFLOW` 兜底 cron、`worker/auth.js`。

## 保留（这两天的净收益，不回退）

| 改进 | 位置 | 价值 |
|---|---|---|
| 市场日历缺口判定 | `sync_service._build_missing_segments` + `db_repository.get_market_trade_dates` | 长假不再对 300 只股票反复回补空区间，请求量降一个量级 |
| 数据完整性门禁 | `scripts/ci_check_data.py`（GH 最后一步） | 数据不完整 → workflow 变红，不再「绿着发旧数据」 |
| 三重触发兜底 | `deploy-site.yml` 三个 cron + Worker 07:35 dispatch | GH schedule 被丢时仍会跑 |
| 计划日期对齐真实行情日 | `deploy-site.yml`（`MAX(daily_prices.trade_date)`） | 绝不生成没有行情的计划 |
| 缓存 key 按 run_id 唯一 | `deploy-site.yml` | 修复「首次保存后缓存永不更新」 |
| `pull_from_d1` 带来的 pct_change 补算经验 | 已并入门禁/校验思路 | 腾讯降级时 `pct_change=0` 会在门禁里暴露 |

## 迁移顺序（每步可独立回滚）

| 步 | 动作 | 回滚 |
|---|---|---|
| 1 | `wrangler d1 export a-finder --remote` 备份到 artifact | 无需（只读） |
| 2 | `deploy-site.yml` 同步步骤改回「直连 `sync-incremental` + 3 轮重试 + 数据校验 + 门禁」 | `git revert` |
| 3 | 手动 dispatch 一次 deploy-site，确认：拿到当天数据、门禁绿、站点更新 | 无需 |
| 4 | 删除 Worker market 代码与每分钟 cron → 部署，确认 `/api/decide/*` 200、`/api/market/*` 404、dispatch 正常 | `git revert` + 部署 |
| 5 | `DROP TABLE` 四张表（`market_prices` / `market_symbols` / `sync_jobs` / `worker_heartbeat`） | 用第 1 步备份恢复 |
| 6 | 删除脚本/workflows/测试，更新 CLAUDE.md | `git revert` |

第 4 步之前 D1 行情还在（已无消费者，纯占配额）；第 5 步不可逆，但第 1 步有备份。

## 配额核算（迁移后，全部免费额度内）

| 资源 | 用量 | 免费额度 | 余量 |
|---|---|---|---|
| D1 存储 | 持仓几百行，< 1 MB | 500 MB | 500 倍 |
| D1 行写入 | 每天几次（人工录入持仓） | 100,000/天 | 万倍 |
| D1 行读取 | 每次打开决策台几十行 | 5,000,000/天 | 极大 |
| Worker 请求 | 每分钟 cron 已删 → 每天几十次 | 100,000/天 | 极大 |
| R2 / KV / Queues | **不使用** | — | — |
| GitHub Actions cache | 一个 hs300.db（约 100-200 MB） | 10 GB | 数十倍 |

## 验证（迁移完成的判据）

- [ ] `/api/decide/health` 返回 200 且 `d1: ok`
- [ ] `/api/market/health` 返回 404（路由已退役）
- [ ] D1 `SELECT name FROM sqlite_master WHERE type='table'` 只剩 `real_positions` / `real_cb_positions` / `_cf_KV` 等系统表
- [ ] 手动 dispatch deploy-site：日志出现「数据来源: direct」、门禁绿、站点日期为当天
- [ ] 第二天定时 run 自动跑通（无需人工干预）
- [ ] `wrangler tail` 不再有每分钟调用

## 风险

| 风险 | 缓解 |
|---|---|
| GH 缓存丢失 → 冷启动重抓 400 天（约 20 分钟） | `sync-incremental` 冷启动自动扩窗口；门禁失败会红 |
| 东财再次限流/封禁 | 腾讯降级链 + 3 轮重试（120s 间隔）+ 门禁告警 |
| 将来又想用 Worker 抓数据 | 代码在 git 历史里（`git revert` 即可恢复），届时需先解决存储（R2 或其它） |
| 误删决策台数据 | 第 5 步只删 4 张行情/任务表，不碰 `real_*` |

## 已定决策

1. **不启用 R2**：用户明确只用免费额度且不开通 R2 → JSON 存 R2 的方案作废。
2. **行情不发布到 gh-pages**：站点不消费原始行情，发布只会撑大仓库；行情只留在 CI 的 SQLite 缓存里（丢失可重抓）。
3. **Worker 保留 decide API 与 dispatch cron**：这两件事体量极小、且是 GH 定时不可靠的兜底，与配额无关。
4. **保留市场日历缺口与数据门禁**：它们解决的是「请求量」与「静默发旧数据」，与存储方案无关。
