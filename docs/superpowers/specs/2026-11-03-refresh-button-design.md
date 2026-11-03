# 登录后「刷新数据」按钮 — 设计文档

日期：2026-11-03
状态：待评审
相关：[worker/index.js](../../../worker/index.js) · [worker/schema.sql](../../../worker/schema.sql) · [worker/auth.js](../../../worker/auth.js) · [a-finder.sh](../../../a-finder.sh) · [export_json.py](../../../export_json.py) · [static/js/decide.js](../../../static/js/decide.js) · [static/js/api.js](../../../static/js/api.js) · [templates/decide.html](../../../templates/decide.html) · [.github/workflows/deploy-site.yml](../../../.github/workflows/deploy-site.yml) · [scripts/verify_site_export.py](../../../scripts/verify_site_export.py) · [scripts/needs_fallback.py](../../../scripts/needs_fallback.py)

## 目标

在决策台（`/decide`）加一个**登录后才可见**的「刷新数据」按钮。点击后触发一次完整的数据流水线（增量同步行情 → 选股 → 计划 → 组合 → 导出 JSON → 发布），并在页面上反馈进度与结果。

**全程不依赖 GitHub Actions。** 算力与发布都在本机完成，GitHub Actions 退化为「只维护缓存库 + 只在手工 dispatch 时可发布」的热备。

## 非目标

- 不把计算逻辑移植到 Worker（Worker 免费版 CPU 10ms，且 Python→JS 必然漂移）
- 不开 R2、不给 D1 存行情（行写入 10 万/天配额，见 `CLAUDE.md`「行情数据路径」）
- 不改变任何选股/计划/组合/转债的计算逻辑
- 不改变站点前端取数路径（仍读 `data/*.json`）
- 不做盘中实时刷新（只做收盘后的全量刷新）

## 现状（实测确认）

```
ycu.space  →  Cloudflare Worker「a-finder-api」
               ├── /api/*        → worker/index.js 脚本路由（/api/nonexistent 返回 {"error":"not found"}）
               └── 其他路径      → Workers Static Assets（/404.html → 308 → /404，非 gh-pages 行为）
                                资产来源 = gh-pages 分支
```

| 事实 | 证据 |
|---|---|
| 站点在 Cloudflare 上，不是 GitHub Pages | `server: cloudflare`、etag 为 CF 重算的 MD5（非 gh-pages 的 inode 风格） |
| gh-pages 一变，Cloudflare 资产自动同步 | 线上 `/404` 的 `?v=e46d355` == `export_json._static_version()` 读的本地 HEAD == `gh-pages:404.html` 的版本 |
| 静态资源缓存 4 小时 | `cache-control: max-age=14400`（与 `CLAUDE.md` 记载一致） |
| Worker 已有 dispatch 能力 | `dispatchDeploySite()` + 已认证的 `POST /api/decide/dispatch-test` |
| 本机已有完整发布流水线 | `a-finder.sh site --push`：同步→选股→计划→10 档位→回补→`export_json.py`→orphan push 到 gh-pages |
| 本机已有常驻服务惯例 | `a-finder.sh web {start\|stop\|status}`（pid 文件 + nohup + 日志） |

### 要解决的三个真问题

1. **Worker 在边缘，够不到本机 `127.0.0.1`。** 需设计触发方向。
2. **`workflow_dispatch` 只发信号不等结果**，且 CI 8 班/天 × 本机抢跑 = 双写分叉（`CLAUDE.md` 2026-10-29 事故的核心）。
3. **产物可能比线上旧**（孤儿库 / 同步失败）→ 必须有单调性守卫，否则静默回退站点（2026-10-29 同款事故）。

## 架构

