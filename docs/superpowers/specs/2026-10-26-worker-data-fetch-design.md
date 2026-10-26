# Worker 抓取数据（替代理 GitHub Actions 直连数据源）— 设计文档

日期：2026-10-26
状态：待评审
相关：[deploy-site.yml](../../../.github/workflows/deploy-site.yml) · [worker/index.js](../../../worker/index.js) · [sync_service.py](../../../sync_service.py) · [data_providers.py](../../../data_providers.py)

## 目标

把**行情抓取**从 GitHub Actions 迁移到 Cloudflare Worker：

- Worker 每个交易日收盘后把 301 只沪深300成分股的日线抓进 D1
- GitHub Actions 不再访问东财/腾讯，改为从 D1 拉增量，其余（选股/计划/组合/转债/导出/部署）一行不改

**动机**：GH Actions 的定时本身不可靠（高负载延迟/丢弃，2026-10-26 实测未触发），再叠加东财限流，数据停更的失败面有两个。抓取移到 Worker 后，抓取与 GH 调度彻底解耦：即使当天 GH 那班被丢弃，数据仍在 D1 里，下一班（或手动 dispatch）直接消费。

## 非目标

- 不把选股/计划/组合等 Python 逻辑移植到 Worker（5 个策略 + 多因子 + 风控 + 计划构建，移植成本远大于收益）
- 不做盘中实时行情（本设计只保证"收盘后当天数据尽早落地"）
- 不改造静态站架构（gh-pages 仍是唯一对外站点，D1 只作为抓取落点）

## 约束（Cloudflare Workers Free 计划，用户已确认）

| 限制 | 值 | 对本设计的影响 |
|---|---|---|
| 子请求/次调用 | 50 | 301 只必须分片，每片 ≤ 40 |
| 同时打开连接 | 6 | 分片内并发池上限 6 |
| Cron CPU | 10 ms | 只能抓"当天"（响应 ~200B/只），不能抓一年 |
| Cron 墙钟 | 15 min | 富余 |
| Cron 数量 | 5/账户 | 已用 1（07:35 dispatch），本设计再加 1 |
| 请求/天 | 100,000 | 每天约 1,500 次，富余 |
| D1 单库 | 500 MB | 2 年历史约 20-30 MB |
| D1 查询/次调用 | 50 | 每次调用 3-4 条（batch 算 1 条） |

## 架构

```
                 ┌──────────────────────────────┐
  每 2 分钟 ───▶ │ Worker cron（Free = 1 个）    │
                 │  scheduled() 状态机           │
                 └──────────┬───────────────────┘
                            │ 每次抓 40 只（并发 6）
                            │ 写 D1（batch upsert）
                            ▼
                 ┌──────────────────────────────┐
                 │ D1: market_prices            │
                 │     market_symbols            │
                 │     sync_jobs（游标）         │
                 └──────────┬───────────────────┘
                            │ 鉴权 GET /api/market/day/:date
                            ▼
                 ┌──────────────────────────────┐
                 │ GitHub Actions               │
                 │  取增量 → SQLite → Python     │
                 │  → 导出 → 发 gh-pages         │
                 └──────────────────────────────┘
                            │ 任一步失败
                            ▼
                 回退：直连东财（现有路径）
```

职责边界：

| 组件 | 负责 | 不负责 |
|---|---|---|
| Worker `scheduled` | 抓当天日线、写 D1、维护游标 | 任何分析计算 |
| Worker `fetch`（新增路由） | 只读导出（鉴权）、健康检查 | 写操作（除手动触发） |
| D1 | 行情 + 成分股 + 抓取进度 | 选股结果、计划、持仓 |
| GitHub Actions | Python 计算、静态站发布、兜底抓取 | 外部数据源直连（除非回退） |

## D1 表结构

