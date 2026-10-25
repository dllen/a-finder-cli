# Cloudflare Worker + D1 线上可写持仓 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 Cloudflare Worker + D1 让 ycu.space 上可直接录入/编辑真实持仓，D1 成为持仓唯一真相源。

**Architecture:** D1 只存 2 张持仓表；读继续走静态 JSON，写走 Worker（密码+Cookie 认证）。对比计算从服务端移到前端 JS（`decide_compare.js`），配置从 `data/decide-config.json` 读，保证与 Python 版规则同源。

**Tech Stack:** Cloudflare Workers (ESM), D1, Web Crypto API, Vanilla ES6 modules, wrangler

**Spec:** `docs/superpowers/specs/2026-10-25-cloudflare-worker-d1-design.md`

---

### Task 1: Worker 骨架 + 认证模块

**Files:**
- Create: `wrangler.toml`
- Create: `worker/schema.sql`
- Create: `worker/auth.js`
- Create: `worker/index.js`
- Create: `tests/test_worker_auth.mjs`
- Modify: `.gitignore`（排除 `.dev.vars`）

- [ ] **Step 1: 写 auth 的失败测试**

创建 `tests/test_worker_auth.mjs`：

```javascript
// Worker 认证模块单测（node --test）
import { test } from 'node:test';
import assert from 'node:assert';
import { signToken, verifyToken, parseCookie, buildCookie } from '../worker/auth.js';

const SECRET = 'test-secret-do-not-use';

test('signToken + verifyToken 往返成功', async () => {
  const token = await signToken({ exp: Date.now() + 60000 }, SECRET);
  const payload = await verifyToken(token, SECRET);
  assert.ok(payload, '应验签通过');
  assert.ok(payload.exp > Date.now());
});

test('过期 token 被拒绝', async () => {
  const token = await signToken({ exp: Date.now() - 1000 }, SECRET);
  assert.equal(await verifyToken(token, SECRET), null);
});

test('篡改 token 被拒绝', async () => {
  const token = await signToken({ exp: Date.now() + 60000 }, SECRET);
  const tampered = token.slice(0, -2) + 'xx';
  assert.equal(await verifyToken(tampered, SECRET), null);
});

test('错误密钥被拒绝', async () => {
  const token = await signToken({ exp: Date.now() + 60000 }, SECRET);
  assert.equal(await verifyToken(token, 'wrong-secret'), null);
});

test('parseCookie 提取指定 cookie', () => {
  assert.equal(parseCookie('a=1; decide_auth=abc.def; b=2', 'decide_auth'), 'abc.def');
  assert.equal(parseCookie('a=1', 'decide_auth'), null);
  assert.equal(parseCookie(null, 'decide_auth'), null);
});

test('buildCookie 含安全属性', () => {
  const c = buildCookie('tok', 604800);
  assert.ok(c.includes('HttpOnly'));
  assert.ok(c.includes('Secure'));
  assert.ok(c.includes('SameSite=Strict'));
  assert.ok(c.includes('Max-Age=604800'));
});
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /Users/shichaopeng/Work/self-dir/projects/a-finder-cli
node --test tests/test_worker_auth.mjs 2>&1 | tail -8
```

Expected: FAIL — `Cannot find module '../worker/auth.js'`

- [ ] **Step 3: 实现 worker/auth.js**

创建 `worker/auth.js`：

```javascript
// HMAC-SHA256 签名 token + Cookie 工具。Workers 原生 Web Crypto。
const enc = new TextEncoder();

function b64url(buf) {
  const bytes = new Uint8Array(buf);
  let s = '';
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function b64urlDecode(str) {
  const pad = str.length % 4 === 0 ? '' : '='.repeat(4 - (str.length % 4));
  const s = atob(str.replace(/-/g, '+').replace(/_/g, '/') + pad);
  const bytes = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) bytes[i] = s.charCodeAt(i);
  return bytes;
}

async function hmacKey(secret) {
  return crypto.subtle.importKey(
    'raw', enc.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' }, false, ['sign', 'verify'],
  );
}

export async function signToken(payload, secret) {
  const body = b64url(enc.encode(JSON.stringify(payload)));
  const key = await hmacKey(secret);
  const sig = await crypto.subtle.sign('HMAC', key, enc.encode(body));
  return `${body}.${b64url(sig)}`;
}

export async function verifyToken(token, secret) {
  if (!token || typeof token !== 'string') return null;
  const parts = token.split('.');
  if (parts.length !== 2) return null;
  const [body, sig] = parts;
  try {
    const key = await hmacKey(secret);
    const ok = await crypto.subtle.verify(
      'HMAC', key, b64urlDecode(sig), enc.encode(body),
    );
    if (!ok) return null;
    const payload = JSON.parse(new TextDecoder().decode(b64urlDecode(body)));
    if (!payload || typeof payload.exp !== 'number' || payload.exp < Date.now()) {
      return null;
    }
    return payload;
  } catch {
    return null;
  }
}

export function parseCookie(header, name) {
  if (!header) return null;
  for (const part of header.split(';')) {
    const idx = part.indexOf('=');
    if (idx < 0) continue;
    if (part.slice(0, idx).trim() === name) return part.slice(idx + 1).trim();
  }
  return null;
}

export function buildCookie(value, maxAge) {
  return `decide_auth=${value}; HttpOnly; Secure; SameSite=Strict; ` +
    `Path=/; Max-Age=${maxAge}`;
}

export function clearCookie() {
  return 'decide_auth=; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=0';
}
```

- [ ] **Step 4: 跑测试确认通过**

```bash
node --test tests/test_worker_auth.mjs 2>&1 | tail -10
```

Expected: PASS (6 tests)

- [ ] **Step 5: 创建 schema.sql**