```
┌────────────────────────────────────────────────────────────┐
│  Cloudflare Worker「a-finder-api」                          │
│                                                            │
│   POST /api/refresh          需登录 cookie                │
│   GET  /api/refresh/status   需登录 cookie                │
│   GET  /api/refresh/next     本机 agent 拉任务（共享 secret）│
│                    │                                       │
│                    ▼                                       │
│   D1: refresh_jobs 表（新建）                               │
└────────────────────┬───────────────────────────────────────┘
                     │ ① 本机 agent 每 3s 拉一次 /api/refresh/next
                     ▼
┌────────────────────────────────────────────────────────────┐
│  本机 Mac（常开）                                           │
│                                                            │
│   refresh_agent.py  127.0.0.1:8765                         │
│     ├─ GET  /api/refresh/next → 领任务                     │
│     ├─ 起后台进程 → bash a-finder.sh site --push            │
│     └─ POST /api/refresh/result → 回报状态到 D1             │
│                                                            │
│   bash a-finder.sh site --push                             │
│     同步 → 选股 → 计划 → 10 档位 → 回补 → export_json      │
│     → 单调性校验 → push gh-pages                           │
│                    │                                       │
│                    ▼                                       │
│   Cloudflare 自动同步 Static Assets                         │
│                    ▼                                       │
│   ycu.space/data/*.json 更新 ✅                             │
└────────────────────────────────────────────────────────────┘
```

**触发方向：拉模式（agent 轮询 Worker）。**

Worker 无法主动访问本机 `127.0.0.1`，反过来本机可以出网（它已经在 push gh-pages）。因此由本机 agent 轮询 Worker 的任务队列。

放弃的替代方案：**隧道模式**（Cloudflare Tunnel / ngrok 暴露本机）——能让 Worker 直推、零延迟，但多一个常驻隧道进程和多一个故障点。轮询间隔 3 秒，感知延迟可忽略，不值得为此增加组件。

## 组件

### C1. Worker 刷新接口（新增，`worker/index.js`）

**职责**：认证、下发任务、查询状态。**不含任何计算逻辑。**

| 端点 | 认证 | 说明 |
|---|---|---|
| `POST /api/refresh` | `decide_auth` cookie | 建 `refresh_jobs` 行（status=`running`），返回 `{job_id}`。已有 running 任务 → 409 |
| `GET /api/refresh/status?job_id=` | `decide_auth` cookie | 返回该 job 的状态 |
| `GET /api/refresh/next` | `REFRESH_AGENT_SECRET` 头 | 返回一个待领任务；无任务 → `{job_id: null}` |
| `POST /api/refresh/result` | `REFRESH_AGENT_SECRET` 头 | 本机回报 `{job_id, status, latest_date, message}` |

约定：

- `job_id` 用 `crypto.randomUUID()`
- `/api/refresh/next` 与 `/api/refresh/result` 用**独立的共享 secret**（不复用 `DECIDE_SECRET`——那个是登录态签名密钥，混用会让职责纠缠）。新增 secret：`REFRESH_AGENT_SECRET`
- `/api/refresh/next` 领任务时把 D1 行状态从 `pending` 改成 `running`（用 `UPDATE ... WHERE status='pending'` 保证只有一个领到者）
- 前端可见的端点（`/api/refresh`、`/api/refresh/status`）**必须**要求登录 cookie

### C2. 本机 refresh-agent（新增，`refresh_agent.py`）

**职责**：领任务、跑流水线、回报状态。**不含任何业务计算。**

- HTTP 服务绑定 `127.0.0.1:8765`（**只绑回环**，不对外暴露）
- 主循环：每 3 秒 `GET {WORKER_BASE}/api/refresh/next`（带 secret 头）
- 领到任务 → `subprocess.Popen` 起后台进程跑 `bash a-finder.sh site --push`，把 stdout/stderr 写入 `logs/refresh.log`
- 进程结束后读 `site/data/dates.json` 的最新日 → `POST /api/refresh/result` 回报 `success`/`failed`
- **单任务互斥**：本地同时只允许一个刷新任务在跑
- 用 `caffeinate -i` 包裹子进程，**防止 Mac 睡眠导致任务中断**（Mac 常开，但合盖会睡）

### C3. D1 状态表（新增，`worker/schema.sql`）

