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
    # s[1:] 是 list（JSON 解出来的），断言里比的是 tuple —— 统一转 tuple
    got = {s[0]: tuple(s[1:]) for s in steps}
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
