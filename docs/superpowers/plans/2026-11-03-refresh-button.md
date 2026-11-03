# 登录后「刷新数据」按钮 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在决策台（`/decide`）加一个登录后才可见的「刷新数据」按钮，点击后由本机跑完整数据流水线并发布到站点，页面轮询反馈进度与结果。

**Architecture:** Cloudflare Worker 新增 4 个刷新端点（认证 + 任务队列 + 状态查询），任务状态存 D1 新表 `refresh_jobs`。本机新增 `refresh_agent.py` 常驻进程，每 3 秒轮询 Worker 领任务，跑 `bash a-finder.sh site --push`，完成后回报状态。GitHub Actions 的 `deploy-site.yml` 去掉发布步骤，本机成为 gh-pages 唯一日常写者。

**Tech Stack:** Cloudflare Workers (JS)、D1 (SQLite)、Python 3.11 stdlib (http.server / urllib)、Bash、原生 ES6 前端

**设计文档：** [`docs/superpowers/specs/2026-11-03-refresh-button-design.md`](../specs/2026-11-03-refresh-button-design.md)

---

## 文件结构

| 文件 | 责任 |
|---|---|
| `worker/index.js`（改） | 新增 4 个刷新端点。**不含计算逻辑** |
| `worker/schema.sql`（改） | 新增 `refresh_jobs` 表（D1 建表幂等） |
| `refresh_agent.py`（新） | 本机常驻 agent：轮询领任务 → 跑流水线 → 回报状态 |
| `a-finder.sh`（改） | 新增 `refresh-agent` 子命令；`cmd_site` 加单调性校验 + 计划日期对齐 |
| `static/js/api.js`（改） | 新增 `fetchRefresh()` / `fetchRefreshStatus(jobId)` |
| `static/js/decide.js`（改） | 按钮渲染 + 递归轮询 |
| `templates/decide.html`（改） | 按钮元素 + 状态提示容器 |
| `.github/workflows/deploy-site.yml`（改） | 删除 `peaceiris` 发布步骤 |
| `tests/test_refresh_worker.py`（新） | Worker 端点契约（node 跑） |
| `tests/test_refresh_agent.py`（新） | agent 纯逻辑单测 |
| `tests/test_site_publish_guard.py`（新） | `cmd_site` 单调性校验 + 计划日期对齐（bash 级） |

**职责边界：**
- Worker 只做「认证 / 排队 / 查状态」，**不做任何计算**
- agent 只做「领任务 / 调 `a-finder.sh` / 回报」，**不碰业务逻辑**
- 所有选股/计划/导出逻辑仍在既有 Python 模块里，**本计划不改它们**

---

## Task 1: D1 `refresh_jobs` 表 + Worker 任务队列端点

**Files:**
- Modify: `worker/schema.sql`
- Modify: `worker/index.js`（在 `dispatch-test` 端点之后、`return json({ error: 'not found' })` 之前插入）
- Test: `tests/test_refresh_worker.py`（新建）

**背景：** Worker 在 Cloudflare 边缘，无法主动访问本机 `127.0.0.1`。因此由**本机 agent 轮询 Worker 领任务**（拉模式）。Worker 侧需要 4 个端点：

| 端点 | 认证 | 用途 |
|---|---|---|
| `POST /api/refresh` | `decide_auth` cookie | 前端点按钮 → 建任务 |
| `GET /api/refresh/status?job_id=` | `decide_auth` cookie | 前端轮询进度 |
| `GET /api/refresh/next` | `X-Agent-Secret` 头 | agent 领任务 |
| `POST /api/refresh/result` | `X-Agent-Secret` 头 | agent 回报结果 |

> **认证必须分两套**：前端端点用登录 cookie（`readAuth`）；agent 端点用独立的 `REFRESH_AGENT_SECRET` 头。**不复用 `DECIDE_SECRET`**——那是登录态签名密钥，混用会让职责纠缠（一个泄露就同时丢掉登录态和 agent 通道）。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_refresh_worker.py`：

```python
"""Worker 刷新端点的契约测试。

用 node 跑 worker/index.js 的 fetch 处理器，用一个假的 env（内存版 D1）验证：
- 前端端点必须登录（无 cookie → 401）
- agent 端点必须带 X-Agent-Secret（错 secret → 401）
- 任务状态机：建 → 领 → 回报 → 查
"""
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WORKER_DIR = ROOT / "worker"

HARNESS = r"""
import { readFileSync } from 'node:fs';

// ---- 假 D1：只实现本测试用到的语句 ----
// 注意：Worker 里既有 `.prepare(sql).bind(...).run()`，也有 `.prepare(sql).first()`
// （无 bind，例如 SELECT COUNT(*)）。两种调用形态都要支持。
class FakeD1 {
  constructor() { this.rows = new Map(); }

  prepare(sql) {
    const self = this;

    const exec = async (args) => {
      if (sql.includes('INSERT INTO refresh_jobs')) {
        const o = Object.fromEntries(args.map((v, i) => [COLS[i], v]));
        self.rows.set(o.job_id, { ...o });
        return { success: true, meta: { changes: 1 } };
      }
      if (sql.includes("UPDATE refresh_jobs SET status = 'running'")) {
        const r = self.rows.get(args[1]);
        if (!r || r.status !== 'pending') return { success: false, meta: { changes: 0 } };
        r.status = 'running';
        r.started_at = args[0];
        return { success: true, meta: { changes: 1 } };
      }
      if (sql.includes('UPDATE refresh_jobs SET status = ?')) {
        const r = self.rows.get(args[4]);
        if (!r) return { success: false, meta: { changes: 0 } };
        r.status = args[0];
        r.latest_date = args[1];
        r.message = args[2];
        r.finished_at = args[3];
        return { success: true, meta: { changes: 1 } };
      }
      if (sql.includes('SELECT * FROM refresh_jobs WHERE job_id')) {
        const r = self.rows.get(args[0]);
        return { results: r ? [r] : [] };
      }
      if (sql.includes("SELECT * FROM refresh_jobs WHERE status = 'pending'")) {
        const out = [...self.rows.values()].filter(r => r.status === 'pending');
        return { results: out };
      }
      if (sql.includes('SELECT COUNT(*) AS n FROM refresh_jobs')) {
        const n = [...self.rows.values()]
          .filter(r => r.status === 'pending' || r.status === 'running').length;
        return { results: [{ n }] };
      }
      return { results: [] };
    };

    return {
      bind(...args) {
        return {
          run: () => exec(args),
          first: async () => { const r = await exec(args); return r.results ? r.results[0] : null; },
          all: async () => { const r = await exec(args); return r.results || []; },
        };
      },
      first: async () => { const r = await exec([]); return r.results ? r.results[0] : null; },
      all: async () => { const r = await exec([]); return r.results || []; },
    };
  }
}

const COLS = ['job_id','status','requested_by','created_at','started_at','finished_at','latest_date','message'];

const env = {
  DB: new FakeD1(),
  DECIDE_USER: 'u', DECIDE_PASSWORD: 'p', DECIDE_SECRET: 's',
  REFRESH_AGENT_SECRET: 'agent-secret',
};

// 用一个伪造的 Cookie 绕过 readAuth：直接构造合法 token
import { signToken } from './auth.js';
const token = await signToken({ user: 'u', exp: Date.now() + 3600_000 }, 's');
const COOKIE = 'decide_auth=' + token;

const mod = await import('./index.js');

function req(path, opts = {}) { return mod.default.fetch(new Request('https://ycu.space' + path, opts), env, {}); }

const out = [];

// 1. 未登录 → 401
let r = await req('/api/refresh', { method: 'POST' });
out.push(['refresh_noauth', r.status]);

// 2. agent 端点错 secret → 401
r = await req('/api/refresh/next', { headers: { 'X-Agent-Secret': 'wrong' } });
out.push(['next_badsecret', r.status]);

// 3. 已登录建任务 → 200 + job_id
r = await req('/api/refresh', { method: 'POST', headers: { Cookie: COOKIE } });
const created = await r.json();
out.push(['create', r.status, typeof created.job_id === 'string' && created.job_id.length > 0]);

// 4. 重复建 → 409
r = await req('/api/refresh', { method: 'POST', headers: { Cookie: COOKIE } });
out.push(['duplicate', r.status]);

// 5. agent 领任务 → 拿到 job，状态变 running
r = await req('/api/refresh/next', { headers: { 'X-Agent-Secret': 'agent-secret' } });
const next = await r.json();
out.push(['next', r.status, next.job_id === created.job_id]);

// 6. 查状态 → running
r = await req('/api/refresh/status?job_id=' + created.job_id, { headers: { Cookie: COOKIE } });
const st = await r.json();
out.push(['status_running', r.status, st.status]);

// 7. agent 回报成功
r = await req('/api/refresh/result', {
  method: 'POST',
  headers: { 'X-Agent-Secret': 'agent-secret', 'Content-Type': 'application/json' },
  body: JSON.stringify({ job_id: created.job_id, status: 'success', latest_date: '2026-11-03', message: 'ok' }),
});
out.push(['result', r.status]);

// 8. 查状态 → success + latest_date
r = await req('/api/refresh/status?job_id=' + created.job_id, { headers: { Cookie: COOKIE } });
const st2 = await r.json();
out.push(['status_success', r.status, st2.status, st2.latest_date]);

// 9. 建任务后已无 pending/running → 可以再建
r = await req('/api/refresh', { method: 'POST', headers: { Cookie: COOKIE } });
out.push(['create_again', r.status]);

process.stdout.write(JSON.stringify(out));
"""