```sql
-- 行情：字段与 SQLite daily_prices 对齐，便于两边直传
CREATE TABLE IF NOT EXISTS market_prices (
  code       TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  open REAL, close REAL, high REAL, low REAL,
  volume REAL, amount REAL, turnover REAL, pct_change REAL,
  PRIMARY KEY (code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_mp_date ON market_prices(trade_date);

-- 成分股：避免把 301 个代码硬编码进 Worker
CREATE TABLE IF NOT EXISTS market_symbols (
  code TEXT PRIMARY KEY,
  name TEXT,
  updated_at TEXT
);

-- 抓取任务：状态机全部状态就是这两个数字
CREATE TABLE IF NOT EXISTS sync_jobs (
  id         TEXT PRIMARY KEY,        -- 用 target_date，天然幂等
  status     TEXT NOT NULL,           -- pending | running | done | failed
  cursor     INTEGER NOT NULL DEFAULT 0,
  total      INTEGER NOT NULL,
  attempts   INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL,
  note       TEXT
);
```

建表脚本并入现有 `worker/schema.sql`（`deploy-worker.yml` 已幂等执行它）。

**字段依据**：`market_data.build_market_from_db` 读取 `close, volume, turnover, amount, pct_change`，
`strategies/volume_price.py` 用 `pct_change`，`strategies/backtest.py` 用 `turnover/amount`，
所以这五个字段必须齐全（`turnover` 漏掉会让 `Stock.turnover` 全是 0，成交额类策略退化）。
`amplitude` 与 `change` 分析链路未使用，不入表；`pull_from_d1.py` 写 SQLite 时这两列置 NULL。
东财/腾讯的响应里都能拿到 turnover（东财 f61=换手率，腾讯 qfq 返回换手率字段），无需额外请求。

## 抓取状态机

### 触发

`wrangler.toml` 增加 cron：

```toml
[triggers]
crons = ["35 7 * * *", "*/2 * * * *"]   # 07:35 兜底 dispatch（现状）+ 每 2 分钟驱动抓取
```

### scheduled() 流程

```
1. 北京时区判定（Intl.DateTimeFormat, Asia/Shanghai）
   ├─ 非工作日 → return
   └─ 时间不在 15:35–23:00 → return        # 收盘前/深夜不做无意义空转
                                          # 15:35 起才抓：东财当日 K 线收盘后才稳定
2. 查 sync_jobs：status IN ('pending','running') 的最新一条
   └─ 没有 → return                        # 绝大多数分钟走这条，成本 = 1 条 D1 查询
3. 读 market_symbols 第 [cursor, cursor+40) 段代码
4. 并发池 6（Free 同时连接上限）抓 beg=end=target_date
   ├─ 东财 push2his（主）+ UA/Referer 头
   ├─ 腾讯 fqkline（备，需 Referer）
   └─ 该只失败 → 记入 note，不阻塞本片其他代码
5. db.batch() 一次 upsert 本片全部行（batch 只计 1 次 D1 查询）
6. cursor += 40
   ├─ cursor >= total → status='done'
   └─ 否则保持 running，等 2 分钟后的下一轮
```

### 失败与重试

- 单只失败：本片其余照常入库，失败的代码计入 `sync_jobs.note`（JSON 数组）
- 整片失败（数据源全挂）：`attempts += 1`；`attempts > 3` → `status='failed'`
- `status='failed'` 或超过 23:00 仍未 `done`：GH 侧检测到数据不全 → 走直连兜底（见下）

### 任务创建（幂等）

`INSERT OR IGNORE INTO sync_jobs (id, status, cursor, total, ...) VALUES (<target_date>, 'pending', 0, <count>)`。
创建入口两处：Worker 自己的 07:35 cron（作为第二动作）、GH 手动 dispatch 时调 `POST /api/sync/start`（鉴权）。

## 对外 API（新增，全部要求 `X-Sync-Token`）

