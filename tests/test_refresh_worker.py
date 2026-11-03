"""Worker 刷新端点的契约测试。

用 node 跑 worker/index.js 的 fetch 处理器，用一个假的 env（内存版 D1）验证：
- 前端端点必须登录（无 cookie → 401）
- agent 端点必须带 X-Agent-Secret（错 secret → 401）
- 两套认证不串：cookie 打到 agent 端点 → 401，agent secret 打到前端端点 → 401
- 任务状态机：建 → 领 → 回报 → 查
- 僵尸任务必须能被过期清理（否则 409 永久卡死整个功能）
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WORKER_DIR = ROOT / "worker"

# ---- 假 D1：只实现本测试用到的语句 ----
# 注意：Worker 里既有 `.prepare(sql).bind(...).run()`，也有 `.prepare(sql).first()`
# （无 bind，例如 SELECT COUNT(*)）。两种调用形态都要支持。
# 时间戳一律用 nowStamp() 同格式的定宽 UTC 字符串 'YYYY-MM-DDTHH:MM:SS'，
# 字典序 == 时间序（Worker 侧的过期判定就靠这个性质）。
HARNESS = r"""
// ---- 假 D1：只实现本测试用到的语句 ----
class FakeD1 {
  constructor() { this.rows = new Map(); }

  // 把 'YYYY-MM-DDTHH:MM:SS' 字符串当时间比较（定宽 UTC，字典序 == 时间序）
  static isBefore(stamp, limit) {
    return typeof stamp === 'string' && stamp < limit;
  }