def _run_worker():
    script = HARNESS.replace("import { readFileSync } from 'node:fs';\n", "")
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=str(WORKER_DIR), capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, f"node 失败: {proc.stderr}"
    return json.loads(proc.stdout)


def test_refresh_endpoints_contract():
    """刷新端点的完整契约：认证、409 互斥、状态机。"""
    steps = _run_worker()
    got = {s[0]: s[1:] for s in steps}
    assert got["refresh_noauth"] == (401,), got
    assert got["next_badsecret"] == (401,), got
    assert got["create"] == (200, True), got
    assert got["duplicate"] == (409,), got
    assert got["next"] == (200, True), got
    assert got["status_running"] == (200, "running"), got
    assert got["result"] == (200,), got
    assert got["status_success"] == (200, "success", "2026-11-03"), got
    assert got["create_again"] == (200,), got


def test_node_available():
    """node 必须可用（否则上面的测试是假绿）。"""
    proc = subprocess.run(["node", "--version"], capture_output=True, text=True)
    assert proc.returncode == 0, "node 不可用，无法跑 Worker 契约测试"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_refresh_worker.py -v`
Expected: FAIL —— `node 失败: ... Cannot find module` 或断言失败（端点还不存在）

- [ ] **Step 3: 加 D1 表**

在 `worker/schema.sql` 末尾追加：

```sql
-- 刷新任务状态表：只存状态，不存行情。
-- 每天个位数到几十行，相对免费版 10 万行/天配额可忽略。
CREATE TABLE IF NOT EXISTS refresh_jobs (
    job_id       TEXT PRIMARY KEY,
    status       TEXT NOT NULL,          -- pending | running | success | failed
    requested_by TEXT,                   -- 登录用户名
    created_at   TEXT,
    started_at   TEXT,
    finished_at  TEXT,
    latest_date  TEXT,                   -- 刷新后站点的最新数据日期
    message      TEXT                    -- 摘要 / 错误原因
);
```

- [ ] **Step 4: 加 Worker 端点**

在 `worker/index.js` 里，`// ---- 手动触发 dispatch 测试（需认证，验证 GITHUB_DISPATCH_TOKEN 配好没）----` 那段**之后**、`return json({ error: 'not found' }, 404, origin);` **之前**，插入：

```javascript
    // =======================================================================
    // 刷新数据：本机 agent 领任务 → 跑流水线 → 回报状态
    //
    // 触发方向是「拉模式」：Worker 在边缘够不到本机 127.0.0.1，
    // 所以由本机 refresh_agent.py 轮询 /api/refresh/next 领任务。
    //
    // 认证分两套，**故意不共用密钥**：
    //   - 前端端点用登录 cookie（decide_auth）
    //   - agent 端点用独立的 REFRESH_AGENT_SECRET 头
    // 共用会让一个泄露同时丢掉登录态和 agent 通道。
    // =======================================================================

    // ---- 刷新：建任务（需登录）----
    if (path === '/api/refresh' && method === 'POST') {
      const payload = await readAuth(request, env);
      if (!payload) {
        return json({ error: '未登录' }, 401, origin);
      }
      // 互斥：已有 pending/running 的任务 → 409（防连点排出多班流水线）
      const busy = await env.DB.prepare(
        `SELECT COUNT(*) AS n FROM refresh_jobs WHERE status IN ('pending','running')`,
      ).first();
      if (busy && Number(busy.n || 0) > 0) {
        return json({ error: '已有刷新任务进行中' }, 409, origin);
      }
      const jobId = crypto.randomUUID();
      const now = nowStamp();
      await env.DB.prepare(
        `INSERT INTO refresh_jobs
         (job_id, status, requested_by, created_at)
         VALUES (?,?,?,?)`,
      ).bind(jobId, 'pending', payload.user || null, now).run();
      return json({ ok: true, job_id: jobId }, 200, origin);
    }

    // ---- 刷新：查状态（需登录）----
    if (path === '/api/refresh/status' && method === 'GET') {
      const payload = await readAuth(request, env);
      if (!payload) {
        return json({ error: '未登录' }, 401, origin);
      }
      const jobId = url.searchParams.get('job_id') || '';
      if (!jobId) {
        return json({ error: 'job_id 必填' }, 400, origin);
      }
      const row = await env.DB.prepare(
        `SELECT * FROM refresh_jobs WHERE job_id = ?`,
      ).bind(jobId).first();
      if (!row) {
        return json({ error: '任务不存在' }, 404, origin);
      }
      return json({
        job_id: row.job_id,
        status: row.status,
        latest_date: row.latest_date || null,
        message: row.message || null,
        created_at: row.created_at || null,
        started_at: row.started_at || null,
        finished_at: row.finished_at || null,
      }, 200, origin);
    }

    // ---- 刷新：agent 领任务（需 X-Agent-Secret）----
    if (path === '/api/refresh/next' && method === 'GET') {
      if (!env.REFRESH_AGENT_SECRET
          || request.headers.get('X-Agent-Secret') !== env.REFRESH_AGENT_SECRET) {
        return json({ error: 'unauthorized' }, 401, origin);
      }
      const row = await env.DB.prepare(
        `SELECT * FROM refresh_jobs WHERE status = 'pending' ORDER BY created_at LIMIT 1`,
      ).first();
      if (!row) {
        return json({ job_id: null }, 200, origin);
      }
      // 用带条件的 UPDATE 抢占：并发领任务时只有一个能成功改到 running
      const upd = await env.DB.prepare(
        `UPDATE refresh_jobs SET status = 'running', started_at = ? WHERE job_id = ? AND status = 'pending'`,
      ).bind(nowStamp(), row.job_id).run();
      const changes = upd && upd.meta && typeof upd.meta.changes === 'number'
        ? upd.meta.changes
        : 1;
      if (!changes) {
        // 别人抢先领走了
        return json({ job_id: null }, 200, origin);
      }
      return json({
        job_id: row.job_id,
        requested_by: row.requested_by || null,
        created_at: row.created_at || null,
      }, 200, origin);
    }

    // ---- 刷新：agent 回报结果（需 X-Agent-Secret）----
    if (path === '/api/refresh/result' && method === 'POST') {
      if (!env.REFRESH_AGENT_SECRET
          || request.headers.get('X-Agent-Secret') !== env.REFRESH_AGENT_SECRET) {
        return json({ error: 'unauthorized' }, 401, origin);
      }
      const body = await request.json().catch(() => ({}));
      const jobId = String(body.job_id || '').trim();
      if (!jobId) {
        return json({ error: 'job_id 必填' }, 400, origin);
      }
      const st = body.status === 'success' ? 'success' : 'failed';
      await env.DB.prepare(
        `UPDATE refresh_jobs SET status = ?, latest_date = ?, message = ?, finished_at = ? WHERE job_id = ?`,
      ).bind(
        st,
        body.latest_date || null,
        String(body.message || '').slice(0, 500) || null,
        nowStamp(),
        jobId,
      ).run();
      return json({ ok: true }, 200, origin);
    }
```

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/test_refresh_worker.py -v`
Expected: PASS（2 passed）

- [ ] **Step 6: 提交**

```bash
git add worker/schema.sql worker/index.js tests/test_refresh_worker.py
git commit -m "feat(worker): 刷新任务端点 + D1 refresh_jobs 表"
```

---

## Task 2: `cmd_site` 的两道护栏（单调性校验 + 计划日期对齐）

**Files:**
- Modify: `a-finder.sh:162-203`（`cmd_site`）
- Test: `tests/test_site_publish_guard.py`（新建）

**背景（两个既有缺陷，本方案会踩到）：**

1. **单调性校验缺失**：`cmd_site` 当前无条件 push。若产出比线上旧（孤儿库 / 同步失败），会把陈旧站点推上去 —— 2026-10-29 事故的直接形态（「页面正常、数据停在半个月前」，比空站点更隐蔽）。CI 侧已有这道门（`verify_site_export.py --not-older-than`），本机侧要补上。

2. **计划日期错位**：`cmd_site:179` 用 `plan_date="$(date +%F)"`（今天），而 `pick_history.py:321` 用 `latest_trade_date(conn)`（`MAX(daily_prices.trade_date)`）。同步失败时两者不一致 → `_read_picks(conn, 今天)` 查不到行 → **空计划**。CI 已在 `deploy-site.yml:215` 修过，`cmd_site` 漏了。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_site_publish_guard.py`：