创建 `worker/schema.sql`（与 `db/migrations/2026_10_25_real_positions.sql` 完全一致）：

```sql
-- D1 schema：持仓唯一真相源。与 db/migrations/2026_10_25_real_positions.sql 一致。
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

- [ ] **Step 6: 创建 wrangler.toml**

创建 `wrangler.toml`：

```toml
name = "a-finder-api"
main = "worker/index.js"
compatibility_date = "2026-10-25"

[[d1_databases]]
binding = "DB"
database_name = "a-finder"
# 运行 `npx wrangler d1 create a-finder` 后，把返回的 database_id 填到这里
database_id = "REPLACE_WITH_D1_DATABASE_ID"

routes = [
  { pattern = "ycu.space/api/*", zone_name = "ycu.space" }
]
```

- [ ] **Step 7: 创建 worker/index.js 骨架（仅 health）**

创建 `worker/index.js`：

```javascript
// Cloudflare Worker：10w 决策台持仓的线上读写。
// 读公开（首屏渲染需要），写需密码登录后的 HttpOnly Cookie。
import { buildCookie, clearCookie, parseCookie, signToken, verifyToken } from './auth.js';

const COOKIE_NAME = 'decide_auth';
const MAX_AGE = 7 * 24 * 3600;           // 7 天（秒）
const ALLOWED_ORIGINS = [
  'https://ycu.space',
  'https://dllen.github.io',
  'http://127.0.0.1:8000',
  'http://localhost:8000',
];

function corsHeaders(origin) {
  const ok = ALLOWED_ORIGINS.includes(origin);
  const h = {
    'Access-Control-Allow-Methods': 'GET, POST, DELETE, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Access-Control-Max-Age': '86400',
  };
  if (ok) {
    h['Access-Control-Allow-Origin'] = origin;
    h['Access-Control-Allow-Credentials'] = 'true';
  }
  return h;
}

function json(data, status, origin) {
  return new Response(JSON.stringify(data), {
    status: status || 200,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      ...corsHeaders(origin),
    },
  });
}

async function isAuthed(request, env) {
  const token = parseCookie(request.headers.get('Cookie'), COOKIE_NAME);
  if (!token) return false;
  return (await verifyToken(token, env.DECIDE_SECRET)) !== null;
}