```sql
CREATE TABLE IF NOT EXISTS refresh_jobs (
    job_id       TEXT PRIMARY KEY,
    status       TEXT NOT NULL,          -- pending | running | success | failed
    requested_by TEXT,                   -- 登录用户名
    created_at   TEXT,
    started_at   TEXT,
    finished_at  TEXT,
    latest_date  TEXT,                   -- 刷新后站点的最新数据日期
    message      TEXT,                   -- 摘要 / 错误原因
);
```

**配额**：每天个位数到几十行 → 相对 10 万/天配额可忽略。表只存状态，**不存行情**（`CLAUDE.md` 明令禁止）。

### C4. 前端按钮（新增）

位置：`/decide` 页，登录状态栏（`#decide-auth`）旁。**该页已有登录态**（`AUTHED` / `renderAuthBar()`）。

| 状态 | 表现 |
|---|---|
| 未登录 | 按钮**不显示**（与现有 `write-control` 隐藏机制一致） |
| 已登录 | 显示「刷新数据」按钮 |
| 点击后 | 按钮禁用，文字「刷新中…」，每 4 秒轮询 `/api/refresh/status` |
| 成功 | 「✅ 已更新，最新数据 2026-11-03」，按钮恢复 |
| 失败 | 「❌ 失败：<原因>」，按钮恢复 |
| 进行中 | 展示阶段提示（见下） |

**阶段提示**：本机 agent 回报 `message` 字段，前端直接展示（如「同步行情中」「计算选股与计划中」「导出并发布中」）。

改动：
- `static/js/api.js`：加 `fetchRefresh()` / `fetchRefreshStatus(jobId)`
- `static/js/decide.js`：加按钮逻辑 + 轮询块
- `templates/decide.html`：加按钮元素

前端约定：
- 用**原生** `fetch` + `setTimeout` 递归轮询（不用 `setInterval`，避免请求堆积）
- 轮询在页面隐藏时暂停（`document.visibilitychange`），回来再继续
- 组件卸载/登出时清理定时器

### C5. `a-finder.sh` 改动

| 新增 | 说明 |
|---|---|
| `refresh-agent {start\|stop\|status}` | 仿 `cmd_web` 的模式：pid 文件 + `nohup` + 日志 |
| `cmd_site` 发布前校验 | 见「防回退」一节 |
| `cmd_site` 计划日期对齐 | 改用 `MAX(daily_prices.trade_date)`（见下节） |

## 数据流与时序

```
用户点「刷新数据」
  │
  ├─ POST /api/refresh  [cookie]
  │    Worker：认证 → D1 建 job(status=pending) → 返回 job_id
  │
  ├─ 前端开始轮询 GET /api/refresh/status?job_id=X（每 4s）
  │
  │   [本机 agent 每 3s 拉 GET /api/refresh/next]
  │    Worker：返回 job，置 status=running
  │    本机：Popen 跑 a-finder.sh site --push → logs/refresh.log
  │      ├─ 增量同步行情（10~30 min，可能被数据源限流）
  │      ├─ 选股 → 计划 → 10 档位 → 回补历史
  │      ├─ export_json.py → site/data/*.json
  │      ├─ 单调性校验（见下）→ 通过才 push gh-pages
  │      └─ push gh-pages → Cloudflare 同步 Assets → 站点更新 ✅
  │
  └─ 本机 POST /api/refresh/result → D1(status=success, latest_date=...)
       前端轮询拿到 success → 显示结果
```

**总耗时 15~35 分钟**，主因是行情同步与历史回补。

## 防回退（单调性守卫）—— 不可省

2026-10-29 的直接教训：孤儿库导出的 `dates.json` 也能通过「有没有数据」的校验，于是把比线上旧 16 天的站点推上去，页面正常、数据停在半个月前，**比空站点更隐蔽**。

**发布前必须校验产物最新日 ≥ 线上最新日。** 复用现成工具，不重写：

```bash
LIVE=$(curl -sf --max-time 20 https://ycu.space/data/dates.json \
  | python3 -c "import json,sys;d=json.load(sys.stdin).get('dates') or [''];print(d[0])")
python3 scripts/verify_site_export.py --site site --not-older-than "$LIVE" || exit 1
```