```python
"""`a-finder.sh cmd_site` 的两道护栏：

1. 发布前单调性校验（产物最新日不得比线上旧）
2. 计划日期对齐 MAX(daily_prices.trade_date)，而不是今天

这两个都是 bash 里的行为，用「桩掉外部命令」的方式测：
- 桩掉 git push：断言产物比线上旧时**没有** push
- 桩掉 stock_cli.py：断言 plan build 收到的是库里的最大交易日
"""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SH = ROOT / "a-finder.sh"


def _bash_source():
    """抽出 cmd_site 的函数体（避免真跑整条流水线）。"""
    text = SH.read_text(encoding="utf-8")
    m = re.search(r"^cmd_site\(\) \{(.*?)^\}", text, re.S | re.M)
    assert m, "cmd_site 找不到"
    return m.group(1)


def test_cmd_site_uses_max_trade_date_not_today():
    """计划日期必须取库里最大交易日，不能用今天。"""
    body = _bash_source()
    # 必须查 MAX(daily_prices.trade_date)
    assert "MAX(trade_date) FROM daily_prices" in body, body
    # 不能直接用 date +%F 作为 plan_date
    assert 'plan_date="$(date +%F)"' not in body, (
        "cmd_site 仍在用今天作为计划日期 —— 同步失败时会产出空计划"
    )


def test_cmd_site_has_monotonic_guard_before_push():
    """push 之前必须有单调性校验，且校验失败要中止。"""
    body = _bash_source()
    assert "verify_site_export.py" in body, body
    assert "--not-older-than" in body, body
    # 校验失败必须阻止 push：校验语句要在 push 之前，且带 || exit 1
    guard = body.index("verify_site_export.py")
    push = body.index("push_site_to_ghpages")
    assert guard < push, "单调性校验必须在 push 之前"
    tail = body[guard:push]
    assert "|| exit 1" in tail or "|| return 1" in tail, (
        "校验失败必须中止发布（否则守卫形同虚设）"
    )


def test_cmd_site_reads_live_latest_before_push():
    """单调性下限必须取自线上 dates.json，而不是硬编码。"""
    body = _bash_source()
    assert "ycu.space/data/dates.json" in body, body
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_site_publish_guard.py -v`
Expected: FAIL —— 3 failed（`cmd_site` 还没有这些行为）

- [ ] **Step 3: 加 `run_pyc` helper（跑内联 python）**

`run_py` 只接受**脚本路径**（`"$ROOT_DIR/.venv/bin/python" "$@"`），不支持 `-c`。`cmd_site` 需要跑两段内联 python（查库、取线上日期），所以先加一个 helper。

在 `a-finder.sh` 的 `run_py()` 定义**之后**插入：

```bash
# 跑一段内联 python（run_py 只接受脚本路径，不支持 -c）
run_pyc() {
  if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    "$ROOT_DIR/.venv/bin/python" -c "$1"
  else
    python3 -c "$1"
  fi
}
```

- [ ] **Step 4: 改 `cmd_site`**

把 `a-finder.sh` 中 `cmd_site` 整个函数替换为：

```bash
cmd_site() {
  # 用法: a-finder.sh site [DB] [TOP] [OUT] [--push]
  local db="${1:-$DEFAULT_DB}"
  local top="${2:-20}"
  local out="${3:-site}"
  local push=0
  for a in "$@"; do
    if [[ "$a" == "--push" ]]; then push=1; fi
  done

  echo "=== 本地数据生成 ==="
  echo "db=$db top=$top out=$out push=$push"
  echo ""

  echo "[1/5] 增量同步 + 选股..."
  cmd_sync_incremental "$db" "$top" pick-history

  # 计划日期对齐**库里真正的行情最新日**，而不是硬编码今天：
  # 同步失败时（数据源限流 / 当天数据未出）选股会落在库里的最大交易日，
  # 若此时按今天生成计划就查不到选股行 → 空计划（不报错，最难查的一类故障）。
  # CI 的 deploy-site.yml 早已这么修过（DATA_DATE），这里对齐。
  local plan_date
  plan_date="$(run_pyc "import sqlite3;print(sqlite3.connect('$db').execute('SELECT MAX(trade_date) FROM daily_prices').fetchone()[0] or '')")"
  if [[ -z "$plan_date" ]]; then
    echo "ERROR: 库里没有行情数据（MAX(daily_prices.trade_date) 为空），中止" >&2
    return 1
  fi
  echo "[2/5] 生成 $plan_date 交易计划（库里行情最新日）..."
  run_cmd plan build --db "$db" --date "$plan_date"

  echo "[3/5] 构建 10 个资金档位组合..."
  run_cmd plan build-all --db "$db" --strategy linyuan --tiers "$TIERS"

  echo "[4/5] 回补历史组合（since ${BACKFILL_ANCHOR}）..."
  run_cmd plan build-all --backfill --since "$BACKFILL_ANCHOR" --db "$db"

  echo "[5/5] 导出静态数据到 $out..."
  run_py "$ROOT_DIR/export_json.py" --db "$db" --out "$out"

  cp "$out/index.html" "$out/404.html"
  touch "$out/.nojekyll"
  echo "HTML: $(ls -1 "$out"/*.html 2>/dev/null | wc -l | tr -d ' ')  JSON: $(ls -1 "$out/data/"*.json 2>/dev/null | wc -l | tr -d ' ')"

  # ---- 发布前的单调性守卫（不可省）----
  # 2026-10-29 事故：孤儿库导出的 dates.json 也能通过「有没有数据」的校验，
  # 于是把比线上旧 16 天的站点推上去 —— 页面看着正常、数据停在半个月前，
  # 比空站点更隐蔽。拿**线上最新日**当下限：宁可这次不发布，也不发布更旧的。
  local live_latest
  live_latest="$(curl -sf --max-time 20 "https://ycu.space/data/dates.json" \
    | run_pyc "import json,sys;d=json.load(sys.stdin).get('dates') or [''];print(d[0])" 2>/dev/null || echo "")"
  if [[ -z "$live_latest" ]]; then
    # 查不到线上日期时不敢用「无下限」放行：那正是空站点事故的同款漏洞。
    echo "::error::查不到线上最新日，改用库里行情最新日 $plan_date 作为下限"
    live_latest="$plan_date"
  fi
  echo "线上最新日: $live_latest（产物的下限）"
  if ! run_py "$ROOT_DIR/scripts/verify_site_export.py" --site "$out" --not-older-than "$live_latest"; then
    echo "ERROR: 导出产物比线上旧或不完整，已阻止发布（避免用旧数据覆盖更新的线上站点）" >&2
    return 1
  fi

  if [[ "$push" == "1" ]]; then
    push_site_to_ghpages "$out"
  else
    echo "预览: cd $out && python3 -m http.server 8000"
    echo "推送: bash a-finder.sh site $db $top $out --push"
  fi
}
```

> `verify_site_export.py` 是**脚本路径 + 参数**，正好匹配 `run_py` 的签名，所以直接用 `run_py` 调用，不要套 `run_pyc`。

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/test_site_publish_guard.py -v`
Expected: PASS（3 passed）

- [ ] **Step 6: 确认 bash 语法正确**

Run: `bash -n a-finder.sh`
Expected: 无输出（退出码 0）

- [ ] **Step 7: 提交**

```bash
git add a-finder.sh tests/test_site_publish_guard.py
git commit -m "fix(site): 发布前单调性守卫 + 计划日期对齐库里最新交易日"
```

---
---

## Task 3: 本机 refresh agent（Python）

**Files:**
- Create: `refresh_agent.py`
- Test: `tests/test_refresh_agent.py`（新建）

**背景：** Worker 在 Cloudflare 边缘，够不到本机 `127.0.0.1`。所以由本机 agent 轮询 Worker 的任务队列（拉模式）。

**职责边界：** agent 只做「领任务 → 调 `a-finder.sh` → 回报状态」，**不含任何业务计算**。业务逻辑仍在既有 Python 模块里。

**设计要点：**

- 用 stdlib（`urllib` + `http.server`），不引入新依赖
- 单任务互斥：本地同时只允许一个刷新任务在跑
- `caffeinate -i` 包裹子进程，防止 Mac 睡眠中断任务
- 配置走环境变量（`REFRESH_WORKER_BASE`、`REFRESH_AGENT_SECRET`、`REFRESH_POLL_SECONDS`、`REFRESH_DB`、`REFRESH_SITE_DIR`）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_refresh_agent.py`：