export default {
  async fetch(request, env) {
    const origin = request.headers.get('Origin') || '';
    const url = new URL(request.url);
    const path = url.pathname;

    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: corsHeaders(origin) });
    }

    if (path === '/api/decide/health' && request.method === 'GET') {
      let d1 = 'ok';
      try {
        await env.DB.prepare('SELECT 1').first();
      } catch (e) {
        d1 = 'error: ' + (e && e.message ? e.message : e);
      }
      return json({ ok: d1 === 'ok', d1 }, 200, origin);
    }

    return json({ error: 'not found' }, 404, origin);
  },
};
```

- [ ] **Step 8: 语法检查 + 补 .gitignore**

```bash
cd /Users/shichaopeng/Work/self-dir/projects/a-finder-cli
node --check worker/index.js && node --check worker/auth.js && echo "语法 OK"
grep -q '.dev.vars' .gitignore || printf '\n# Cloudflare\n.dev.vars\n.wrangler/\n' >> .gitignore
tail -4 .gitignore
```

Expected: `语法 OK`，且 `.gitignore` 含 `.dev.vars`

- [ ] **Step 9: 本地起 Worker 验证 health**

```bash
cd /Users/shichaopeng/Work/self-dir/projects/a-finder-cli
npx --yes wrangler@latest dev --local --port 8788 --test-scheduled 2>&1 &
sleep 25
curl -s http://127.0.0.1:8788/api/decide/health; echo
```

Expected: `{"ok":true,"d1":"ok"}`（若 D1 未创建则 `d1:"error:..."`，可接受——本地 dev 会用 miniflare 建本地 D1；若报错先跑 `npx wrangler d1 execute a-finder --file=worker/schema.sql --local`）

> 若 `wrangler dev` 因未登录 Cloudflare 无法启动，跳过本步并在 Task 5 部署后验证；此步不阻塞后续。

- [ ] **Step 10: 提交**

```bash
git add wrangler.toml worker/ tests/test_worker_auth.mjs .gitignore
git commit -m "feat(worker): Worker 骨架 + HMAC 认证 + D1 schema"
```

---

### Task 2: Worker 持仓 CRUD + 登录路由

**Files:**
- Modify: `worker/index.js`

- [ ] **Step 1: 扩展 worker/index.js**

把 `worker/index.js` 的 `fetch` 函数改为完整路由（保留已有的 health 与 CORS 代码）：

```javascript
export default {
  async fetch(request, env) {
    const origin = request.headers.get('Origin') || '';
    const url = new URL(request.url);
    const path = url.pathname;
    const method = request.method;

    if (method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: corsHeaders(origin) });
    }

    // ---- health ----
    if (path === '/api/decide/health' && method === 'GET') {
      let d1 = 'ok';
      try {
        await env.DB.prepare('SELECT 1').first();
      } catch (e) {
        d1 = 'error: ' + (e && e.message ? e.message : e);
      }
      return json({ ok: d1 === 'ok', d1, authed: await isAuthed(request, env) }, 200, origin);
    }

    // ---- login ----
    if (path === '/api/decide/login' && method === 'POST') {
      const body = await request.json().catch(() => ({}));
      if (!env.DECIDE_PASSWORD || body.password !== env.DECIDE_PASSWORD) {
        return json({ error: '密码错误' }, 401, origin);
      }
      const token = await signToken({ exp: Date.now() + MAX_AGE * 1000 }, env.DECIDE_SECRET);
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: {
          'Content-Type': 'application/json; charset=utf-8',
          'Set-Cookie': buildCookie(token, MAX_AGE),
          ...corsHeaders(origin),
        },
      });
    }

    // ---- logout ----
    if (path === '/api/decide/logout' && method === 'POST') {
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: {
          'Content-Type': 'application/json; charset=utf-8',
          'Set-Cookie': clearCookie(),
          ...corsHeaders(origin),
        },
      });
    }

    // ---- 读持仓（公开）----
    if (path === '/api/decide/positions' && method === 'GET') {
      const [stock, cb] = await Promise.all([
        env.DB.prepare('SELECT * FROM real_positions ORDER BY code').all(),
        env.DB.prepare('SELECT * FROM real_cb_positions ORDER BY code').all(),
      ]);
      return json({ rows: stock.results || [], cb_rows: cb.results || [] }, 200, origin);
    }

    // ---- 写持仓（需认证）----
    if (path === '/api/decide/positions' && method === 'POST') {
      if (!(await isAuthed(request, env))) {
        return json({ error: '未登录' }, 401, origin);
      }
      const p = await request.json().catch(() => ({}));
      const code = String(p.code || '').trim();
      const shares = Number(p.shares);
      const entryPrice = Number(p.entry_price);
      if (!code) return json({ error: '缺少 code' }, 400, origin);
      if (!Number.isFinite(shares) || shares <= 0) {
        return json({ error: 'shares 必须 > 0' }, 400, origin);
      }
      if (!Number.isFinite(entryPrice) || entryPrice <= 0) {
        return json({ error: 'entry_price 必须 > 0' }, 400, origin);
      }
      const table = p.is_cb ? 'real_cb_positions' : 'real_positions';
      const now = new Date().toISOString().slice(0, 19);
      await env.DB.prepare(
        `INSERT INTO ${table}
         (code, name, shares, entry_price, stop_price, entry_date, note, updated_at)
         VALUES (?,?,?,?,?,?,?,?)
         ON CONFLICT(code) DO UPDATE SET
           name=excluded.name, shares=excluded.shares,
           entry_price=excluded.entry_price, stop_price=excluded.stop_price,
           entry_date=excluded.entry_date, note=excluded.note,
           updated_at=excluded.updated_at`,
      ).bind(
        code, p.name || null, shares, entryPrice,
        p.stop_price == null || p.stop_price === '' ? null : Number(p.stop_price),
        p.entry_date || null, p.note || null, now,
      ).run();
      return json({ ok: true }, 200, origin);
    }

    // ---- 删持仓（需认证）----
    const delMatch = path.match(/^\/api\/decide\/positions\/([^/]+)$/);
    if (delMatch && method === 'DELETE') {
      if (!(await isAuthed(request, env))) {
        return json({ error: '未登录' }, 401, origin);
      }
      const code = decodeURIComponent(delMatch[1]);
      const table = url.searchParams.get('cb') === '1'
        ? 'real_cb_positions' : 'real_positions';
      await env.DB.prepare(`DELETE FROM ${table} WHERE code=?`).bind(code).run();
      return json({ ok: true }, 200, origin);
    }

    return json({ error: 'not found' }, 404, origin);
  },
};
```

- [ ] **Step 2: 语法检查**

```bash
cd /Users/shichaopeng/Work/self-dir/projects/a-finder-cli
node --check worker/index.js && echo "语法 OK"
```

Expected: `语法 OK`

- [ ] **Step 3: 本地端到端验证（若 Task 1 Step 9 能起 dev）**

```bash
cd /Users/shichaopeng/Work/self-dir/projects/a-finder-cli
npx --yes wrangler@latest d1 execute a-finder --file=worker/schema.sql --local 2>&1 | tail -3
printf 'DECIDE_PASSWORD=test123\nDECIDE_SECRET=localsecret\n' > .dev.vars
npx --yes wrangler@latest dev --local --port 8788 2>&1 &
sleep 25
echo "--- 未登录写入应 401 ---"
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8788/api/decide/positions \
  -H 'Content-Type: application/json' -d '{"code":"600519","shares":100,"entry_price":1500}'
echo "--- 登录 ---"
curl -s -c /tmp/wcookie.txt -X POST http://127.0.0.1:8788/api/decide/login \
  -H 'Content-Type: application/json' -d '{"password":"test123"}'; echo
echo "--- 登录后写入 ---"
curl -s -b /tmp/wcookie.txt -X POST http://127.0.0.1:8788/api/decide/positions \
  -H 'Content-Type: application/json' -d '{"code":"600519","shares":100,"entry_price":1500}'; echo
