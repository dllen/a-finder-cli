# Cloudflare Worker + D1：线上可写持仓 — 设计文档

**日期**：2026-10-25
**状态**：设计已确认（方案 A，三节逐节通过）

---

## 一、目标

用 Cloudflare Worker + D1（云 SQLite）让**线上可写**：在 ycu.space 上直接录入/修改真实持仓，不再需要本地跑 Flask。

**决策（用户确认）**：
- 目的：**让线上可写**
- 范围：**只录入持仓**（不搬选股/回测/计划生成）
- 认证：**密码 + HttpOnly Cookie**
- 同步：**D1 为持仓的唯一真相源**

**非目标**：
- ❌ 不把选股/回测/进化/转债迁到云端（Python 重计算留在本地与 CI）
- ❌ 不把 `daily_prices` / `trade_plan` / `daily_picks` 搬进 D1
- ❌ 不改现有静态导出链路（`data/*.json` 照旧）
- ❌ 不做多用户/权限体系（单用户密码）

---

## 二、现状与约束

| 项 | 值 |
|---|---|
| `hs300.db` | 154MB，21 张表（`trade_plan` 31.3 万行最大） |
| 持仓表 | `real_positions` / `real_cb_positions`（各几十行） |
| 现有部署 | 静态 JSON → git push gh-pages → ycu.space（Cloudflare 前置） |
| Cloudflare 配置 | 无（需新建） |
| Node / npm | v22 / 10.9，wrangler 可 npx |

**D1 免费额度**：5GB 存储 / 500 万行读/天 / **10 万行写/天**。
→ 只存 2 张持仓表（几十行）远低于额度；若全量搬 `trade_plan`（31 万行）会超写入额度，故不搬。

---

## 三、架构

```
┌──────────────────────────────────────────────────────────┐
│ 线上 ycu.space（Cloudflare）                              │
│                                                          │
│  静态页面 + data/*.json   ← 现有导出链路，完全不变         │
│         │                                                │
│  读：fetch data/*.json（现有逻辑不动）                     │
│  写/读持仓：decide.js ──> Worker (ycu.space/api/*) ──> D1 │
└──────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────┐
│ 本地 Flask + SQLite                                       │
│                                                          │
│  选股/回测/进化/转债/标注 ──> hs300.db（不变）              │
│  持仓读写 ──> Worker ──> D1（同一真相源）                   │
└──────────────────────────────────────────────────────────┘
```

### 核心决策

1. **D1 只存 2 张表**：`real_positions` + `real_cb_positions`。
2. **读写分道**：读走静态 JSON（快、CDN 缓存），写走 Worker → D1。
3. **对比计算移到前端**：
   - 现状：服务端算（`decide_service.py`），导出时快照进 `data/decide-<date>.json`
   - 问题：持仓在 D1 且随时可变，导出快照会过时
   - 方案：静态 JSON 只提供 plan（`rows`/`skipped`/`target_amount`/`stock_capital`），
     前端拿 plan + Worker 拿持仓 → **前端 JS 算偏差**
   - 配置 `PORTFOLIO_10W` 导出到 `data/decide-config.json`，保证前后端规则同源
4. **本地 Flask 的持仓读写也走 Worker**：本地 `real_positions` 表保留但不再写入
   （避免破坏现有测试）。

---

## 四、Worker 设计

### 4.1 路由

| 路由 | 方法 | 认证 | 说明 |
|---|---|---|---|
| `/api/decide/login` | POST | 否 | `{password}` → 校验后发 HttpOnly Cookie |
| `/api/decide/logout` | POST | 是 | 清 Cookie |
| `/api/decide/positions` | GET | 否 | 读持仓（公开只读） |
| `/api/decide/positions` | POST | **是** | 增/改持仓 |
| `/api/decide/positions/:code` | DELETE | **是** | 删持仓（`?cb=1` 删转债） |
| `/api/decide/health` | GET | 否 | `{ok, d1}` 健康检查 |