```python
"""refresh_agent 的纯逻辑单测（不起真实网络、不跑真实流水线）。

被测的三个纯函数：
- build_report_url / 判定成功失败的逻辑
- latest_date_from_site(site_dir)：读 site/data/dates.json 的最新日
- summarize_result(rc)：把子进程退出码 + 产物日期汇总成回报 payload
"""
import json
from pathlib import Path

from refresh_agent import (
    latest_date_from_site,
    summarize_result,
    build_result_payload,
)


def test_latest_date_from_site_reads_newest_first(tmp_path):
    """dates.json 是新→旧排列，dates[0] 就是最新日。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "dates.json").write_text(
        json.dumps({"dates": ["2026-11-03", "2026-10-30"]}), encoding="utf-8"
    )
    assert latest_date_from_site(tmp_path) == "2026-11-03"


def test_latest_date_from_site_missing_file_returns_none(tmp_path):
    """没有产物时返回 None（不是抛异常 —— agent 不能因为读不到就崩）。"""
    assert latest_date_from_site(tmp_path) is None


def test_latest_date_from_site_broken_json_returns_none(tmp_path):
    """JSON 坏了也返回 None，不能抛。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "dates.json").write_text("{not json", encoding="utf-8")
    assert latest_date_from_site(tmp_path) is None


def test_summarize_result_success():
    """退出码 0 + 有产物日期 → success。"""
    out = summarize_result(returncode=0, latest_date="2026-11-03", log_tail="all good")
    assert out["status"] == "success"
    assert out["latest_date"] == "2026-11-03"


def test_summarize_result_nonzero_exit_is_failed():
    """退出码非 0 → failed，且要带上原因。"""
    out = summarize_result(returncode=1, latest_date=None, log_tail="boom")
    assert out["status"] == "failed"
    assert "boom" in out["message"]


def test_summarize_result_zero_exit_but_no_date_is_failed():
    """退出码 0 但没有产物日期 → failed。

    这条很关键：cmd_site 的单调性守卫失败时 return 1，但如果将来某条路径
    意外 return 0 又没产物，仍必须当失败 —— 页面不能显示"成功"却什么都没更新。
    """
    out = summarize_result(returncode=0, latest_date=None, log_tail="no output")
    assert out["status"] == "failed"


def test_build_result_payload_shape():
    """回报 payload 必须含 Worker 约定的字段名。"""
    payload = build_result_payload(
        job_id="j-1",
        status="success",
        latest_date="2026-11-03",
        message="done",
    )
    assert payload == {
        "job_id": "j-1",
        "status": "success",
        "latest_date": "2026-11-03",
        "message": "done",
    }
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_refresh_agent.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'refresh_agent'`

- [ ] **Step 3: 写 `refresh_agent.py`**

创建 `refresh_agent.py`：

```python
#!/usr/bin/env python3
"""刷新 agent：在本机轮询 Worker 的刷新任务队列，跑完流水线回报状态。

为什么是「拉模式」：
  Worker 跑在 Cloudflare 边缘，**够不到本机的 127.0.0.1**；反过来本机可以出网
  （它本来就要 push gh-pages）。所以由本机主动去 Worker 领任务，而不是 Worker 推过来。
  代价是几秒的感知延迟，换来少一个常驻隧道进程、少一个故障点。

职责边界：只做「领任务 → 调 a-finder.sh → 回报状态」。**不含任何业务计算**
（选股/计划/导出逻辑都在既有 Python 模块里，不在这里重复实现）。

配置（环境变量）：
  REFRESH_WORKER_BASE    Worker 基址，默认 https://ycu.space
  REFRESH_AGENT_SECRET   与 Worker 的 REFRESH_AGENT_SECRET 一致（必需）
  REFRESH_POLL_SECONDS   轮询间隔秒数，默认 3
  REFRESH_DB             SQLite 路径，默认 hs300.db
  REFRESH_SITE_DIR       站点产物目录，默认 site
  REFRESH_LOG            日志路径，默认 logs/refresh.log
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parent

WORKER_BASE = os.environ.get("REFRESH_WORKER_BASE", "https://ycu.space").rstrip("/")
AGENT_SECRET = os.environ.get("REFRESH_AGENT_SECRET", "")
POLL_SECONDS = float(os.environ.get("REFRESH_POLL_SECONDS", "3"))
DB_PATH = os.environ.get("REFRESH_DB", str(ROOT / "hs300.db"))
SITE_DIR = Path(os.environ.get("REFRESH_SITE_DIR", str(ROOT / "site")))
LOG_PATH = Path(os.environ.get("REFRESH_LOG", str(ROOT / "logs" / "refresh.log")))

# 阶段提示：回报给前端的 message，用户看得到
MSG_SYNC = "正在同步行情并计算选股/计划（通常 10~30 分钟）"
MSG_PUBLISH = "正在导出并发布到站点"


def latest_date_from_site(site_dir: Path) -> Optional[str]:
    """读 site/data/dates.json 的最新日；读不到返回 None。

    dates.json 是**新→旧**排列，dates[0] 就是最新日。
    任何异常都返回 None —— agent 不能因为读不到产物就崩掉。
    """
    try:
        payload = json.loads(
            (Path(site_dir) / "data" / "dates.json").read_text(encoding="utf-8")
        )
    except Exception:
        return None
    dates = payload.get("dates") or []
    return dates[0] if dates else None


def summarize_result(returncode: int, latest_date: Optional[str], log_tail: str) -> Dict[str, str]:
    """把「子进程退出码 + 产物日期 + 日志尾巴」汇总成结果摘要。

    退出码 0 但没有产物日期 → **failed**。
    这条不能省：将来若某条路径意外返回 0 又没产物，页面不能显示"成功"
    却什么都没更新。
    """
    tail = (log_tail or "").strip()
    if returncode == 0 and latest_date:
        return {
            "status": "success",
            "latest_date": latest_date,
            "message": f"已更新，最新数据 {latest_date}",
        }
    if returncode == 0:
        return {
            "status": "failed",
            "latest_date": "",
            "message": "流水线退出码为 0 但没有产出数据日期，判定为失败",
        }
    return {
        "status": "failed",
        "latest_date": "",
        "message": f"流水线失败（退出码 {returncode}）" + (f"：{tail[-300:]}" if tail else ""),
    }


def build_result_payload(
    job_id: str,
    status: str,
    latest_date: Optional[str],
    message: str,
) -> Dict[str, Any]:
    """构造 POST /api/refresh/result 的 payload（字段名与 Worker 约定一致）。"""
    return {
        "job_id": job_id,
        "status": status,
        "latest_date": latest_date or "",
        "message": message or "",
    }


def _http(url: str, *, method: str = "GET", headers: Optional[Dict[str, str]] = None,
          body: Optional[Dict[str, Any]] = None, timeout: int = 20) -> Dict[str, Any]:
    """发一个 JSON 请求，返回解析后的 dict；失败抛异常由调用方兜。"""
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def fetch_task() -> Optional[str]:
    """向 Worker 领一个任务；没有任务返回 None。"""
    try:
        payload = _http(
            f"{WORKER_BASE}/api/refresh/next",
            headers={"X-Agent-Secret": AGENT_SECRET},
            timeout=15,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[agent] 领任务失败: {exc}", flush=True)
        return None
    return payload.get("job_id") or None


def report_result(payload: Dict[str, Any]) -> bool:
    """把结果回报给 Worker。返回是否成功。"""
    try:
        _http(
            f"{WORKER_BASE}/api/refresh/result",
            method="POST",
            headers={"X-Agent-Secret": AGENT_SECRET},
            body=payload,
            timeout=20,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[agent] 回报失败: {exc}", flush=True)
        return False


def _run_pipeline() -> tuple[int, str]:
    """跑 `a-finder.sh site --push`，返回 (退出码, 日志尾巴)。

    用 caffeinate 包裹：Mac 常开但合盖会睡，睡眠会中断同步/回补，
    留下一个半成品产物。
    """
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    # caffeinate 不存在时直接跑，不要因为缺它就完全不刷新
    if shutil_which("caffeinate"):
        argv = ["caffeinate", "-i", "bash", "a-finder.sh", "site", DB_PATH, "20", str(SITE_DIR), "--push"]
    else:
        argv = ["bash", "a-finder.sh", "site", DB_PATH, "20", str(SITE_DIR), "--push"]

    with LOG_PATH.open("a", encoding="utf-8") as log:
        log.write(f"\n=== refresh 开始 {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
        log.write(f"$ {' '.join(argv)}\n")
        log.flush()
        try:
            proc = subprocess.run(
                argv, cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT,
                env={**os.environ},
            )
            rc = proc.returncode
        except Exception as exc:  # noqa: BLE001
            log.write(f"\n启动失败: {exc}\n")
            log.flush()
            return 1, f"启动失败: {exc}"
    tail = _read_log_tail()
    return rc, tail


def shutil_which(name: str) -> Optional[str]:
    """shutil.which 的薄包装（单独抽出来便于测试时打桩）。"""
    import shutil

    return shutil.which(name)


def _read_log_tail(n: int = 4000) -> str:
    """读日志末尾若干字符（给用户看失败原因用）。"""
    try:
        text = LOG_PATH.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    return text[-n:]


def main() -> int:
    if not AGENT_SECRET:
        print(
            "缺少 REFRESH_AGENT_SECRET 环境变量。"
            "它必须与 Worker 的同名 secret 一致（wrangler secret put REFRESH_AGENT_SECRET）。",
            file=sys.stderr,
        )
        return 2

    print(f"[agent] 启动：Worker={WORKER_BASE} 轮询={POLL_SECONDS}s", flush=True)
    busy = False

    while True:
        try:
            if not busy:
                job_id = fetch_task()
                if job_id:
                    busy = True
                    print(f"[agent] 领到任务 {job_id}", flush=True)
                    print(f"[agent] {MSG_SYNC}", flush=True)
                    rc, tail = _run_pipeline()
                    latest = latest_date_from_site(SITE_DIR)
                    result = summarize_result(rc, latest, tail)
                    payload = build_result_payload(
                        job_id=job_id,
                        status=result["status"],
                        latest_date=result["latest_date"],
                        message=result["message"],
                    )
                    report_result(payload)
                    print(f"[agent] 任务 {job_id} 完成：{result['status']} {result['message']}", flush=True)
                    busy = False
        except KeyboardInterrupt:
            print("\n[agent] 收到中断，退出", flush=True)
            return 0
        except Exception as exc:  # noqa: BLE001
            print(f"[agent] 循环异常: {exc}", flush=True)
            busy = False
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_refresh_agent.py -v`
Expected: PASS（7 passed）