echo "--- 读回 ---"
curl -s http://127.0.0.1:8788/api/decide/positions; echo
echo "--- 删除 ---"
curl -s -b /tmp/wcookie.txt -X DELETE http://127.0.0.1:8788/api/decide/positions/600519; echo
```

Expected: 401 → 登录成功 → 写入 ok → 读到 600519 → 删除 ok

> 若无法启动 wrangler dev（未登录 Cloudflare），跳过此步；Task 5 部署后在线上验证。

- [ ] **Step 4: 清理本地工件**

```bash
cd /Users/shichaopeng/Work/self-dir/projects/a-finder-cli
rm -f .dev.vars /tmp/wcookie.txt
git status --short
```

Expected: 只有 `worker/index.js` 被修改（`.dev.vars` 已被 gitignore）

- [ ] **Step 5: 提交**

```bash
git add worker/index.js
git commit -m "feat(worker): 持仓 CRUD + 登录/登出路由（写需 Cookie 认证）"
```

---

### Task 3: 前端 JS 对比逻辑 + 与 Python 一致性测试

**Files:**
- Create: `static/js/decide_compare.js`
- Create: `tests/test_decide_compare_parity.py`

- [ ] **Step 1: 写一致性测试（先失败）**

创建 `tests/test_decide_compare_parity.py`：

```python
"""JS 版对比逻辑（decide_compare.js）与 Python 版（decide_service）必须一致。

用 node 跑同一组 fixture，逐项比对 deviations 的 (severity, kind, code) 序列。
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
JS_DIR = ROOT / "static" / "js"

CFG = {
    "total_capital": 100000.0,
    "stock_ratio": 0.8,
    "cb_ratio": 0.2,
    "max_positions": 8,
    "max_single_weight": 0.10,
    "max_stock_exposure": 0.75,
    "cb_capital": 20000.0,
    "max_cb_positions": 3,
}

CASES = [
    # name, plan, positions, cb, prices
    ("perfect",
     [{"code": "600519", "name": "茅台", "action": "buy", "plan_price": 100.0,
       "shares": 100, "stop_price": 95.0, "tp_price": 110.0}],
     [{"code": "600519", "name": "茅台", "shares": 100,
       "entry_price": 100.0, "stop_price": 95.0}],
     [], {"600519": 100.0}),
    ("missing_buy",
     [{"code": "600519", "name": "茅台", "action": "buy", "plan_price": 100.0,
       "shares": 100, "stop_price": 95.0, "tp_price": 110.0},
      {"code": "600036", "name": "招行", "action": "buy", "plan_price": 40.0,
       "shares": 200, "stop_price": 38.0, "tp_price": 44.0}],
     [{"code": "600519", "name": "茅台", "shares": 100,
       "entry_price": 100.0, "stop_price": 95.0}],
     [], {"600519": 100.0}),
    ("empty_portfolio",
     [{"code": "600519", "name": "茅台", "action": "buy", "plan_price": 100.0,
       "shares": 100, "stop_price": 95.0, "tp_price": 110.0}],
     [], [], {}),
    ("over_position_limit",
     [], [{"code": f"60000{i}", "name": f"股{i}", "shares": 10,
           "entry_price": 10.0, "stop_price": 9.0} for i in range(9)],
     [], {}),
    ("stop_breached",
     [{"code": "600519", "name": "茅台", "action": "buy", "plan_price": 100.0,
       "shares": 100, "stop_price": 95.0, "tp_price": 110.0}],
     [{"code": "600519", "name": "茅台", "shares": 100,
       "entry_price": 100.0, "stop_price": 95.0}],
     [], {"600519": 90.0}),
    ("cb_over",
     [], [], [{"code": "113059", "name": "转债A", "shares": 200,
              "entry_price": 110.0, "stop_price": None}], {}),
]


def _run_js(plan, positions, cb, prices):
    script = """