**读为什么公开**：持仓只含「代码 + 股数 + 成本」，不含账户/资金信息；前端首屏渲染需要它，
加认证会让页面必须先登录才能看。

### 4.2 认证

```
1. POST /login {password}
   → 比对 env.DECIDE_PASSWORD（wrangler secret）
   → 成功签发 token = base64(payload).HMAC-SHA256(secret)
     payload = {exp: now + 7d}
   → Set-Cookie: decide_auth=<token>; HttpOnly; Secure; SameSite=Strict;
                 Path=/; Max-Age=604800
2. 写操作：读 Cookie → 验签 + 验 exp → 失败 401
```

- 密码与签名密钥用 `wrangler secret put` 注入，不进代码库
- 用 Web Crypto API（`crypto.subtle.sign`）做 HMAC，Workers 原生支持
- 登录失败统一 401，不透露细节

### 4.3 D1 schema（`worker/schema.sql`）

```sql
CREATE TABLE IF NOT EXISTS real_positions (
    code          TEXT PRIMARY KEY,
    name          TEXT,
    shares        INTEGER NOT NULL,
    entry_price   REAL NOT NULL,
    stop_price    REAL,
    entry_date    TEXT,
    note          TEXT,
    updated_at    TEXT
);

CREATE TABLE IF NOT EXISTS real_cb_positions (
    code          TEXT PRIMARY KEY,
    name          TEXT,
    shares        INTEGER NOT NULL,
    entry_price   REAL NOT NULL,
    stop_price    REAL,
    entry_date    TEXT,
    note          TEXT,
    updated_at    TEXT
);
```

> 与本地迁移 `db/migrations/2026_10_25_real_positions.sql` **完全一致**，便于双向迁移。

### 4.4 CORS

Worker 需允许本地 Flask（`http://127.0.0.1:8000` / `http://localhost:8000`）跨域：

```
Access-Control-Allow-Origin: <origin 白名单>
Access-Control-Allow-Credentials: true
Access-Control-Allow-Methods: GET, POST, DELETE, OPTIONS
Access-Control-Allow-Headers: Content-Type
```

白名单：`https://ycu.space`、`https://dllen.github.io`、`http://127.0.0.1:8000`、`http://localhost:8000`

---

## 五、前端改造

### 5.1 `api.js` 增加 API base 与 Worker 调用

```javascript
// 写操作（持仓）统一走 Worker。
// 线上静态站：同源 ycu.space/api/*；本地 Flask：跨域到 ycu.space
export const API_BASE = isStatic() ? '' : 'https://ycu.space';
```

新增函数：`fetchDecidePositionsRemote` / `postDecidePosition` /
`deleteDecidePosition` / `loginDecide` / `logoutDecide` / `fetchDecideAuthState`
（全部带 `credentials: 'include'`）

### 5.2 新增 `static/js/decide_compare.js`

把 `decide_service.compare_plan_vs_actual` 的 **10 类偏差规则**移植为 JS：

- 输入：`plan.rows`、`positions`、`cb_positions`、`config`（来自 `data/decide-config.json`）
- 输出：`{deviations, pending_build, stock_used, cb_used, cash, danger_count, ...}`
- 与 Python 版共用同一份 `PORTFOLIO_10W` 配置，保证规则同源

### 5.3 `decide.js` 改造

- 持仓数据源：`fetchDecidePositionsRemote()`（Worker）
- 对比：`computeComparison(plan, positions, config)`（前端 JS）
- 写操作：`postDecidePosition` / `deleteDecidePosition`（带 cookie）
- 新增登录/登出 UI：未登录显示「登录以编辑」，登录后显示录入按钮

### 5.4 `export_json.py`

- 新增导出 `data/decide-config.json`（`PORTFOLIO_10W`）
- `data/decide-<date>.json` **移除**对比结果（`deviations`/`pending_build`/资金汇总），
  只保留 plan（`rows`/`skipped`/`target_amount`/`stock_capital`）
- `data/decide-positions.json` **保留**（Worker 不可用时的降级展示）