- [ ] **Step 5: 确认 agent 缺 secret 时能明确报错**

Run: `uv run python refresh_agent.py`（不设 `REFRESH_AGENT_SECRET`）
Expected: 退出码 2，stderr 打印「缺少 REFRESH_AGENT_SECRET 环境变量」

- [ ] **Step 6: 提交**

```bash
git add refresh_agent.py tests/test_refresh_agent.py
git commit -m "feat(agent): 本机刷新 agent（轮询领任务 + 跑流水线 + 回报）"
```

---
---

## Task 4: 前端刷新按钮（api.js + decide.js + decide.html）

**Files:**
- Modify: `static/js/api.js`（在 `fetchDecideConfig` 之后追加）
- Modify: `static/js/decide.js`（import 区 + 新增刷新逻辑块）
- Modify: `templates/decide.html`（按钮元素）
- Test: `tests/test_refresh_button_static.py`（新建）

**背景：** 按钮放在 `/decide` 页，因为该页已有登录态（`AUTHED` / `renderAuthBar()`）。未登录时**不显示**（与既有 `write-control` 隐藏机制一致）。

**设计要点：**

- 用**递归 `setTimeout`** 轮询，不用 `setInterval`（避免请求堆积）
- 页面隐藏时暂停轮询（`document.visibilitychange`）
- 登出时清理定时器
- 静态站（`isStatic()`）下按钮不渲染 —— 静态站没有 Worker API

- [ ] **Step 1: 写失败测试**

新建 `tests/test_refresh_button_static.py`：

```python
"""刷新按钮的前端契约：登录才显示、静态站不显示、轮询函数存在。"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _js(name):
    return (ROOT / "static" / "js" / name).read_text(encoding="utf-8")


def test_api_exposes_refresh_helpers():
    """api.js 必须导出两个函数，字段名与 Worker 约定一致。"""
    src = _js("api.js")
    assert "export function fetchRefresh()" in src
    assert "export function fetchRefreshStatus(" in src
    assert "/api/refresh/status" in src


def test_decide_js_has_refresh_button_wiring():
    """decide.js 必须把按钮绑到 fetchRefresh，并轮询状态。"""
    src = _js("decide.js")
    assert "fetchRefresh" in src
    assert "fetchRefreshStatus" in src
    assert "btn-refresh-data" in src


def test_decide_js_uses_recursive_settimeout_not_setinterval():
    """轮询必须用 setTimeout 递归，避免 setInterval 堆积请求。"""
    src = _js("decide.js")
    assert "setTimeout" in src
    assert "setInterval" not in src, "轮询不要用 setInterval（请求会堆积）"


def test_decide_js_clears_timer_on_visibility_change():
    """页面隐藏时应暂停轮询。"""
    src = _js("decide.js")
    assert "visibilitychange" in src


def test_template_has_button_and_status_container():
    """decide.html 必须有按钮与状态容器，且按钮带 write-control 类（静态站会隐藏）。"""
    html = (ROOT / "templates" / "decide.html").read_text(encoding="utf-8")
    assert 'id="btn-refresh-data"' in html
    assert 'id="refresh-status"' in html
    assert "write-control" in html.split('id="btn-refresh-data"')[1][:200]


def test_static_export_hides_refresh_button():
    """静态站导出时不能出现刷新按钮（静态站没有 Worker API）。"""
    from export_json import _render_static_page

    html = _render_static_page("decide.html", today="2026-11-03", year=2026)
    assert "btn-refresh-data" not in html
```

> **注意**：`_render_static_page("decide.html", ...)` 的参数可能与实际不符 —— 实施时先看 `export_json.py` 里 `_render_static_page` 的调用方式（`_export_decide_data` 附近），按实际签名调整。若 `decide.html` 的静态渲染需要额外 context，先读那段代码再改测试。

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_refresh_button_static.py -v`
Expected: FAIL —— 至少 4 个 failed（函数与按钮都还不存在）

- [ ] **Step 3: 加 api.js 的两个函数**

在 `static/js/api.js` 的 `fetchDecideConfig()` 之后追加：

```javascript
// ---------------------------------------------------------------------------
// 刷新数据（本机 agent 跑流水线，Worker 只做排队与状态）
// ---------------------------------------------------------------------------

export function fetchRefresh() {
  return reqJSON(API_BASE + '/api/refresh', { method: 'POST' });
}

export function fetchRefreshStatus(jobId) {
  return reqJSON(API_BASE + '/api/refresh/status?job_id=' + encodeURIComponent(jobId));
}
```

> `reqJSON` 已经带了 `credentials: 'include'`，登录 cookie 会自动带上。

- [ ] **Step 4: 加 decide.html 的按钮与容器**

在 `templates/decide.html` 的 `<div id="decide-auth" class="mb-3"></div>` **后面**插入：

```html
  <div id="refresh-status" class="mb-3"></div>
```

并把按钮加到顶部标题行（`ms-auto d-flex align-items-center gap-2` 那个 div 里，`decide-refresh` 之后）：

```html
      <button id="btn-refresh-data" class="btn btn-sm btn-outline-primary write-control d-none"
              title="触发一次完整的数据刷新（同步行情 → 选股 → 计划 → 发布），通常需要 10~30 分钟">
        刷新数据
      </button>
```

> 默认带 `d-none`（隐藏）。登录后由 JS 移除该类。这样未登录时按钮**根本不在 DOM 里**，比 CSS 隐藏更彻底。

- [ ] **Step 5: 加 decide.js 的刷新逻辑**

在 `static/js/decide.js` 的 import 区加上：

```javascript
  fetchRefresh, fetchRefreshStatus,
```

在「登录态」小节之后新增：

```javascript
// ---------------------------------------------------------------------------
// 刷新数据
// ---------------------------------------------------------------------------

const REFRESH_POLL_MS = 4000;      // 轮询间隔
let REFRESH_TIMER = null;          // 轮询定时器（递归 setTimeout）
let REFRESH_JOB_ID = null;         // 当前任务 id

/** 显示刷新状态。kind: 'idle' | 'running' | 'ok' | 'err' */
function renderRefreshState(kind, text) {
  const el = $('refresh-status');
  if (!el) return;
  if (kind === 'idle') { el.innerHTML = ''; return; }
  const cls = kind === 'ok' ? 'alert-success'
    : kind === 'err' ? 'alert-danger'
    : 'alert-info';
  el.innerHTML = '<div class="alert ' + cls + ' py-2 mb-0 small">' + esc(text) + '</div>';
}

/** 登录态变化时显示/隐藏刷新按钮（未登录根本不该有它）。 */
function renderRefreshButton() {
  const btn = $('btn-refresh-data');
  if (!btn) return;
  if (AUTHED && !isStatic()) {
    btn.classList.remove('d-none');
  } else {
    btn.classList.add('d-none');
  }
}

/** 停止轮询并清掉定时器。 */
function stopRefreshPolling() {
  if (REFRESH_TIMER) {
    clearTimeout(REFRESH_TIMER);
    REFRESH_TIMER = null;
  }
  REFRESH_JOB_ID = null;
}