import { computeComparison } from './decide_compare.js';
const input = JSON.parse(process.argv[1]);  // -e 模式下 argv[1] 是首个附加参数
const out = computeComparison(input.plan, input.positions, input.cb, input.prices, input.cfg);
process.stdout.write(JSON.stringify(out));
"""
    fixture = json.dumps({
        "plan": plan, "positions": positions, "cb": cb,
        "prices": prices, "cfg": CFG,
    })
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script, fixture],
        cwd=str(JS_DIR), capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, f"node 失败: {proc.stderr}"
    return json.loads(proc.stdout)


@pytest.mark.parametrize("name,plan,positions,cb,prices", CASES)
def test_js_matches_python(name, plan, positions, cb, prices):
    from decide_service import compare_plan_vs_actual

    py = compare_plan_vs_actual(plan, positions, cb, prices, CFG)
    js = _run_js(plan, positions, cb, prices)

    py_sig = [(d["severity"], d["kind"], d["code"]) for d in py["deviations"]]
    js_sig = [(d["severity"], d["kind"], d["code"]) for d in js["deviations"]]
    assert js_sig == py_sig, f"{name}: deviations 不一致\npy={py_sig}\njs={js_sig}"

    assert js["stock_used"] == pytest.approx(py["stock_used"], abs=0.01)
    assert js["cb_used"] == pytest.approx(py["cb_used"], abs=0.01)
    assert js["cash"] == pytest.approx(py["cash"], abs=0.01)
    assert len(js["pending_build"]) == len(py["pending_build"])
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /Users/shichaopeng/Work/self-dir/projects/a-finder-cli
python3 -m pytest tests/test_decide_compare_parity.py -x -q 2>&1 | tail -8
```

Expected: FAIL — `Cannot find module './decide_compare.js'`

- [ ] **Step 3: 实现 static/js/decide_compare.js**

创建 `static/js/decide_compare.js`（纯函数，无 DOM 依赖，可被 node 导入）：

```javascript
// 计划 vs 实际的对比判定（JS 版）。
// 与 decide_service.compare_plan_vs_actual 规则逐条对齐，
// 由 tests/test_decide_compare_parity.py 保证一致性。
// 配置来自 data/decide-config.json（即 config.PORTFOLIO_10W）。

function num(v) {
  if (v === null || v === undefined || v === '') return null;
  const f = Number(v);
  return Number.isFinite(f) ? f : null;
}

function dev(severity, kind, code, name, message, actual, expected) {
  return {
    severity, kind, code, name: name || code,
    message, actual: actual === undefined ? null : actual,
    expected: expected === undefined ? null : expected,
  };
}

export function dedupePlanRows(rows) {
  const seen = new Map();
  const order = [];
  for (const r of rows || []) {
    const code = String(r.code || '').trim();
    if (!code) continue;
    if (!seen.has(code)) order.push(code);
    seen.set(code, r);
  }
  return order.map(c => seen.get(c));
}

const SEV_ORDER = { danger: 0, warn: 1, info: 2 };

export function computeComparison(planRows, actualRows, cbRows, latestPrices, config) {
  const cfg = config;
  const totalCapital = Number(cfg.total_capital);

  const plan = dedupePlanRows(planRows);
  const actual = actualRows || [];
  const cb = cbRows || [];

  const planByCode = new Map(plan.map(r => [String(r.code), r]));
  const actualByCode = new Map(actual.map(r => [String(r.code), r]));

  const deviations = [];
  const pendingBuild = [];

  // 资金占用
  let stockUsed = 0;
  for (const r of actual) {
    stockUsed += (num(r.entry_price) || 0) * Math.trunc(r.shares || 0);
  }
  let cbUsed = 0;
  for (const r of cb) {
    cbUsed += (num(r.entry_price) || 0) * Math.trunc(r.shares || 0);
  }
  const cash = totalCapital - stockUsed - cbUsed;

  // 待建仓 / 漏买 / 多买
  const emptyPortfolio = actual.length === 0;
  for (const [code, p] of planByCode) {
    if (p.action !== 'buy' || actualByCode.has(code)) continue;
    if (emptyPortfolio) {
      pendingBuild.push({
        code,
        name: p.name || code,
        plan_price: num(p.plan_price),
        shares: p.shares,
        amount: Math.round((num(p.plan_price) || 0) * Math.trunc(p.shares || 0) * 100) / 100,
        stop_price: num(p.stop_price),
        tp_price: num(p.tp_price),
      });
    } else {
      deviations.push(dev('info', 'missing_buy', code, p.name,
        '计划买入但未持有', null, p.shares));
    }
  }
  for (const [code, a] of actualByCode) {
    if (!planByCode.has(code)) {
      deviations.push(dev('warn', 'unplanned', code, a.name,
        '持有但计划中没有（超配）', a.shares, 0));
    }
  }

  // 持仓数超限
  if (actual.length > Number(cfg.max_positions)) {
    deviations.push(dev('danger', 'too_many_positions', '', '',
      `持仓 ${actual.length} 只，超出上限 ${cfg.max_positions} 只`,
      actual.length, Number(cfg.max_positions)));
  }

  // 单票超上限
  const maxSingle = Number(cfg.max_single_weight) * totalCapital;
  for (const [code, a] of actualByCode) {
    const amt = (num(a.entry_price) || 0) * Math.trunc(a.shares || 0);
    if (amt > maxSingle) {
      deviations.push(dev('danger', 'single_weight_over', code, a.name,
        `单票占用 ¥${Math.round(amt).toLocaleString('en-US')}，超出 ${Math.round(Number(cfg.max_single_weight) * 100)}% 上限`,
        Math.round(amt * 100) / 100, Math.round(maxSingle * 100) / 100));
    }
  }

  // 缺止损 / 跌破止损 / 达止盈
  for (const [code, a] of actualByCode) {
    const stop = num(a.stop_price);
    const price = num((latestPrices || {})[code]);
    if (stop === null) {
      deviations.push(dev('warn', 'missing_stop', code, a.name, '未设置止损价'));
    } else if (price !== null && price <= stop) {
      deviations.push(dev('danger', 'stop_breached', code, a.name,
        `最新价 ${price.toFixed(2)} 已跌破止损 ${stop.toFixed(2)}，应执行止损`,
        price, stop));
    }
    const p = planByCode.get(code);
    const tp = p ? num(p.tp_price) : null;
    if (tp !== null && price !== null && price >= tp) {
      deviations.push(dev('info', 'tp_hit', code, a.name || code,
        `最新价 ${price.toFixed(2)} 已达止盈 ${tp.toFixed(2)}，可分批止盈`,
        price, tp));
    }
  }

  // 计划股数不符
  for (const [code, p] of planByCode) {
    const a = actualByCode.get(code);
    if (!a) continue;
    const want = p.shares;
    const have = a.shares;
    if (want !== null && want !== undefined && have !== null && have !== undefined
        && Math.trunc(want) !== Math.trunc(have)) {
      deviations.push(dev('info', 'shares_mismatch', code, p.name,
        `计划 ${Math.trunc(want)} 股，实际 ${Math.trunc(have)} 股`,
        Math.trunc(have), Math.trunc(want)));
    }
  }

  // 总仓位超限
  const maxExposure = Number(cfg.max_stock_exposure) * totalCapital;
  if (stockUsed > maxExposure) {
    deviations.push(dev('danger', 'exposure_over', '', '',
      `股票占用 ¥${Math.round(stockUsed).toLocaleString('en-US')}，超出 ${Math.round(Number(cfg.max_stock_exposure) * 100)}% 上限`,
      Math.round(stockUsed * 100) / 100, Math.round(maxExposure * 100) / 100));
  }

  // 转债仓超配
  const cbCapital = Number(cfg.cb_capital);
  if (cbUsed > cbCapital) {
    deviations.push(dev('warn', 'cb_over', '', '',
      `转债占用 ¥${Math.round(cbUsed).toLocaleString('en-US')}，超出 ¥${Math.round(cbCapital).toLocaleString('en-US')} 上限`,
      Math.round(cbUsed * 100) / 100, cbCapital));
  }
  if (cb.length > Number(cfg.max_cb_positions)) {
    deviations.push(dev('warn', 'cb_too_many', '', '',
      `转债持仓 ${cb.length} 只，超出上限 ${cfg.max_cb_positions} 只`,
      cb.length, Number(cfg.max_cb_positions)));
  }

  deviations.sort((x, y) => (SEV_ORDER[x.severity] ?? 3) - (SEV_ORDER[y.severity] ?? 3));

  const count = s => deviations.filter(d => d.severity === s).length;
  return {
    deviations,
    pending_build: pendingBuild,
    stock_used: Math.round(stockUsed * 100) / 100,
    cb_used: Math.round(cbUsed * 100) / 100,
    cash: Math.round(cash * 100) / 100,
    total_capital: totalCapital,
    plan_count: plan.length,
    actual_count: actual.length,
    cb_count: cb.length,
    danger_count: count('danger'),
    warn_count: count('warn'),
    info_count: count('info'),
  };
}
```

- [ ] **Step 4: 跑一致性测试确认通过**

```bash
cd /Users/shichaopeng/Work/self-dir/projects/a-finder-cli
python3 -m pytest tests/test_decide_compare_parity.py -q 2>&1 | tail -10
```

Expected: PASS (6 cases)

- [ ] **Step 5: 提交**

```bash
git add static/js/decide_compare.js tests/test_decide_compare_parity.py
git commit -m "feat(decide): JS 版对比逻辑 + 与 Python 版一致性测试"
```

---

### Task 4: 前端接入 Worker（api.js / decide.js / decide.html）

**Files:**
- Modify: `static/js/api.js`
- Modify: `static/js/decide.js`
- Modify: `templates/decide.html`
- Test: `tests/test_decide_web.py`（追加静态检查）

- [ ] **Step 1: 追加静态检查测试**

追加到 `tests/test_decide_web.py`：

```python
class TestWorkerIntegrationJs:

    def _js(self, name):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "static", "js", name,
        )
        return open(path, encoding="utf-8").read()

    def test_api_js_has_api_base(self):
        src = self._js("api.js")
        assert "export const API_BASE" in src
        assert "ycu.space" in src

    def test_api_js_has_worker_calls(self):
        src = self._js("api.js")
        for fn in ("fetchDecidePositionsRemote", "postDecidePosition",
                   "deleteDecidePosition", "loginDecide", "logoutDecide"):
            assert f"export " in src and fn in src, f"缺少 {fn}"

    def test_decide_js_uses_worker_and_compare(self):
        src = self._js("decide.js")
        assert "decide_compare.js" in src
        assert "fetchDecidePositionsRemote" in src
        assert "loginDecide" in src

    def test_decide_html_has_login_area(self):
        from app import create_app
        from db_repository import open_db
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        open_db(path).close()
        app = create_app(db_path=path)
        body = app.test_client().get("/decide").data.decode("utf-8")
        assert 'id="decide-auth"' in body
        os.unlink(path)
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /Users/shichaopeng/Work/self-dir/projects/a-finder-cli
python3 -m pytest tests/test_decide_web.py::TestWorkerIntegrationJs -q 2>&1 | tail -6
```

Expected: FAIL（4 项）

- [ ] **Step 3: 改 static/js/api.js**

追加到 `static/js/api.js` 末尾：

```javascript
// ---------------------------------------------------------------------------
// Cloudflare Worker（持仓写入；D1 是唯一真相源）
// ---------------------------------------------------------------------------

// 线上静态站：同源（Worker 挂在 ycu.space/api/*）
// 本地 Flask：跨域到 ycu.space
export const API_BASE = isStatic() ? '' : 'https://ycu.space';

async function reqJSON(url, opts) {
  const resp = await fetch(url, Object.assign({ credentials: 'include' }, opts));
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw Object.assign({ status: resp.status }, data);
  return data;
}

export function fetchDecidePositionsRemote() {
  return reqJSON(API_BASE + '/api/decide/positions');
}

export function postDecidePosition(payload) {
  return reqJSON(API_BASE + '/api/decide/positions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export function deleteDecidePosition(code, isCb) {
  const q = isCb ? '?cb=1' : '';
  return reqJSON(API_BASE + '/api/decide/positions/' + encodeURIComponent(code) + q, {
    method: 'DELETE',
  });
}

export function loginDecide(password) {
  return reqJSON(API_BASE + '/api/decide/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password }),
  });
}

export function logoutDecide() {
  return reqJSON(API_BASE + '/api/decide/logout', { method: 'POST' });
}

export function fetchDecideAuthState() {
  return reqJSON(API_BASE + '/api/decide/health');
}

export function fetchDecideConfig() {
  return isStatic()
    ? getJSON(dsPath('data/decide-config.json'))
    : getJSON('/api/decide/config');
}
```

- [ ] **Step 4: 改 templates/decide.html**

在 `<div class="row row-cols-2 row-cols-md-5 g-2 mb-3" id="decide-summary"></div>` 之前插入登录区：

```html
  <div id="decide-auth" class="mb-3"></div>
```

- [ ] **Step 5: 改 static/js/decide.js**

改动点（保留现有渲染函数，仅替换数据源与对比计算）：

1. 顶部 import 改为：

```javascript
import {
  fetchDecidePlan, fetchDecideConfig,
  fetchDecidePositionsRemote, postDecidePosition, deleteDecidePosition,
  loginDecide, logoutDecide, fetchDecideAuthState, isStatic,
} from './api.js';
import { $, fmt, esc, setStatus } from './dom.js';
import { computeComparison } from './decide_compare.js';
```

2. 新增登录区渲染与状态：

```javascript
let AUTHED = false;
let CFG = null;

function renderAuthBar() {
  const el = $('decide-auth');
  if (!el) return;
  if (isStatic() && !AUTHED) {
    el.innerHTML = `<div class="alert alert-secondary d-flex align-items-center gap-2 py-2 mb-0">
      <span>只读模式。登录后可录入 / 编辑持仓。</span>
      <button id="btn-login" class="btn btn-sm btn-primary ms-auto">登录</button>
    </div>`;
  } else if (AUTHED) {
    el.innerHTML = `<div class="alert alert-success d-flex align-items-center gap-2 py-2 mb-0">
      <span>已登录，可编辑持仓。</span>
      <button id="btn-logout" class="btn btn-sm btn-outline-secondary ms-auto">登出</button>
    </div>`;
  } else {
    el.innerHTML = '';
  }
}

function showLoginForm() {
  const pwd = prompt('输入决策台密码');
  if (!pwd) return;
  loginDecide(pwd).then(() => {
    AUTHED = true;
    renderAuthBar();
    loadAll();
  }).catch(e => setStatus('登录失败：' + (e.error || e.status), 'alert-danger'));
}
```

3. `loadAll()` 改为：plan 从 `fetchDecidePlan`，持仓从 Worker，对比前端算：

```javascript
async function loadAll() {
  setStatus('加载中…', 'alert-info');
  try {
    const [planRes, posRes, cfgRes] = await Promise.all([
      fetchDecidePlan().catch(() => ({ date: '', rows: [], dates: [] })),
      fetchDecidePositionsRemote().catch(() => ({ rows: [], cb_rows: [] })),
      fetchDecideConfig().catch(() => null),
    ]);
    CFG = cfgRes || CFG;
    fillDateOptions(planRes.dates || []);
    const date = ($('decide-date') && $('decide-date').value) || planRes.date || '';

    // 最新价：从 plan 行的 plan_price 近似（静态站无实时行情）
    const prices = {};
    (planRes.rows || []).forEach(r => { if (r.plan_price != null) prices[r.code] = r.plan_price; });

    const cmp = CFG
      ? computeComparison(planRes.rows || [], posRes.rows || [], posRes.cb_rows || [], prices, CFG)
      : { deviations: [], pending_build: [], stock_used: 0, cb_used: 0, cash: 0,
          actual_count: 0, cb_count: 0, plan_count: 0, danger_count: 0, warn_count: 0, info_count: 0 };

    renderPlan(date, planRes);
    renderComparison(cmp);
    renderPositions(posRes.rows || [], posRes.cb_rows || []);
    renderSummary(cmp);
    renderAuthBar();
    setStatus('', '');
  } catch (e) {
    setStatus('加载失败：' + (e && e.message ? e.message : e), 'alert-danger');
  }
}
```

4. `showAddForm` 的保存改为 `postDecidePosition(payload)`；删除改为 `deleteDecidePosition(code, isCb)`

5. `DOMContentLoaded` 里加登录/登出按钮委托：

```javascript
document.addEventListener('click', e => {
  if (e.target.closest('#btn-login')) { showLoginForm(); return; }
  if (e.target.closest('#btn-logout')) {
    logoutDecide().then(() => { AUTHED = false; renderAuthBar(); loadAll(); });
  }
});
```

- [ ] **Step 6: 语法检查 + 跑测试**

```bash
cd /Users/shichaopeng/Work/self-dir/projects/a-finder-cli
node --check static/js/api.js && node --check static/js/decide.js && node --check static/js/decide_compare.js && echo "语法 OK"
python3 -m pytest tests/test_decide_web.py -q 2>&1 | tail -8
```

Expected: `语法 OK` + all PASS

- [ ] **Step 7: 提交**

```bash
git add static/js/api.js static/js/decide.js static/js/decide_compare.js templates/decide.html tests/test_decide_web.py
git commit -m "feat(decide): 前端接入 Worker（持仓读写 + 登录 + 前端对比）"
```

---

### Task 5: 导出 decide-config + 调整 plan 快照

**Files:**
- Modify: `export_json.py`
- Test: `tests/test_decide_web.py`（追加）

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_decide_web.py`：

```python
class TestDecideConfigExport:

    def test_export_writes_decide_config(self, plan_db, tmp_path):
        from export_json import export
        out = str(tmp_path / "site")
        export(plan_db, out)
        cfg_path = os.path.join(out, "data", "decide-config.json")
        assert os.path.exists(cfg_path)
        cfg = json.load(open(cfg_path, encoding="utf-8"))
        assert cfg["total_capital"] == 100000.0
        assert cfg["max_positions"] == 8
        assert cfg["max_single_weight"] == 0.10

    def test_plan_snapshot_excludes_comparison(self, plan_db, tmp_path):
        """对比改由前端算，快照不再含 deviations。"""
        from export_json import export
        out = str(tmp_path / "site")
        export(plan_db, out)
        snap_path = os.path.join(out, "data", "decide-2026-10-25.json")
        snap = json.load(open(snap_path, encoding="utf-8"))
        assert "deviations" not in snap
        assert "pending_build" not in snap
        assert "rows" in snap
        assert "skipped" in snap
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python3 -m pytest tests/test_decide_web.py::TestDecideConfigExport -q 2>&1 | tail -6
```

Expected: FAIL（无 decide-config.json）

- [ ] **Step 3: 改 export_json.py**

在 `export()` 里、`_export_decide_data(conn, data_dir)` 之后加：

```python
        (data_dir / "decide-config.json").write_text(
            json.dumps(PORTFOLIO_10W, ensure_ascii=False, indent=2), encoding="utf-8"
        )
```

并把 `_export_decide_data` 里的快照构造改为**只写 plan**：

```python
    for d in dates:
        picks = [
            dict(zip(["rank", "code", "name", "buy", "stop", "target", "score"], r))
            for r in conn.execute(
                "SELECT rank, code, name, buy, stop, target, score "
                "FROM daily_picks WHERE kind='买入信号' AND date=? ORDER BY rank",
                (d,),
            )
        ]
        built = build_plan_from_picks(picks)
        json.dump(
            {"date": d, **built},
            (data_dir / f"decide-{d}.json").open("w"),
            ensure_ascii=False, default=str,
        )
```

同时删除 `_export_decide_data` 里对 `compare_plan_vs_actual` 的 import 与调用。

- [ ] **Step 4: 跑测试 + 全量**

```bash
python3 -m pytest tests/test_decide_web.py -q 2>&1 | tail -5
python3 -m pytest tests/ -q 2>&1 | tail -3
```

Expected: all PASS

- [ ] **Step 5: 提交**

```bash
git add export_json.py tests/test_decide_web.py
git commit -m "feat(export): 导出 decide-config.json；plan 快照去掉对比结果（改前端算）"
```

---

### Task 6: 部署（需手动执行）+ 线上验证

**Files:** None（部署与验证）

- [ ] **Step 1: 确认 ycu.space 在当前 Cloudflare 账户的 zone 列表里**

登录 https://dash.cloudflare.com → 左侧 Websites，确认能看到 `ycu.space`。
看不到则 Worker 路由挂不上，需先把域名接入 Cloudflare。

- [ ] **Step 2: 登录 wrangler 并创建 D1**

```bash
cd /Users/shichaopeng/Work/self-dir/projects/a-finder-cli
npx --yes wrangler@latest login
npx --yes wrangler@latest d1 create a-finder
```

把返回的 `database_id` 填入 `wrangler.toml` 的 `database_id` 字段。

- [ ] **Step 3: 初始化 D1 表**

```bash
npx --yes wrangler@latest d1 execute a-finder --file=worker/schema.sql --remote
```

Expected: 两张表创建成功

- [ ] **Step 4: 设置密码与签名密钥**

```bash
npx --yes wrangler@latest secret put DECIDE_PASSWORD
npx --yes wrangler@latest secret put DECIDE_SECRET
```

`DECIDE_PASSWORD` 输入你的登录密码；`DECIDE_SECRET` 输入随机字符串
（可用 `openssl rand -hex 32` 生成）。

- [ ] **Step 5: 部署 Worker**

```bash
npx --yes wrangler@latest deploy
```

Expected: 部署成功，路由到 `ycu.space/api/*`

- [ ] **Step 6: 验证线上 Worker**

```bash
curl -s https://ycu.space/api/decide/health; echo
curl -s -o /dev/null -w "未登录写入: %{http_code}\n" -X POST https://ycu.space/api/decide/positions \
  -H 'Content-Type: application/json' -d '{"code":"600519","shares":100,"entry_price":1500}'
```

Expected: `{"ok":true,"d1":"ok","authed":false}` + `未登录写入: 401`

- [ ] **Step 7: 重新导出并部署静态站**

```bash
python3 export_json.py --db hs300.db --out site
# 按现有流程推送 gh-pages（或等 CI workflow 自动跑）
```

- [ ] **Step 8: 浏览器端到端验证**

打开 https://ycu.space/decide.html：
1. 顶部显示「只读模式。登录后可录入 / 编辑持仓」
2. 点「登录」→ 输入密码 → 显示「已登录，可编辑持仓」
3. 点「＋ 添加持仓」→ 填 600519 / 100 股 / 1500 → 保存
4. 刷新页面 → 持仓仍在（来自 D1）
5. 删除 → 刷新 → 已删除
6. 登出 → 录入按钮消失，写入返回 401

- [ ] **Step 9: 更新 CLAUDE.md**

在 Architecture 的 **10w 决策台** bullet 后追加：

```markdown
- **持仓云端化** — 持仓（`real_positions` / `real_cb_positions`）的唯一真相源是
  **Cloudflare D1**，由 `worker/index.js` 提供 `/api/decide/*`（读公开、写需密码
  登录的 HttpOnly Cookie，密码与密钥走 `wrangler secret`）。本地 SQLite 同名表
  保留但不再写入。前端对比逻辑在 `static/js/decide_compare.js`（与
  `decide_service.py` 由 `tests/test_decide_compare_parity.py` 保证一致），
  配置来自 `data/decide-config.json`。部署见 `wrangler.toml`。
```

- [ ] **Step 10: 提交**

```bash
git add CLAUDE.md
git commit -m "docs: 记录持仓云端化（Worker + D1）"
git push origin main
```

---

## Self-Review

**Spec coverage:**
- §三 架构（D1 只存 2 表 / 读写分道 / 对比前端算）→ Task 3 + Task 5
- §4.1 路由 6 个 → Task 2
- §4.2 认证（HMAC + Cookie）→ Task 1
- §4.3 D1 schema → Task 1
- §4.4 CORS 白名单 → Task 1
- §5.1 API_BASE + Worker 调用 → Task 4
- §5.2 decide_compare.js → Task 3
- §5.3 decide.js 改造 → Task 4
- §5.4 export_json decide-config → Task 5
- §六 部署步骤 → Task 6
- §八 测试策略（一致性/认证/导出/回归）→ Task 1/2/3/5
- §九 风险控制 → 各 Task 内体现

**Placeholder scan:** 无 TBD/TODO。所有代码完整可运行。

**Type consistency:**
- `signToken(payload, secret)` / `verifyToken(token, secret)` / `parseCookie(header, name)` / `buildCookie(value, maxAge)` / `clearCookie()` — Task 1 定义，Task 2 使用，一致
- `computeComparison(planRows, actualRows, cbRows, latestPrices, config)` — Task 3 定义，Task 4 使用，一致
- `fetchDecidePositionsRemote` / `postDecidePosition(payload)` / `deleteDecidePosition(code, isCb)` / `loginDecide(password)` / `logoutDecide()` / `fetchDecideAuthState()` / `fetchDecideConfig()` — Task 4 定义与使用，一致
- `pending_build` 字段名 — Python 与 JS 一致（Task 3 一致性测试保证）
- `PORTFOLIO_10W` 键名 — Task 5 导出，Task 3 JS 消费，一致（Task 5 测试校验）
- D1 表结构 — 与 `db/migrations/2026_10_25_real_positions.sql` 逐列一致