---

## 六、部署步骤（一次性，需手动执行）

```bash
npx wrangler login

npx wrangler d1 create a-finder
# → 把返回的 database_id 填进 wrangler.toml

npx wrangler d1 execute a-finder --file=worker/schema.sql --remote

npx wrangler secret put DECIDE_PASSWORD     # 登录密码
npx wrangler secret put DECIDE_SECRET       # 签名密钥（随机字符串）

npx wrangler deploy                         # 挂到 ycu.space/api/*
```

> 第 5 步占用 `ycu.space/api/*` 路由；现有静态站无 `/api/*` 路径，不冲突。

---

## 七、文件清单

| 文件 | 类型 | 职责 |
|---|---|---|
| `worker/index.js` | 新建 | Worker 入口：路由 + 认证 + D1 查询 + CORS |
| `worker/auth.js` | 新建 | HMAC 签名/验签、Cookie 解析 |
| `worker/schema.sql` | 新建 | D1 建表 |
| `wrangler.toml` | 新建 | Worker + D1 binding + 路由 |
| `static/js/decide_compare.js` | 新建 | JS 版对比逻辑 |
| `static/js/api.js` | 修改 | `API_BASE` + 6 个 Worker 调用 |
| `static/js/decide.js` | 修改 | 持仓走 Worker、对比前端算、登录 UI |
| `templates/decide.html` | 修改 | 登录区 |
| `export_json.py` | 修改 | 导出 `decide-config.json`；plan 快照去掉对比结果 |
| `tests/test_decide_compare_parity.py` | 新建 | JS 对比逻辑与 Python 版一致性 |
| `tests/test_decide_web.py` | 修改 | 适配新 decide API 形状 |

---

## 八、测试策略

| 测试 | 验证点 |
|---|---|
| **一致性测试（最关键）** | 同一 fixture（plan + positions + config），Python `decide_service` 与 JS `decide_compare` 输出的 deviations 种类/顺序/severity 完全一致 |
| `auth.js` 单测 | HMAC 签名/验签、过期 token 拒绝、篡改 token 拒绝 |
| 导出测试 | `decide-config.json` 存在；plan 快照不再含 `deviations` |
| 前端静态检查 | `node --check` 全部 JS |
| 回归 | 全量 pytest 通过 |

---

## 九、风险控制

| 风险 | 缓解 |
|---|---|
| 前后端两套对比逻辑漂移 | 一致性测试 + 共用 `decide-config.json` |
| Worker 挂 `ycu.space/api/*` 与静态站冲突 | 现有站点无 `/api/*` 路径，已核实 |
| 密码/密钥泄漏 | 只用 `wrangler secret`，不进 git；`.gitignore` 排除 `.dev.vars` |
| 跨域 cookie 被浏览器拒 | `SameSite=Strict` 在跨域场景需 `SameSite=None; Secure`（本地 Flask 场景单独放开白名单） |
| D1 写入额度 | 只写 2 张小表，日均几十次 |
| Worker 挂掉导致页面无持仓 | 静态 `data/decide-positions.json` 兜底只读展示 |
| 本地 Flask 测试受影响 | 本地 `real_positions` 表保留，现有测试不改； Flask 的 `/api/decide/comparison` 保留 |

---

## 十、验证计划

1. `npx wrangler dev` 本地起 Worker，验证 6 个路由 + 认证
2. 一致性测试通过（Python vs JS 对比结果逐项相同）
3. `npx wrangler deploy` 后，在 https://ycu.space/decide.html 登录 → 录入持仓 → 刷新可见
4. 未登录时写入返回 401，页面显示「登录以编辑」
5. 全量 pytest 通过

---

## 十一、评审 / 实施

- 决策已确认：线上可写 / 只录持仓 / 密码+Cookie / D1 唯一真相源 / 方案 A
- 设计三节（架构与数据流 / Worker 路由与认证 / 前端改造与部署）逐节获用户确认
- 接着调 writing-plans 拆实施计划
