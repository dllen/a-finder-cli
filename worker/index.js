// Cloudflare Worker：10w 决策台持仓的线上读写。
// 读公开（首屏渲染需要），写需登录后的 HttpOnly Cookie。
// 登录是双因子：DECIDE_USER + DECIDE_PASSWORD；签名密钥 DECIDE_SECRET。
// 三者都通过 `wrangler secret put` 注入。
// 另带一个 Cron：每天 UTC 07:35（北京 15:35）兜底触发 deploy-site 的
// workflow_dispatch（GitHub schedule 高负载时会延迟/丢弃）。
// 该功能需要 secret GITHUB_DISPATCH_TOKEN（fine-grained PAT，
// 仅授权本仓库 Actions: Read and write）。
import { buildCookie, clearCookie, readAuth, signToken } from './auth.js';

const MAX_AGE = 7 * 24 * 3600;           // 7 天（秒）
const ALLOWED_ORIGINS = [
  'https://ycu.space',
  'https://dllen.github.io',
  'http://127.0.0.1:8000',
  'http://localhost:8000',
];

// Cron 兜底触发的目标 workflow
const DISPATCH_REPO = 'dllen/a-finder-cli';
const DISPATCH_WORKFLOW = 'deploy-site.yml';
const DISPATCH_REF = 'main';