校验失败 → **不 push**，任务标记 `failed`，消息写明「产物比线上旧，已阻止发布」。

（`cmd_site` 当前无条件 push，这是要补的洞。）

## GitHub Actions 的角色调整

| Workflow | 变化 | 理由 |
|---|---|---|
| `deploy-site.yml` | **删除 `peaceiris/actions-gh-pages` 步骤**；保留交易日判定、同步、选股、计算、缓存保存 | 本机成为 gh-pages 的**唯一日常写者** → 双写分叉从根上消失。CI 仍推进自己的缓存谱系（那是 `CLAUDE.md` 里最贵的一块资产） |
| `daily-audit.yml` | **不变**（仍会推 gh-pages） | 极低频（每晚一次），且只在「发现并修复了缺口」时发布，与本机的每日刷新不冲突 |
| `gap-heal.yml` | 不变 | 同上（每周一次） |
| `deploy-worker.yml` | 不变 | Worker 自身部署 |

**CI 的 `skip_sync` / `site_stale` 语义保持不变**：

- `skip_sync` ← CI 自己的库有没有当天行情（决定要不要同步）
- `site_stale` ← 线上有没有当天（决定要不要跑下游计算）

CI 仍会同步+计算（维护缓存谱系），只是**不再发布**。本机发布后，CI 的 `skip_sync=true` + `site_stale=false` → 秒退，省 CI 分钟。两条谱系各自独立推进，互不干扰。

**⚠️ 改 workflow 的 `run:` 块后必须验 YAML**（`CLAUDE.md` 记载：块标量缩进 0 会让整个文件变非法 YAML）。

## 错误处理

| 失败点 | 表现 | 处理 |
|---|---|---|
| 未登录 | 按钮不显示 | 前端按 `AUTHED` 隐藏；Worker 仍独立校验 cookie（401） |
| 重复点击 | 409 | Worker 查有无 `pending`/`running` 的 job |
| 本机 agent 未启动 | job 长时间停在 `pending` | Worker 记 `created_at`；超过 10 分钟仍 `pending` → 标记 `failed`（消息：本机服务未响应，提示 `a-finder.sh refresh-agent start`） |
| 本机 agent 崩了 | job 停在 `running` | 同上超时判定（`started_at` 超 60 分钟） |
| 行情同步失败（限流） | `cmd_site` 第 1 步失败但**继续执行**（现有语义） | 产物日期没推进 → 单调性校验拦截 → 不发布 → 标记 `failed` |
| 产物比线上旧 | 校验失败 | **不 push**，标记 `failed`，消息写明原因 |
| D1 写失败 | Worker 500 | 前端报错并停止轮询 |
| Mac 睡眠 | 子进程被中断 | `caffeinate -i` 包裹；agent 启动时校验 `caffeinate` 可用，不可用则告警 |

## 测试策略

| 层 | 测试 | 位置 |
|---|---|---|
| Worker 认证 | 无 cookie → 401；错 secret → 401 | `tests/test_refresh_worker.py`（node 跑，与 `test_decide_compare_parity.py` 同模式） |
| Worker 状态机 | 建 job → 领任务 → 回报 → 查状态 | 同上 |
| D1 建表幂等 | 重复执行 schema.sql 不报错 | 同上 |
| agent 接口契约 | 缺 token → 401；正常 → 返回任务 | `tests/test_refresh_agent.py` |
| 产物校验 | `verify_site_export.py` 的单调性守卫行为 | 复用既有测试（若已有） |
| **回归** | `uv run pytest tests/` 全绿 | 重点确认 CI 相关既有测试未破 |
| **端到端** | 手动：点按钮 → 看 D1 → 看线上 `dates.json` 更新 | 人工 |

## 改动清单