/** 递归 setTimeout 轮询任务状态（不用 setInterval，避免请求堆积）。 */
function pollRefreshStatus(jobId) {
  stopRefreshPollingIfDone();
  fetchRefreshStatus(jobId).then(st => {
    if (st.status === 'success') {
      renderRefreshState('ok', '刷新完成：' + (st.message || ('最新数据 ' + st.latest_date)));
      setRefreshButtonEnabled(true);
      return;
    }
    if (st.status === 'failed') {
      renderRefreshState('err', '刷新失败：' + (st.message || '未知原因'));
      setRefreshButtonEnabled(true);
      return;
    }
    // pending / running：继续轮询
    renderRefreshState('running', st.message || '刷新进行中…（通常 10~30 分钟）');
    REFRESH_TIMER = setTimeout(() => pollRefreshStatus(jobId), REFRESH_POLL_MS);
  }).catch(err => {
    renderRefreshState('err', '无法查询刷新状态：' + (err && err.message ? err.message : err));
    setRefreshButtonEnabled(true);
  });
}

/** 只清定时器，不清 job id（轮询中间态时用）。 */
function stopRefreshPollingIfDone() {
  if (REFRESH_TIMER) {
    clearTimeout(REFRESH_TIMER);
    REFRESH_TIMER = null;
  }
}

function setRefreshButtonEnabled(on) {
  const btn = $('btn-refresh-data');
  if (!btn) return;
  btn.disabled = !on;
  btn.textContent = on ? '刷新数据' : '刷新中…';
}

/** 点按钮：建任务 → 开始轮询。 */
function startRefresh() {
  if (REFRESH_JOB_ID) return;           // 已有任务在跑
  setRefreshButtonEnabled(false);
  renderRefreshState('running', '正在提交刷新任务…');
  fetchRefresh().then(res => {
    REFRESH_JOB_ID = res.job_id;
    renderRefreshState('running', '任务已提交，等待本机开始（通常 10~30 分钟）…');
    REFRESH_TIMER = setTimeout(() => pollRefreshStatus(REFRESH_JOB_ID), 1500);
  }).catch(err => {
    setRefreshButtonEnabled(true);
    const msg = err && err.status === 409
      ? '已有刷新任务进行中，请等它完成'
      : (err && err.message ? err.message : '未知错误');
    renderRefreshState('err', '无法启动刷新：' + msg);
  });
}
```

在 `renderAuthBar()` 函数**末尾**加一行（让登录态变化时按钮跟着显示/隐藏）：

```javascript
  renderRefreshButton();
```

> 注意 `renderAuthBar()` 里有 `if (!el) return;` 的早退 —— `renderRefreshButton()` 要放在那个早退**之后**，否则未登录时 `el` 存在但直接 return 了。实施时确认插入位置在早退语句之后。

再在文件末尾（`DOMContentLoaded` 初始化处）绑定按钮：

```javascript
// 刷新按钮
const btnRefresh = $('btn-refresh-data');
if (btnRefresh) {
  btnRefresh.addEventListener('click', startRefresh);
}

// 页面隐藏时暂停轮询（避免无谓请求），回来后继续
document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    if (REFRESH_TIMER) { clearTimeout(REFRESH_TIMER); REFRESH_TIMER = null; }
  } else if (REFRESH_JOB_ID) {
    REFRESH_TIMER = setTimeout(() => pollRefreshStatus(REFRESH_JOB_ID), 500);
  }
});
```

> `decide.js` 里已有的 `DOMContentLoaded` 逻辑（绑 `btn-login` 等）要合并到同一个 handler 里，不要新增第二个 `DOMContentLoaded` 监听 —— 或者新增也可以（浏览器允许多个），但**同一段逻辑集中在一个 handler** 更清楚。

- [ ] **Step 6: 运行测试确认通过**

Run: `uv run pytest tests/test_refresh_button_static.py -v`
Expected: PASS（6 passed）

> 若 `test_static_export_hides_refresh_button` 因 `_render_static_page` 签名不符而报错，按 Step 1 的提示调整测试调用方式（**不要**为了通过测试而改 `export_json.py` 的行为）。

- [ ] **Step 7: 确认没有破坏既有 decide 页测试**

Run: `uv run pytest tests/test_decide_web.py tests/test_decide.py tests/test_export_static.py -v`
Expected: PASS

- [ ] **Step 8: 提交**

```bash
git add static/js/api.js static/js/decide.js templates/decide.html tests/test_refresh_button_static.py
git commit -m "feat(web): 决策台刷新按钮 + 状态轮询"
```

---
---

## Task 5: `a-finder.sh refresh-agent` 子命令

**Files:**
- Modify: `a-finder.sh`（新增 `cmd_refresh_agent` 函数 + dispatcher 分支 + help 文本）
- Test: `tests/test_refresh_agent_cmd.py`（新建）

**背景：** agent 需要一个常驻进程的启动/停止/状态管理。仓库里已有成熟先例 —— `cmd_web`（pid 文件 + `nohup` + 日志），照抄它的模式。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_refresh_agent_cmd.py`：

```python
"""a-finder.sh 的 refresh-agent 子命令契约（不真起进程，只查结构）。"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SH = ROOT / "a-finder.sh"


def _text():
    return SH.read_text(encoding="utf-8")


def test_dispatcher_has_refresh_agent_branch():
    """dispatcher 必须能分派 refresh-agent。"""
    src = _text()
    assert re.search(r"refresh-agent\)\s*shift;\s*cmd_refresh_agent", src), "dispatcher 缺 refresh-agent 分支"


def test_cmd_refresh_agent_exists():
    """必须有 cmd_refresh_agent 函数，且支持 start/stop/status。"""
    src = _text()
    assert "cmd_refresh_agent()" in src
    m = re.search(r"^cmd_refresh_agent\(\) \{(.*?)^\}", src, re.S | re.M)
    assert m, "cmd_refresh_agent 函数体找不到"
    body = m.group(1)
    for action in ("start", "stop", "status"):
        assert action in body, f"缺 {action} 分支"


def test_cmd_refresh_agent_uses_pid_file():
    """必须用 pid 文件管理进程（与 cmd_web 一致）。"""
    src = _text()
    m = re.search(r"^cmd_refresh_agent\(\) \{(.*?)^\}", src, re.S | re.M)
    body = m.group(1)
    assert ".refresh-agent.pid" in body, "缺 pid 文件"
    assert "nohup" in body, "缺 nohup 后台启动"


def test_cmd_refresh_agent_passes_secret_env():
    """必须把 REFRESH_AGENT_SECRET 传给 agent 进程。"""
    src = _text()
    m = re.search(r"^cmd_refresh_agent\(\) \{(.*?)^\}", src, re.S | re.M)
    body = m.group(1)
    assert "REFRESH_AGENT_SECRET" in body, "没传 REFRESH_AGENT_SECRET"


def test_help_mentions_refresh_agent():
    """help 里要能查到怎么用。"""
    src = _text()
    assert "refresh-agent" in src
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_refresh_agent_cmd.py -v`
Expected: FAIL —— 5 failed

- [ ] **Step 3: 加 `cmd_refresh_agent` 函数**

在 `a-finder.sh` 的 `cmd_web()` 函数**之后**插入：

```bash
cmd_refresh_agent() {
  # 用法: a-finder.sh refresh-agent {start|stop|status}
  # 本机常驻 agent：轮询 Worker 的刷新任务队列，领到就跑 site --push。
  # 仿 cmd_web 的模式（pid 文件 + nohup + 日志）。
  local pid_file="$ROOT_DIR/.refresh-agent.pid"
  local log_file="$ROOT_DIR/logs/refresh-agent.log"
  local py
  if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    py="$ROOT_DIR/.venv/bin/python"
  else
    py="python3"
  fi
  local cmd="${1:-start}"
  case "$cmd" in
    start)
      local cur; cur="$(cat "$pid_file" 2>/dev/null || true)"
      if [[ -n "$cur" ]] && kill -0 "$cur" 2>/dev/null; then
        echo "already running (pid $cur)"; exit 0
      fi
      if [[ -z "${REFRESH_AGENT_SECRET:-}" ]]; then
        echo "ERROR: 请先设置 REFRESH_AGENT_SECRET（要与 Worker 的同名 secret 一致）" >&2
        echo "   例：export REFRESH_AGENT_SECRET=xxxx" >&2
        exit 1
      fi
      mkdir -p "$ROOT_DIR/logs"
      nohup "$py" "$ROOT_DIR/refresh_agent.py" >>"$log_file" 2>&1 &
      echo $! >"$pid_file"
      echo "started (pid $(cat "$pid_file"))  日志: $log_file"
      ;;
    stop)
      local cur; cur="$(cat "$pid_file" 2>/dev/null || true)"
      if [[ -n "$cur" ]] && kill -0 "$cur" 2>/dev/null; then
        kill "$cur"; echo "stopped"; else echo "not running"; fi
      rm -f "$pid_file"
      ;;
    status)
      local cur; cur="$(cat "$pid_file" 2>/dev/null || true)"
      if [[ -n "$cur" ]] && kill -0 "$cur" 2>/dev/null; then
        echo "running (pid $cur)"; else echo "stopped"; fi
      ;;
    *) echo "usage: a-finder.sh refresh-agent {start|stop|status}"; exit 1 ;;
  esac
}
```