/** 调 GitHub API 触发 workflow_dispatch。返回 {ok, status, detail}。 */
async function dispatchDeploySite(env) {
  if (!env.GITHUB_DISPATCH_TOKEN) {
    return { ok: false, status: 0, detail: 'GITHUB_DISPATCH_TOKEN 未配置' };
  }
  const resp = await fetch(
    `https://api.github.com/repos/${DISPATCH_REPO}/actions/workflows/${DISPATCH_WORKFLOW}/dispatches`,
    {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${env.GITHUB_DISPATCH_TOKEN}`,
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'a-finder-cron',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ ref: DISPATCH_REF }),
    },
  );
  // 成功返回 204 No Content
  if (resp.status === 204) return { ok: true, status: 204, detail: '' };
  return { ok: false, status: resp.status, detail: await resp.text() };
}

function corsHeaders(origin) {
  const h = {
    'Access-Control-Allow-Methods': 'GET, POST, DELETE, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Access-Control-Max-Age': '86400',
  };
  if (ALLOWED_ORIGINS.includes(origin)) {
    h['Access-Control-Allow-Origin'] = origin;
    h['Access-Control-Allow-Vary'] = 'Origin';
    h['Vary'] = 'Origin';
    h['Access-Control-Allow-Credentials'] = 'true';
  }
  return h;
}

function json(data, status, origin, extraHeaders) {
  return new Response(JSON.stringify(data), {
    status: status || 200,
    headers: Object.assign(
      { 'Content-Type': 'application/json; charset=utf-8' },
      corsHeaders(origin),
      extraHeaders || {},
    ),
  });
}

async function isAuthed(request, env) {
  return (await readAuth(request, env)) !== null;
}

// agent 端点的独立认证：只看 X-Agent-Secret 头，不看登录 cookie。
// 两套认证故意分开 —— 复用 DECIDE_SECRET 会让一个泄露同时丢掉登录态和 agent 通道。
async function isAgentAuthed(request, env) {
  if (!env.REFRESH_AGENT_SECRET) return false;
  return request.headers.get('X-Agent-Secret') === env.REFRESH_AGENT_SECRET;
}

function nowStamp() {
  return new Date().toISOString().slice(0, 19);
}

// 取 D1 的 meta.changes（实际影响的行数）。
// **缺省值是 0（fail-closed）**：D1 的 meta 形状一变，缺省 1 会让「抢占失败」
// 这个守卫静默消失（并发领任务时两个 agent 都以为自己抢到了）；缺省 0 则只是
// 保守地返回「没抢到」，agent 会下一轮重试，不会重复跑流水线。
function metaChanges(result) {
  if (!result || !result.meta) return 0;
  return typeof result.meta.changes === 'number' ? result.meta.changes : 0;
}

// 过期清理：把「卡住」的任务标记为 failed。
//
// 为什么必须有：409 互斥只数 pending/running。agent 领完任务后崩掉（或根本没起），
// 行会永远停在 running/pending ⇒ 之后每次刷新都 409 ⇒ **整个功能永久报废**，
// 只能手工改 D1 恢复。设计文档的「错误处理」表承诺过这个超时。
//
// 阈值：pending 10 分钟（agent 还没领走）、running 60 分钟（流水线正常要 15~35 分钟）。
//
// 时间比较为什么能用字符串：created_at/started_at 与 nowStamp() 同为定宽 UTC 串
// 'YYYY-MM-DDTHH:MM:SS'，定宽 ⇒ 字典序 == 时间序。（换成带时区偏移或变宽格式就废了。）
const PENDING_TTL_SECONDS = 10 * 60;
const RUNNING_TTL_SECONDS = 60 * 60;

/** 返回 N 秒前的 nowStamp() 格式时间串。 */
function stampBefore(seconds) {
  const t = new Date(Date.now() - seconds * 1000);
  return t.toISOString().slice(0, 19);
}

/** 把过期的 pending/running 任务标记为 failed。返回被清理的行数。 */
async function expireStaleJobs(env) {
  let expired = 0;
  const pending = await env.DB.prepare(
    `UPDATE refresh_jobs SET status = 'failed', message = ?, finished_at = ?
     WHERE status = 'pending' AND created_at < ?`,
  ).bind(
    '本机 agent 未响应，任务超时',
    nowStamp(),
    stampBefore(PENDING_TTL_SECONDS),
  ).run();
  expired += metaChanges(pending);
  const running = await env.DB.prepare(
    `UPDATE refresh_jobs SET status = 'failed', message = ?, finished_at = ?
     WHERE status = 'running' AND started_at < ?`,
  ).bind(
    '本机 agent 超时未回报，任务超时',
    nowStamp(),
    stampBefore(RUNNING_TTL_SECONDS),
  ).run();
  expired += metaChanges(running);
  return expired;
}

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
      const payload = await readAuth(request, env);
      let d1 = 'ok';
      try {
        await env.DB.prepare('SELECT 1').first();
      } catch (e) {
        d1 = 'error: ' + (e && e.message ? e.message : e);
      }
      return json({
        ok: d1 === 'ok',
        d1,
        authed: payload !== null,
        user: payload ? payload.user || null : null,
      }, 200, origin);
    }

    // ---- login ----
    if (path === '/api/decide/login' && method === 'POST') {
      const body = await request.json().catch(() => ({}));
      const userOk = !!env.DECIDE_USER && body.user === env.DECIDE_USER;
      const passOk = !!env.DECIDE_PASSWORD && body.password === env.DECIDE_PASSWORD;
      if (!userOk || !passOk) {
        // 不区分用户名错还是密码错，避免探测
        return json({ error: '用户名或密码错误' }, 401, origin);
      }
      const token = await signToken(
        { user: env.DECIDE_USER, exp: Date.now() + MAX_AGE * 1000 },
        env.DECIDE_SECRET,
      );
      return json({ ok: true, user: env.DECIDE_USER }, 200, origin, {
        'Set-Cookie': buildCookie(token, MAX_AGE),
      });
    }

    // ---- logout ----
    if (path === '/api/decide/logout' && method === 'POST') {
      return json({ ok: true }, 200, origin, { 'Set-Cookie': clearCookie() });
    }

    // ---- 读持仓（公开）----
    if (path === '/api/decide/positions' && method === 'GET') {
      const [stock, cb] = await Promise.all([
        env.DB.prepare('SELECT * FROM real_positions ORDER BY code').all(),
        env.DB.prepare('SELECT * FROM real_cb_positions ORDER BY code').all(),
      ]);
      return json(
        { rows: stock.results || [], cb_rows: cb.results || [] }, 200, origin,
      );
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
      const stop = p.stop_price;
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
        code,
        p.name || null,
        Math.trunc(shares),
        entryPrice,
        stop == null || stop === '' ? null : Number(stop),
        p.entry_date || null,
        p.note || null,
        nowStamp(),
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

    // ---- 手动触发 dispatch 测试（需认证，验证 GITHUB_DISPATCH_TOKEN 配好没）----
    if (path === '/api/decide/dispatch-test' && method === 'POST') {
      if (!(await isAuthed(request, env))) {
        return json({ error: '未登录' }, 401, origin);
      }
      const r = await dispatchDeploySite(env);
      return json(
        r.ok
          ? { ok: true, status: r.status, workflow: DISPATCH_WORKFLOW, ref: DISPATCH_REF }
          : { ok: false, status: r.status, detail: r.detail.slice(0, 300) },
        r.ok ? 200 : 502,
        origin,
      );
    }

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
      // 先清理过期任务：否则上一次崩掉的 agent 会留下一条永远 running 的行，
      // 让这次（和以后每一次）刷新都 409 —— 整个功能永久报废。
      await expireStaleJobs(env);
      const jobId = crypto.randomUUID();
      // 互斥用**一条原子语句**：INSERT ... SELECT ... WHERE NOT EXISTS (...)
      // 为什么不用「先 SELECT COUNT 再 INSERT」：那两条语句之间有窗口，
      // 两次并发点击都可能看到 count=0 而各插一条 pending —— 正是这里要防的
      // 「连点排出多班流水线」。D1 单条语句是原子的，WHERE NOT EXISTS 在
      // 同一条语句里完成「检查 + 插入」。
      const ins = await env.DB.prepare(
        `INSERT INTO refresh_jobs (job_id, status, requested_by, created_at)
         SELECT ?,?,?,? WHERE NOT EXISTS (
           SELECT 1 FROM refresh_jobs WHERE status IN ('pending','running')
         )`,
      ).bind(jobId, 'pending', payload.user || null, nowStamp()).run();
      if (!metaChanges(ins)) {
        // 已有 pending/running 的任务 → 409
        return json({ error: '已有刷新任务进行中' }, 409, origin);
      }
      return json({ ok: true, job_id: jobId }, 200, origin);
    }

    // ---- 刷新：查状态（需登录）----
    if (path === '/api/refresh/status' && method === 'GET') {
      // 状态端点只要「是否登录」，不需要 payload.user ⇒ 用 isAuthed 更贴切
      if (!(await isAuthed(request, env))) {
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
      if (!(await isAgentAuthed(request, env))) {
        return json({ error: 'agent 认证失败（REFRESH_AGENT_SECRET 未配置或不匹配）' }, 401, origin);
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
      if (!metaChanges(upd)) {
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
      if (!(await isAgentAuthed(request, env))) {
        return json({ error: 'agent 认证失败（REFRESH_AGENT_SECRET 未配置或不匹配）' }, 401, origin);
      }
      const body = await request.json().catch(() => ({}));
      const jobId = String(body.job_id || '').trim();
      if (!jobId) {
        return json({ error: 'job_id 必填' }, 400, origin);
      }
      const st = body.status === 'success' ? 'success' : 'failed';
      const upd = await env.DB.prepare(
        `UPDATE refresh_jobs SET status = ?, latest_date = ?, message = ?, finished_at = ? WHERE job_id = ?`,
      ).bind(
        st,
        body.latest_date || null,
        String(body.message || '').slice(0, 500) || null,
        nowStamp(),
        jobId,
      ).run();
      // 0 行更新 = 这个 job_id 不存在。之前一律返回 ok:true，agent 无从得知
      // 自己报错了对象（可能是任务已被过期清理掉了）。
      if (!metaChanges(upd)) {
        return json({ error: '任务不存在' }, 404, origin);
      }
      return json({ ok: true }, 200, origin);
    }

    return json({ error: 'not found' }, 404, origin);
  },

  // Cron：每天 UTC 07:35（北京 15:35）兜底触发 deploy-site workflow_dispatch。
  // GitHub schedule 高负载时会延迟/丢弃；交易日判断仍由 workflow 自己做，
  // 这里每天发，非交易日 workflow 会在「判定 A 股交易日」步骤跳过。
  async scheduled(event, env, ctx) {
    ctx.waitUntil((async () => {
      try {
        const r = await dispatchDeploySite(env);
        if (r.ok) {
          console.log('workflow_dispatch 已触发:', DISPATCH_WORKFLOW, 'ref', DISPATCH_REF);
        } else {
          console.error('workflow_dispatch 失败:', r.status, r.detail);
        }
      } catch (err) {
        console.error('workflow_dispatch 异常:', String(err).slice(0, 200));
      }
      // 注意：这里绝不能抛错。waitUntil 的 promise 被 reject 会让整个 invocation
      // 标记失败并取消同一次 cron 中的其他 waitUntil 任务 —— 抓取就是这样被静默掐死的
      // （表现：心跳只到 enter，任务游标永不推进）。
    })());

  },
};
