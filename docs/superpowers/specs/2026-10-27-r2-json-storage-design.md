# 行情存储改造：R2 + JSON，D1 只存元数据 — 设计文档

日期：2026-10-27
状态：待评审
相关：[wrangler.toml](../../../wrangler.toml) · [worker/schema.sql](../../../worker/schema.sql) · [worker/market/](../../../worker/market/) · [scripts/pull_from_d1.py](../../../scripts/pull_from_d1.py) · [deploy-site.yml](../../../.github/workflows/deploy-site.yml)

## 目标

把行情数据从 D1 搬到 R2（一天一个 JSON），D1 只保留元数据，使整条链路稳定运行在 **Cloudflare 免费额度**内（不额外付费）。

**触发原因**：D1 免费版「行写入」配额是 **10 万行/天**，而一次历史回填就是 19.5 万行（650 天 × 301 只）—— 配额被打爆后表现为「数据写进去了但游标不推进」，且错误极不直观。存储本身不是瓶颈（19.5 万行仅约 8.5 MB，远低于 500 MB）。

## 非目标

- 不改选股/计划/组合等 Python 计算逻辑
- 不改静态站（gh-pages）架构
- 不引入付费额度（R2/KV/Queues 付费特性都不用）
- 不做 Worker 侧的历史回填（历史回填移到 CI，见 §6）

## 约束

| 约束 | 值 | 对设计的影响 |
|---|---|---|
| D1 行写入/天 | 100,000（免费） | 行情表必须搬走；D1 每天只允许 ~10 行写入 |
| D1 存储 | 500 MB/库 | 不是瓶颈，但搬走后可 DROP |
| Worker 子请求/次 | 50（免费） | 一天 301 只必须分片，每片 ≤40 |
| Worker CPU（cron） | 10 ms（免费） | 每轮只做「净请求 + 一次 JSON 写盘」，不做大计算 |
| R2 免费额度 | 10 GB / 1,000,000 写/月 / 10,000,000 读/月 | 每天 9 次写，绰绰有余 |
| 指标最低历史 | `pick_history` 要求 `min_days=221`；`candidate_rules` 的 MA200 还需前 20 天 | **D1/ R2 至少保留 260 个交易日**，否则冷启动时榜单会空 |

## 现状

```
D1: market_prices (650 个交易日 / ~19.5 万行)   ← 行写入配额被打爆
    market_symbols / sync_jobs / real_positions / worker_heartbeat
Worker: 每 2 分钟 cron 分片抓取 → 逐行 upsert 到 D1（一天 301 行，回填一次上万行）
GH:     /api/market/day/:date → 写入 SQLite（pandas 之外纯 stdlib）→ 跑 Python
```

## 架构

```
                      ┌──────────────────────────────────────────┐
  15:35 cron ────────▶│ sync_jobs 取任务 → 抓 40 只 → R2 put      │
                      │ parts/<date>/NN.json（分片暂存）           │
                      │ 游标满 → 读 8 片 → 合并 → prices/<date>.json│
                      └───────────────┬──────────────────────────┘
                                      │
                    ┌─────────────────┴──────────────────┐
                    ▼                                    ▼
        R2 a-finder-data                       D1 a-finder（元数据）
        prices/2026-10-27.json                 market_symbols / data_index
        parts/…（合并后清理）                    sync_jobs / real_positions
                    │
                    │ GET /api/market/day/:date（Worker 原样返回 R2 对象）
                    ▼
        GitHub Actions → pull_from_d1.py → SQLite → Python 分析 → gh-pages
```

### R2 目录结构

```
prices/2026-10-27.json      # 一天一个文件，全部 301 只，约 59 KB
parts/2026-10-27/00.json    # 分片暂存（每片 ≤40 只，约 8 KB）
parts/2026-10-27/01.json
...
```

JSON 结构（与现接口返回完全一致，Python 侧解析代码不变）：

```json
{"date":"2026-10-27","rows":[
  {"code":"600519","open":1325.4,"close":1349.98,"high":1350.0,"low":1324.79,
   "volume":41599,"amount":5574287600,"turnover":0.33,"pct_change":1.122}
]}
```