| 端点 | 方法 | 返回 | 用途 |
|---|---|---|---|
| `/api/market/health` | GET | `{latest_date, codes_on_latest, jobs:[...]}` | GH 判断是否需要补数 |
| `/api/market/dates` | GET | `{dates:[...]}`（`?since=` 可限） | GH 决定补哪几天 |
| `/api/market/day/:date` | GET | `{date, rows:[{code,trade_date,open,close,high,low,volume,amount,turnover,pct_change}]}` | GH 取一天（301 行 ≈ 60 KB） |
| `/api/sync/start` | POST | `{ok, job}` | 手动触发当天抓取 |

鉴权：`X-Sync-Token` 与 Worker secret `SYNC_TOKEN` 比对，不匹配返回 401。GH 侧把它存为仓库 secret `D1_SYNC_TOKEN`。

## GitHub Actions 改造

替换现有「增量同步行情」步骤中**抓取**部分（`plan/pick/export/deploy` 全部不动）：

```yaml
- name: 从 D1 取增量（Worker 抓取）
  id: pull
  run: |
    python3 scripts/pull_from_d1.py --db hs300.db \
      --worker https://ycu.space --token "$D1_SYNC_TOKEN" --max-days 60
  # --max-days：单次最多补多少天（日常 60 足够；缓存丢失的冷启动传 400）
  # 内部逻辑：
  #   health.latest_date 对比本地 MAX(trade_date)
  #   → 逐日 GET /api/market/day/:date → INSERT OR REPLACE INTO daily_prices
  #   → 退出码 0=有新数据  3=Worker 无新数据/不可用（触发回退）
- name: 回退（Worker 无数据时直连）
  if: steps.pull.outputs.fell_back == 'true'
  run: bash a-finder.sh sync-incremental hs300.db 20 pick-history
```

`scripts/pull_from_d1.py` 的契约：

- 输入：DB 路径、Worker base URL、token、最多回补天数
- 输出：写入行数；`GITHUB_OUTPUT` 写 `fell_back=true|false`、`latest_date`
- 幂等：重复执行不产生重复行（`INSERT OR REPLACE`，主键 `(code, trade_date)`）

## 冷启动与历史回填

| 场景 | 做法 |
|---|---|
| 首次把历史灌进 D1 | 本地 `hs300.db` → `scripts/seed_d1.py` 生成 SQL 文件 → `npx wrangler@latest d1 execute a-finder --file=seed_prices.sql --remote`。走 CLI 不过 Worker，无需分片（148k 行） |
| 成分股初始化 | 同上，从 `hs300_metadata` 生成 `market_symbols` 的 INSERT |
| GH 缓存丢失（需要全历史） | `GET /api/market/dates` + 逐日拉 277 天（GH 无子请求限制，约 3 分钟）；或 `wrangler d1 export` 拿 CSV 直接灌 sqlite3 |

## 数据源与反爬（已实测，2026-10-26）

探针通过 CI 部署到现有 Worker（临时路由，验证后已移除），实测四个源：

| 源 | 端点 | 结果 |
|---|---|---|
| 东财（主域） | `push2his.eastmoney.com/api/qt/stock/kline/get` | **HTTP 520** —— Cloudflare 出站被拒 |
| 东财（备域） | `push2.eastmoney.com/...` | **HTTP 502** |
| 腾讯 前复权 `fqkline` | `web.ifzq.gtimg.cn/appstock/app/fqkline/get` | 200，仅 6 字段（无成交额/换手率） |
| 腾讯 **不复权 `newfqkline`** | `web.ifzq.gtimg.cn/appstock/app/newfqkline/get?...bfq` | **200，11 字段** ✅ |
| 新浪日线 | `money.finance.sina.com.cn/.../getKLineData` | 200，仅 OHLCV（成交量单位为**股**，腾讯是**手**） |

**结论：东财从 Worker 完全不可达，主源改用腾讯 `newfqkline` + `bfq`（不复权）。**
不复权是硬要求——Python 侧 `data_prices` 存的是 `fqt=0`（不复权），复权价会让历史序列断裂。

腾讯 `newfqkline` 行格式（11 字段）：