> `REFRESH_AGENT_SECRET` 通过环境继承传给子进程（`nohup "$py" ...` 会继承当前 shell 的环境），所以不需要在这里显式 export。

- [ ] **Step 4: 加 dispatcher 分支与 help 文本**

在 dispatcher 的 `web)` 那行**之后**插入：

```bash
  refresh-agent)  shift; cmd_refresh_agent "$@" ;;
```

在 `print_help()` 里加一行（找到 `web)` 或 `fallback)` 那段说明文字附近）：

```bash
  refresh-agent)   本机刷新 agent：{start|stop|status}（需 REFRESH_AGENT_SECRET）
```

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/test_refresh_agent_cmd.py -v`
Expected: PASS（5 passed）

- [ ] **Step 6: 确认 bash 语法正确**

Run: `bash -n a-finder.sh`
Expected: 无输出（退出码 0）

- [ ] **Step 7: 确认子命令能响应（不真起进程）**

Run: `bash a-finder.sh refresh-agent status`
Expected: `stopped`（没起过）

Run: `bash a-finder.sh refresh-agent`（不设 secret）
Expected: 退出码 1，stderr 提示「请先设置 REFRESH_AGENT_SECRET」

- [ ] **Step 8: 提交**

```bash
git add a-finder.sh tests/test_refresh_agent_cmd.py
git commit -m "feat(cli): refresh-agent 子命令（start/stop/status）"
```

---
---

## Task 6: CI 退化为「只维护缓存库，不发布」

**Files:**
- Modify: `.github/workflows/deploy-site.yml`（删除 `peaceiris/actions-gh-pages` 步骤）
- Test: `tests/test_ci_no_publish.py`（新建）

**背景：** 本机成为 gh-pages 的**唯一日常写者** → 双写分叉从根上消失。CI 仍保留交易日判定、同步、选股/计划计算、缓存保存 —— **缓存谱系继续推进**（那是 `CLAUDE.md` 里最贵的一块资产，4 个 workflow 共享）。

**⚠️ 只删发布那一步，不动其他逻辑。** 尤其不要动 `freshness`（`skip_sync` / `site_stale`）、`sync`、缓存保存的条件 —— 那些是 2026-10-29 事故后反复调出来的。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_ci_no_publish.py`：

```python
"""deploy-site workflow 不再发布到 gh-pages（本机是唯一发布者）。

同时守住「不能顺手删掉缓存保存」—— 那是 4 个 workflow 共享的谱系根，
删了会让 CI 的行情谱系冻结（2026-10-29 事故的根因之一）。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WF = ROOT / ".github" / "workflows" / "deploy-site.yml"


def _text():
    return WF.read_text(encoding="utf-8")


def test_deploy_site_does_not_publish_to_gh_pages():
    """deploy-site 不得再推 gh-pages（本机是唯一日常写者）。"""
    src = _text()
    assert "peaceiris/actions-gh-pages" not in src, (
        "deploy-site 还在推 gh-pages —— 会与本机双写，造成分叉"
    )


def test_deploy_site_still_saves_cache():
    """缓存保存必须保留 —— 删了会让 CI 行情谱系冻结。"""
    src = _text()
    assert "actions/cache@v4" in src, "缓存保存被误删"
    assert "hs300-db-" in src, "缓存 key 规则被误改"


def test_deploy_site_keeps_freshness_gate():
    """freshness 判据（skip_sync / site_stale）必须保留。"""
    src = _text()
    assert "db_freshness.py" in src, "freshness 判据被误删"
    assert "skip_sync" in src
    assert "site_stale" in src


def test_deploy_site_keeps_sync_step():
    """同步步骤必须保留 —— CI 仍需维护自己的缓存库。"""
    src = _text()
    assert "sync-incremental" in src or "sync-hs300-range" in src


def test_deploy_site_run_blocks_pass_bash_syntax_check():
    """每个 run 块都要过 `bash -n`（缩进 0 会让块标量截断整个文件）。

    **不用 YAML 解析器** —— venv 里没有 PyYAML（`pyproject.toml` 里也没这个依赖）。
    照 `tests/test_daily_audit_workflow.py` 的既有做法：按缩进切 step 文本块。
    """
    import subprocess

    from test_daily_audit_workflow import _step_block, _step_names, _run_script

    text = _text()
    checked = 0
    for name in _step_names(text):
        script = _run_script(_step_block(text, name))
        if not script.strip():
            continue
        checked += 1
        proc = subprocess.run(["bash", "-n"], input=script,
                              capture_output=True, text=True)
        assert proc.returncode == 0, (name, proc.stderr)
    assert checked >= 5, "应该有不少于 5 个带脚本的步骤"


def test_other_workflows_still_publish():
    """daily-audit 仍会发布（Q9=A：极低频，只在修复缺口时）。"""
    audit = (ROOT / ".github" / "workflows" / "daily-audit.yml").read_text(encoding="utf-8")
    assert "peaceiris/actions-gh-pages" in audit, (
        "daily-audit 的发布被误删 —— 设计上它保留发布权"
    )
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_ci_no_publish.py -v`
Expected: FAIL —— `test_deploy_site_does_not_publish_to_gh_pages` 失败（其余应通过）

- [ ] **Step 3: 删掉发布步骤**

在 `.github/workflows/deploy-site.yml` 里删除整个 `- name: 部署 gh-pages` 步骤块（从 `- name: 部署 gh-pages` 到该步骤结束，即 `uses: peaceiris/actions-gh-pages@v4` 及其 `with:` 子块）。

**替换成**（保留说明为什么不再发布）：

```yaml
      # 本机（refresh_agent + a-finder.sh site --push）是 gh-pages 的**唯一日常写者**。
      # 这里不再发布：
      #   - 8 班/天的 CI 发布 + 手动发布 = 双写分叉（2026-10-29 事故的根因：
      #     本地先把当天发上去 → CI 判 skip → CI 库再也不推进）
      #   - 删掉发布后，CI 的缓存库仍在推进（同步 + 计算 + 缓存保存都保留），
      #     只是不再对外发布；线上由本机负责。
      # 手动兜底仍可：workflow_dispatch 本 workflow 时，它会同步+计算但**不发布**；
      #   真的要发布请用本机：bash a-finder.sh site --push
      - name: 汇总发布状态（本 workflow 不发布）
        if: always() && steps.trade-day.outputs.is_trade_day == 'true'
        run: |
          echo "::notice::本 workflow 不再推 gh-pages（避免与本机双写）；线上由本机发布"
```