### D1 元数据表

```sql
-- 新增：日期索引，GH 用它判断缺哪几天，health 用它汇总
CREATE TABLE IF NOT EXISTS data_index (
    trade_date TEXT PRIMARY KEY,   -- 2026-10-27
    codes      INTEGER NOT NULL,   -- 301
    bytes      INTEGER,            -- 该 JSON 体积，便于观察
    missing    TEXT,               -- JSON 数组，如 ["601059"]（停牌股，可诊断）
    updated_at TEXT NOT NULL
);

-- 保留：market_symbols / sync_jobs / real_positions / real_cb_positions / worker_heartbeat
-- 迁移完成后 DROP：market_prices
```

## 日抓流程（关键设计）

**为什么不能一次写完**：一天一个 JSON 需要「当天全部 301 只」同时在手，但免费版单次只能发 50 个子请求，而抓 301 只至少要 301 次 fetch。

**方案：分片暂存 + 合并**

```
tick 1..8   每次抓 40 只 → R2 put parts/<date>/NN.json       （1 写/轮，CPU 忽略不计）
tick 9      cursor 满 → R2 get parts/<date>/00..07.json       （8 子请求）
            → 内存合并（8 × 8 KB 拼接 + stringify ≈ 1-2 ms）
            → R2 put prices/<date>.json                       （1 写）
            → D1 INSERT data_index（1 行写入）
            → R2 delete 8 个 part                             （8 子请求）
```

合计每轮子请求 ≤17、D1 写入 1 行，均远低于配额。合并不写成「边抓边追加」，因为 R2 对象不可变，读改写会让每轮多出 8 次读 + 8 次写。

**失败处理**：

| 情况 | 处理 |
|---|---|
| 单只抓取失败 | 该只缺行，记录到 `data_index.missing`；`codes` 字段反映实际行数 |
| 某片写 R2 失败 | 该轮 `attempts+1`，游标不推进，下一轮重试同一片 |
| 合并时缺片 | 合并前校验片数 == ceil(total/40)，不符则不合并、游标不推进 |
| 整轮超时/异常 | runner 外层 try/catch 记日志（observability 已开启），游标不动，下轮重试 |
| 23:00 仍未完成 | 保留 running 状态；次日 15:35 日报优先级更高，先抓当天 |

## GitHub 读取合约（对 Python 侧几乎透明）

| 端点 | 数据来源 | 说明 |
|---|---|---|
| `GET /api/market/day/:date` | R2 `prices/<date>.json` | 原样返回；**miss 时回退 D1 `market_prices`**（删除表之前） |
| `GET /api/market/health` | D1 `data_index` | `latest_date / codes_on_latest / days / missing_codes` |
| `GET /api/market/dates` | D1 `data_index` | 日期列表（GH 用它算缺口） |
| `POST /api/market/symbols` | D1 `market_symbols` | 不变 |
| `POST /api/sync/start` | D1 `sync_jobs` | 只保留日报类型（历史回填移出 Worker） |
| `GET /api/market/job` | D1 `sync_jobs` | 不变 |
| `POST /api/market/tick` | — | 不变（诊断用，dry 模式保留） |

`pull_from_d1.py` 的改动仅在「取数」这一层；写入 SQLite、`pct_change` 补算、退出码契约（0/2/3）、回退直连逻辑全部不动。

## 历史数据：迁移与回填都走 CI

Worker 不做历史（CPU 10 ms + D1 行配额双重限制，昨晚已证实走不通）。改为 GitHub Actions：

**一次性迁移（保留最近 260 个交易日）**

1. `wrangler d1 export a-finder --remote` 导出 SQL
2. Python 解析出 `market_prices` 行 → 按日期分组 → 生成 260 个 JSON 文件
3. `wrangler r2 object put a-finder-data/prices/<date>.json --file=…`（260 次写）
4. `wrangler d1 execute` 批量写 `data_index`（260 行，一次 batch）
5. 核对：D1 `data_index` 行数 == 260，抽样 JSON 与 `hs300.db` 同日数据一致