```
["2026-10-12","1262.95","1288.00","1288.00","1261.00","31556.00",{},"0.25","404285.76","0.00","0.00"]
   日期         开        收        高        低        量(手)   {}   换手率%   成交额(万)
```

映射：`open=1, close=2, high=3, low=4, volume=5, turnover=7, amount=8×10000`。
`pct_change` 腾讯不提供 → Worker 用窗口内前一交易日收盘价计算；窗口首行无法计算则留空，
由 GH 侧 `pull_from_d1.py` 用本地库前收盘补算（已有实现）。

降级链：`newfqkline(bfq)` → `fqkline(bfq)`（无成交额/换手率）→ 新浪日线（成交量需 ÷100）。
东财不再在链上（不可达），代码中保留但默认禁用，便于将来复测。

## 测试策略

| 层 | 内容 |
|---|---|
| 纯函数单测（node，无网络） | 游标推进 / 完成判定 / 失败计数 / 北京时区与交易时段判定 / 东财与腾讯响应解析 |
| Parity（沿用 `test_decide_compare_parity.py` 模式） | 同一份 fixture，Worker 解析结果 vs Python `data_providers` 解析结果逐字段相等 |
| Worker 集成 | `wrangler dev --remote` 抓 40 只，核对 D1 与 `hs300.db` 同日数据一致（行数 + 抽样价格） |
| GH 侧 | `scripts/pull_from_d1.py` 单测（mock fetch）：正常增量 / Worker 不可用 → fell_back / 重复执行幂等 |
| 门禁 | `scripts/ci_check_data.py` 增加「数据来源：d1|direct」字段，原有判定不变 |

## 限额核算（Free 计划）

| 项 | 每天用量 | 额度 |
|---|---|---|
| Worker 请求 | ~720 次 cron 空转 + 8 次实抓 + 少量 API | 100,000 |
| 子请求/次 | 40（分片） | 50/次 |
| D1 查询/次 | 3-4（insert batch 算 1） | 50/次 |
| D1 写入 | ~301 行/天 | 无独立限制（受批次约束） |
| D1 存储 | 2 年 ≈ 20-30 MB | 500 MB |
| Cron 数量 | 2 | 5 |

## 实施顺序（含回滚点）

1. **探针**：验证 Worker 能否抓到东财/腾讯数据 → 不通则终止
2. 建表 + `market_symbols` 灌入 + 历史行情 seed（只写不读，对现状零影响）
3. Worker 状态机 + API（部署后默认 `SYNC_ENABLED=false`，不自动抓）
4. 手动翻开关跑一次，核对 D1 与本地库一致
5. GH 侧 `pull_from_d1.py` + workflow 改造，先保留回退分支
6. 观察 3 个交易日：D1 落地时间、GH 日志数据来源、门禁是否绿
7. 稳定后把回退分支降级为"仅告警"（不自动直连）

**回滚**：GH workflow 的改动是纯新增步骤 + 一个 `fell_back` 分支，删掉即回到现状；Worker 侧新增表与路由不影响现有 `/api/decide/*`；D1 数据保留不影响任何现有功能。

## 已定决策（原本的三个待确认项）

1. **成分股刷新由 GH 负责**：GH 每天在取增量前先把 `hs300_metadata` 全量 upsert 到 D1
   （`POST /api/market/symbols`，301 行 ≈ 20 KB）。Worker 不自己维护成分股名单——
   它没有获取沪深300成分股的能力，硬编码又会在调整成分时抓错票。
2. **抓取从北京 15:35 开始**：东财当日 K 线收盘后才稳定，15:35 之前的响应可能缺量或
   是盘中价。状态机的时间窗就是 15:35–23:00；23:00 仍未完成则当天判失败，交给 GH 兜底。
   `sync_jobs.id = 北京今天`，同日不会重复建任务。
3. **保留 07:35 的 dispatch cron**：抓取迁走后它的语义从"触发抓取"变成"确保 GH 来消费
   D1"。它仍由 `GITHUB_DISPATCH_TOKEN` 驱动（现状），不改代码。