  prepare(sql) {
    const self = this;

    const exec = async (args) => {
      // ---- 过期清理：pending 超过 10 分钟 ----
      if (sql.includes("SET status = 'failed'") && sql.includes("status = 'pending'")) {
        // args = [message, finished_at, limit]（limit 绑在第 3 个 ? 上）
        const limit = args[2];
        let changes = 0;
        for (const r of self.rows.values()) {
          if (r.status === 'pending' && FakeD1.isBefore(r.created_at, limit)) {
            r.status = 'failed';
            r.message = '本机 agent 未响应，任务超时';
            r.finished_at = limit;
            changes++;
          }
        }
        return { success: true, meta: { changes } };
      }
      // ---- 过期清理：running 超过 60 分钟 ----
      if (sql.includes("SET status = 'failed'") && sql.includes("status = 'running'")) {
        // args = [message, finished_at, limit]（limit 绑在第 3 个 ? 上）
        const limit = args[2];
        let changes = 0;
        for (const r of self.rows.values()) {
          if (r.status === 'running' && FakeD1.isBefore(r.started_at, limit)) {
            r.status = 'failed';
            r.message = '本机 agent 超时未回报，任务超时';
            r.finished_at = limit;
            changes++;
          }
        }
        return { success: true, meta: { changes } };
      }
      // ---- 原子建任务：INSERT ... SELECT ... WHERE NOT EXISTS ----
      if (sql.includes('INSERT INTO refresh_jobs') && sql.includes('NOT EXISTS')) {
        const busy = [...self.rows.values()]
          .some(r => r.status === 'pending' || r.status === 'running');
        if (busy) return { success: true, meta: { changes: 0 } };
        const o = Object.fromEntries(args.map((v, i) => [COLS[i], v]));
        self.rows.set(o.job_id, { ...o });
        return { success: true, meta: { changes: 1 } };
      }
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
        // self.loseRace=true 模拟「别人抢先领走」：影响 0 行
        if (self.loseRace) return { success: true, meta: { changes: 0 } };
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

// 2b. 两套认证不串：cookie 打到 agent 端点 → 401
r = await req('/api/refresh/next', { headers: { Cookie: COOKIE } });
out.push(['next_cookie_only', r.status]);

// 2c. 两套认证不串：agent secret 打到前端端点 → 401
r = await req('/api/refresh', { method: 'POST', headers: { 'X-Agent-Secret': 'agent-secret' } });
out.push(['refresh_agentsecret_only', r.status]);

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

// 7b. agent 回报一个不存在的 job_id → 404
r = await req('/api/refresh/result', {
  method: 'POST',
  headers: { 'X-Agent-Secret': 'agent-secret', 'Content-Type': 'application/json' },
  body: JSON.stringify({ job_id: 'no-such-job', status: 'success' }),
});
out.push(['result_unknown_job', r.status]);

// 8. 查状态 → success + latest_date
r = await req('/api/refresh/status?job_id=' + created.job_id, { headers: { Cookie: COOKIE } });
const st2 = await r.json();
out.push(['status_success', r.status, st2.status, st2.latest_date]);

// 9. 建任务后已无 pending/running → 可以再建
r = await req('/api/refresh', { method: 'POST', headers: { Cookie: COOKIE } });
out.push(['create_again', r.status]);

// 10. 无 pending 任务时 agent 领任务 → {job_id: null}
r = await req('/api/refresh/next', { headers: { 'X-Agent-Secret': 'agent-secret' } });
await r.json();
r = await req('/api/refresh/next', { headers: { 'X-Agent-Secret': 'agent-secret' } });
const emptyNext = await r.json();
out.push(['next_empty', r.status, emptyNext.job_id === null]);

// 10b. 抢占失败分支（UPDATE 影响 0 行）→ 也返回 {job_id: null}
// 先塞一条 pending 任务，再让假 D1 模拟「被别人抢先领走」
env.DB.rows.set('race-job', {
  job_id: 'race-job', status: 'pending', requested_by: 'u',
  created_at: '2026-11-03T00:00:00', started_at: null, finished_at: null,
  latest_date: null, message: null,
});
env.DB.loseRace = true;
r = await req('/api/refresh/next', { headers: { 'X-Agent-Secret': 'agent-secret' } });
const lost = await r.json();
env.DB.loseRace = false;
out.push(['next_lost_race', r.status, lost.job_id === null]);

// 11. 僵尸任务（pending 太久）必须被过期清理，不能永久 409
//
// 先清掉表里其它**存活**的任务，否则它们的 pending 会让 NOT EXISTS 正确地
// 返回 409 —— 那样测的就不是「僵尸任务被过期清理」，而是「有活任务时互斥」。
// 这里只留僵尸本身，隔离出「唯一的阻塞因素是僵尸」这个场景。
env.DB.rows.clear();
env.DB.rows.set('zombie-pending', {
  job_id: 'zombie-pending', status: 'pending', requested_by: 'u',
  created_at: '2020-01-01T00:00:00', started_at: null, finished_at: null,
  latest_date: null, message: null,
});
r = await req('/api/refresh', { method: 'POST', headers: { Cookie: COOKIE } });
out.push(['create_over_zombie', r.status]);
r = await req('/api/refresh/status?job_id=zombie-pending', { headers: { Cookie: COOKIE } });
const zombie = await r.json();
out.push(['zombie_expired', r.status, zombie.status]);

// 12. 僵尸任务（running 太久）同样要被过期清理
env.DB.rows.clear();
env.DB.rows.set('zombie-running', {
  job_id: 'zombie-running', status: 'running', requested_by: 'u',
  created_at: '2020-01-01T00:00:00', started_at: '2020-01-01T00:00:00',
  finished_at: null, latest_date: null, message: null,
});
r = await req('/api/refresh', { method: 'POST', headers: { Cookie: COOKIE } });
out.push(['create_over_zombie_running', r.status]);
r = await req('/api/refresh/status?job_id=zombie-running', { headers: { Cookie: COOKIE } });
const zombie2 = await r.json();
out.push(['zombie_running_expired', r.status, zombie2.status]);

process.stdout.write(JSON.stringify(out));
"""


def _run_worker():
    if shutil.which("node") is None:
        pytest.skip("node 不在 PATH，无法跑 Worker 契约测试")
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", HARNESS],
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


def test_auth_systems_do_not_bleed():
    """两套认证不许串：cookie 打 agent 端点、agent secret 打前端端点都得 401。"""
    steps = _run_worker()
    got = {s[0]: tuple(s[1:]) for s in steps}
    assert got["next_cookie_only"] == (401,), got
    assert got["refresh_agentsecret_only"] == (401,), got


def test_result_unknown_job_is_404():
    """agent 回报了不存在的 job_id → 404（否则 agent 无从知道自己报错了对象）。"""
    steps = _run_worker()
    got = {s[0]: tuple(s[1:]) for s in steps}
    assert got["result_unknown_job"] == (404,), got


def test_next_returns_null_when_no_pending():
    """没有 pending 任务时领任务 → {job_id: null}（agent 据此进入等待）。"""
    steps = _run_worker()
    got = {s[0]: tuple(s[1:]) for s in steps}
    assert got["next_empty"] == (200, True), got


def test_next_lost_race_returns_null():
    """抢占失败（UPDATE 影响 0 行）→ 返回 {job_id: null}，不把任务派给两个 agent。"""
    steps = _run_worker()
    got = {s[0]: tuple(s[1:]) for s in steps}
    assert got["next_lost_race"] == (200, True), got


def test_zombie_jobs_are_expired_not_409_forever():
    """僵尸任务（pending 太久 / running 太久）必须被过期清理，不能永久 409。"""
    steps = _run_worker()
    got = {s[0]: tuple(s[1:]) for s in steps}
    # 有僵尸 pending 时仍能建新任务（不再 409 卡死）
    assert got["create_over_zombie"] == (200,), got
    assert got["zombie_expired"] == (200, "failed"), got
    assert got["create_over_zombie_running"] == (200,), got
    assert got["zombie_running_expired"] == (200, "failed"), got


def test_node_available():
    """node 必须可用（否则上面的测试是假绿）。"""
    if shutil.which("node") is None:
        pytest.skip("node 不在 PATH，无法跑 Worker 契约测试")
    proc = subprocess.run(["node", "--version"], capture_output=True, text=True)
    assert proc.returncode == 0, "node 不可用，无法跑 Worker 契约测试"