**将来需要更深历史**：同一条 CI workflow 增加「Python 直连数据源抓取」步骤即可，产出 JSON 后同样上传，全程不碰 Worker、不消耗 D1 行配额。

## 配额核算（全部免费额度内）

| 资源 | 用量 | 免费额度 | 余量 |
|---|---|---|---|
| D1 行写入 | ~10 行/天 | 100,000/天 | 1 万倍 |
| D1 行读取 | 数百/天 | 5,000,000/天 | 极大 |
| D1 存储 | < 1 MB（元数据） | 500 MB | 500 倍 |
| R2 写入 | 9/天（迁移一次性 260） | 1,000,000/月 | 极大 |
| R2 读取 | ~10/天 | 10,000,000/月 | 极大 |
| R2 存储 | ~15 MB（260 天） | 10 GB | 600 倍 |
| Worker 子请求 | ≤17/轮 | 50/轮 | 2.9 倍 |
| Worker CPU | 每轮 ≤2 ms（合并轮） | 10 ms | 5 倍 |

## 迁移与回滚（灰度）

| 步骤 | 内容 | 可回滚性 |
|---|---|---|
| 1 | 加 R2 binding + `data_index` 表（纯新增，零影响） | 直接 revert |
| 2 | CI 迁移 260 天 → R2 + `data_index` | 删对象即可 |
| 3 | Worker 读路径改「先 R2，miss 回退 D1」 | revert 即回 D1 |
| 4 | Worker 写路径改「parts → 合并 → R2」 | revert |
| 5 | 观察 1 个交易日（日抓完成、GH 拉到当天、`data_index` 正确） | — |
| 6 | `DROP TABLE market_prices`（不可逆，放最后） | 无（数据已在 R2） |

回滚点：步骤 6 之前，Worker 的读路径始终保留 D1 回退分支，任何时点 revert 都能回到纯 D1 状态。

## 测试策略

| 层 | 内容 |
|---|---|
| Node 单测（无网络） | parts→merge 纯函数（乱序/缺片/重复 code/空片）、R2 key 命名、片数校验、`data_index` 写入形态（假 DB） |
| Python 单测 | 迁移脚本：SQL 解析 → 按日分组 → JSON 序列化 → 只保留最近 260 天；`pull_from_d1.py` 消费 JSON 形状（现有 8 项测试保持通过） |
| Parity | 生成的 `prices/<date>.json` 与本地 `hs300.db` 同日数据逐字段一致（复用现有 `test_worker_market_parity.py` 思路） |
| 集成 | 真实跑一个交易日：核对 R2 对象存在、`data_index` 一行、GH `pull_from_d1.py` 写入行数 == 当日应得行数 |

## 已定决策

1. **JSON 存 R2 而不是 KV/gh-pages**：KV 免费只有 1000 次写/天（回填 260 天就占掉 26%），gh-pages 需要 Worker 把数据回传 GH 再提交，链路更绕。
2. **保留最近 260 个交易日**：`min_days=221` + MA200 的 220 天 + 缓冲；200 天会在冷启动时导致榜单为空。
3. **历史迁移与回填都走 CI**：Worker 受 CPU 10 ms 与 D1 行配额双重限制，不适合批量历史。
4. **分片暂存 + 合并**而不是「一次抓完」：免费版 50 子请求上限决定必须分 8 片。
5. **保留 `market_prices` 直到迁移验证完成**：读路径带 D1 回退，避免切换期空窗。

## 风险

| 风险 | 缓解 |
|---|---|
| 合并轮失败留下残片，第二天数据不完整 | 片数校验 + 游标不推进 + 次日日报优先重抓；`data_index` 记录 missing 可诊断 |
| R2 需要后台开通（绑卡，10 GB 内不收费） | 实施第一步确认 R2 已启用；未启用则该方案不可行，退回纯 D1 + 提高重试 |
| GH 拉取 260 天冷启动需要 260 次请求 | 现有 `--max-days 400` 上限内，GitHub 无子请求限制，约 3-5 分钟 |
| `DROP TABLE` 后想回退 | 删除前先用 `wrangler d1 export` 备份一份 SQL 到 CI artifact |