| 文件 | 改动 |
|---|---|
| `worker/index.js` | 新增 4 个端点 |
| `worker/schema.sql` | 新增 `refresh_jobs` 表 |
| `refresh_agent.py` | **新增**，本机 HTTP agent |
| `a-finder.sh` | 新增 `refresh-agent` 子命令；`cmd_site` 加单调性校验；**`cmd_site` 的计划日期改为对齐 `MAX(daily_prices.trade_date)`**（见下节） |
| `static/js/api.js` | 新增 2 个 fetch 函数 |
| `static/js/decide.js` | 按钮 + 轮询 |
| `templates/decide.html` | 按钮元素 |
| `.github/workflows/deploy-site.yml` | 删除 `peaceiris` 步骤（**不改其他逻辑**） |
| `tests/test_refresh_worker.py` | **新增** |
| `tests/test_refresh_agent.py` | **新增** |

**新增 secret**：`REFRESH_AGENT_SECRET`（`wrangler secret put`），Worker 与本机共享。

## 风险与取舍

| 风险 | 缓解 |
|---|---|
| Mac 睡眠/合盖 | `caffeinate -i`；agent 启动时检测并告警 |
| CI 与本机各跑各的库 | 两条谱系本就独立（`CLAUDE.md` 记载），不是新问题。CI 不再发布，不会互相覆盖 |
| `daily-audit` 仍会发布 | 极低频，仅在修复缺口时。可接受 |
| 本机库持续增长 | 现有 `cmd_site` 已处理 |
| 15~35 分钟无反馈 | 轮询 + 阶段提示（`message` 字段） |
| 本机 agent 是新的常驻进程 | 仿 `cmd_web` 的成熟模式；`status` 子命令便于排查 |
| 放弃隧道模式 | 3 秒轮询延迟可忽略，换来少一个常驻进程与故障点 |

## 与现有方案的对比

|  | GitHub Actions dispatch | 本方案 |
|---|---|---|
| 双写分叉 | ⚠️ 存在（CI 8 班/天 + 手动） | ✅ 消除（日常单写者） |
| 触发反馈 | 10~30 min 黑箱 | 15~35 min，有阶段提示 |
| CI 复杂度 | 4 个 workflow 共享缓存谱系 | 不变（但不碰发布环节） |
| 失败排查 | 看 Actions 日志 | 本机日志 + Worker 日志 + D1 |
| 运行依赖 | GitHub Actions 配额 + 队列可靠性 | 仅本机常开 |

## `cmd_site` 的计划日期必须对齐库里最新交易日（本次一并修）

**这是一个已存在的缺陷，不是新引入的**，但本方案会踩到它，所以必须一起修。

现状（`a-finder.sh:179-181`）：

```bash
local plan_date; plan_date="$(date +%F)"          # ← 今天
run_cmd plan build --db "$db" --date "$plan_date"
```

而 `pick_history` 写选股用的是 `latest_trade_date(conn)`（`pick_history.py:321`，即 `MAX(daily_prices.trade_date)`）。

**两者可能不一致**：如果增量同步失败（数据源限流 / 当天数据未出），选股落在库里的最新日（如 10-30），而计划却按今天（10-31）生成 → `_read_picks(conn, '2026-10-31')` 查不到行 → **空计划**。

CI 早已修过这个问题（`deploy-site.yml:215-219`）：

```bash
DATA_DATE=$(python3 -c "import sqlite3;print(sqlite3.connect('hs300.db').execute('SELECT MAX(trade_date) FROM daily_prices').fetchone()[0])")
python stock_cli.py plan build --db hs300.db --date "$DATA_DATE"
```

**本方案的做法**：`cmd_site` 的 `plan_date` 与 `plan build-all` 全部改用 `MAX(daily_prices.trade_date)`，与 CI 一致。

> 这条同时也解释了「为什么空计划比同步失败更隐蔽」：空计划不会让流水线失败，只会让当天没有计划行。CI 那边已经踩过（`CLAUDE.md`：「绝不生成空计划」）。

## 未决 / 待实施时确认

- `refresh_agent.py` 的轮询间隔（3s）与任务超时阈值（pending 10min / running 60min）是初值，实施时可调。