> ⚠️ **YAML 块标量陷阱**（`CLAUDE.md` 明确记载）：`run: |` 块里的每行必须缩进到与 `run:` 同级或更深。上面 `run: |` 下的 `echo` 行缩进 10 空格，而 `run:` 本身在 8 空格 —— 这是**对的**（块内容比 `run:` 深 2 格）。改完必须验 YAML（Step 5）。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_ci_no_publish.py -v`
Expected: PASS（6 passed）

- [ ] **Step 5: 独立验 bash 语法**

**不要用 YAML 解析器** —— 本仓库的 venv 里没有 PyYAML（`pyproject.toml` 也没这个依赖），
`import yaml` 会直接 `ModuleNotFoundError`。仓库既有做法是按缩进切 step 文本块
（见 `tests/test_daily_audit_workflow.py`），Task 6 的测试已经照此写了。

Run: `.venv/bin/python -m pytest tests/test_ci_no_publish.py -v`
Expected: PASS（6 passed，含 bash -n 逐块检查）

若确实想验 YAML 整体合法性（可选，CI 上会用 actionlint），本地可以：

```bash
npx --yes yaml-lint .github/workflows/deploy-site.yml
```

或用 node（若本地装了 js-yaml）：

```bash
node -e "require('js-yaml').load(require('fs').readFileSync('.github/workflows/deploy-site.yml','utf8')); console.log('YAML OK')"
```

- [ ] **Step 6: 提交**

```bash
git add .github/workflows/deploy-site.yml tests/test_ci_no_publish.py
git commit -m "ci: deploy-site 不再发布（本机唯一发布者），保留同步与缓存维护"
```

---
---

## Task 7: 端到端联调与文档

**Files:**
- Modify: `CLAUDE.md`（新增「刷新按钮」一节）
- Test: 人工端到端验证

**背景：** 前 6 个任务都是「单元可测」，但**整条链路**（Worker → agent → 本机 → gh-pages → Cloudflare → 页面）需要人工验证一次。

- [ ] **Step 1: 先取全量测试基线**

Run: `uv run pytest tests/ -q 2>&1 | tail -20`
Expected: 记录改动前的失败清单（仓库可能有已知的历史失败）。**这个基线是判断「新失败」的依据。**

- [ ] **Step 2: 配置 secret 并部署 Worker**

> ⚠️ **新增发现（Task 1 实施时确认）**：`.github/workflows/deploy-worker.yml` 目前只同步
> `DECIDE_USER` / `DECIDE_PASSWORD` / `DECIDE_SECRET` / `GITHUB_DISPATCH_TOKEN`，
> **没有同步 `REFRESH_AGENT_SECRET`** ⇒ 走 CI 部署的线上 Worker 拿不到这个 secret，
> agent 端点会一律 401。**必须在本步补上 deploy-worker.yml 的同步步骤**（见下方）。

先在 `.github/workflows/deploy-worker.yml` 里，紧跟「同步 GITHUB_DISPATCH_TOKEN（可选）」步骤之后，加入：

```yaml
      - name: 同步 REFRESH_AGENT_SECRET（刷新按钮的 agent 通道）
        if: ${{ secrets.REFRESH_AGENT_SECRET != '' }}
        env:
          REFRESH_AGENT_SECRET: ${{ secrets.REFRESH_AGENT_SECRET }}
        run: |
          if [ -z "$REFRESH_AGENT_SECRET" ]; then
            echo "::notice::未配置 REFRESH_AGENT_SECRET，刷新按钮的 agent 通道不通（前端按钮会一直 pending）"
          else
            printf '%s' "$REFRESH_AGENT_SECRET" | npx --yes wrangler@latest secret put REFRESH_AGENT_SECRET
            echo "已同步 REFRESH_AGENT_SECRET"
          fi
```

再配置并部署：

```bash
# 1. 生成 secret（本机 agent 要用同一个值）
export REFRESH_AGENT_SECRET="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"
echo "$REFRESH_AGENT_SECRET"

# 2. 配到 Worker（会提示输入，粘贴上面的值）
npx --yes wrangler@latest secret put REFRESH_AGENT_SECRET

# 3. 建表 + 部署 Worker（含新端点）
npx --yes wrangler@latest d1 execute a-finder --file=worker/schema.sql --remote --yes
npx --yes wrangler@latest deploy
```

Expected: deploy 成功；`curl https://ycu.space/api/decide/health` 仍返回 200。

- [ ] **Step 3: 启动本机 agent**

```bash
export REFRESH_AGENT_SECRET='<Step 2 的值>'
bash a-finder.sh refresh-agent start
bash a-finder.sh refresh-agent status
```

Expected: `started (pid ...)` 然后 `running (pid ...)`

- [ ] **Step 4: 登录并点按钮（人工）**

1. 打开 `https://ycu.space/decide`
2. 用 `DECIDE_USER` / `DECIDE_PASSWORD` 登录
3. 确认「刷新数据」按钮**出现**（未登录时不应出现）
4. 点按钮
5. 确认页面出现「任务已提交，等待本机开始…」

**如果按钮没出现**：查浏览器控制台 —— 多半是 `renderRefreshButton()` 插在了 `renderAuthBar()` 的早退**之前**（早退后代码不执行）。修好位置再试。

- [ ] **Step 5: 观察任务状态流转**

```bash
# 看 agent 是否领到任务
npx --yes wrangler@latest d1 execute a-finder --remote --command \
  "SELECT job_id, status, started_at FROM refresh_jobs ORDER BY created_at DESC LIMIT 1"

# 看本机流水线日志
tail -f logs/refresh.log
```

Expected:
- 状态从 `pending` → `running`
- `logs/refresh.log` 出现 `=== refresh 开始 ... ===`
- 页面轮询显示「正在同步行情…」
- 15~35 分钟后状态变 `success`，页面显示「刷新完成：最新数据 2026-11-03」

- [ ] **Step 6: 验证站点真的更新了**

```bash
curl -s https://ycu.space/data/dates.json | python3 -c "import json,sys;print(json.load(sys.stdin)['dates'][0])"
```

Expected: 新日期（= 库里 `MAX(daily_prices.trade_date)`）

> ⚠️ Cloudflare 同步 Static Assets 有延迟（通常 1~3 分钟）。刚 push 完立刻查可能还是旧日期 —— 等几分钟再查。

- [ ] **Step 7: 验证单调性守卫真的会拦**

**不要**真的去破坏线上。用 dry-run 方式验证：

```bash
python3 scripts/verify_site_export.py --site site --not-older-than 2099-01-01; echo "退出码: $?"
```

Expected: 退出码 1，stderr 打印「产物最新日 … 比线上最新日 … 更旧」

- [ ] **Step 8: 更新 CLAUDE.md**

在 `CLAUDE.md` 的「CI 自动部署（deploy-site.yml）」一节**之后**新增：

```markdown
# 刷新按钮（本机唯一发布者，2026-11-03）
- 决策台（`/decide`）登录后有「刷新数据」按钮 → Worker `POST /api/refresh`
  → D1 `refresh_jobs` 排队 → 本机 `refresh_agent.py` 轮询领任务 →
  `bash a-finder.sh site --push` → push gh-pages → Cloudflare 同步 Assets。
- **触发方向是拉模式**：Worker 在边缘够不到本机 `127.0.0.1`，由本机轮询
  `/api/refresh/next` 领任务（`REFRESH_AGENT_SECRET` 头认证）。
- **gh-pages 的唯一日常写者是本机**：`deploy-site.yml` 已删掉
  `peaceiris/actions-gh-pages` 步骤（CI 仍同步+计算+维护缓存谱系，但不发布）。
  `daily-audit.yml` / `gap-heal.yml` 仍保留发布权（极低频，只在修复缺口时）。
- **两套认证故意分开**：前端端点用登录 cookie（`decide_auth`），
  agent 端点用独立的 `REFRESH_AGENT_SECRET` 头。共用会让一个泄露同时丢掉两者。
- **发布前有单调性守卫**：`cmd_site` 用 `verify_site_export.py --not-older-than`
  比对线上最新日，比线上旧就**不发布**（2026-10-29 事故的直接教训）。
- **`cmd_site` 的计划日期对齐 `MAX(daily_prices.trade_date)`**（不是今天）——
  同步失败时按今天生成计划会产出空计划。CI 早已这么修，本机这次跟上。
- 运维：`bash a-finder.sh refresh-agent {start|stop|status}`，
  日志 `logs/refresh.log`（流水线）与 `logs/refresh-agent.log`（agent 自身）。
- 耗时 15~35 分钟（行情同步 + 历史回补）。按钮会轮询显示阶段。
```

- [ ] **Step 9: 跑全量回归并对比基线**

Run: `uv run pytest tests/ -q 2>&1 | tail -20`
Expected: 与 Step 1 的基线**相比没有新增失败**

- [ ] **Step 10: 提交**

```bash
git add CLAUDE.md
git commit -m "docs: 刷新按钮链路说明（CLAUDE.md）"
```

---

## 附：新增 secret 清单

| secret | 在哪用 | 怎么配 |
|---|---|---|
| `REFRESH_AGENT_SECRET` | Worker 的 `/api/refresh/next`、`/api/refresh/result`；本机 agent 的请求头 | `wrangler secret put REFRESH_AGENT_SECRET` + 本机 `export` 同值 |

**已有的 secret 不用动**：`DECIDE_USER` / `DECIDE_PASSWORD` / `DECIDE_SECRET`（登录）、`GITHUB_DISPATCH_TOKEN`（cron dispatch）。

---

## 附：排障速查

| 现象 | 大概率原因 | 查什么 |
|---|---|---|
| 按钮不出现 | `renderRefreshButton()` 插在 `renderAuthBar()` 早退之前 | 浏览器控制台 + `static/js/decide.js` |
| 点按钮报 401 | cookie 没带上 | `/api/decide/health` 返回 `authed` 是否为 true |
| 点按钮报 409 | 已有任务在跑（D1 里还有 pending/running） | `SELECT * FROM refresh_jobs` |
| 任务一直 pending | agent 没起 / secret 不一致 | `bash a-finder.sh refresh-agent status` + `logs/refresh-agent.log` |
| 任务一直 running | 流水线卡住 | `logs/refresh.log` 尾部 |
| 任务 failed 但没原因 | agent 回报时 message 为空 | `logs/refresh.log` + D1 的 `message` 字段 |
| 线上日期没变 | Cloudflare 同步延迟（1~3 分钟），或单调性守卫拦了 | 先等几分钟；再查 `logs/refresh.log` 有没有「已阻止发布」 |
