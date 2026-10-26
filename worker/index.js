// Cloudflare Worker：10w 决策台持仓的线上读写。
// 读公开（首屏渲染需要），写需登录后的 HttpOnly Cookie。
// 登录是双因子：DECIDE_USER + DECIDE_PASSWORD；签名密钥 DECIDE_SECRET。
// 三者都通过 `wrangler secret put` 注入。
// 另带一个 Cron：每天 UTC 07:35（北京 15:35）兜底触发 deploy-site 的
// workflow_dispatch（GitHub schedule 高负载时会延迟/丢弃）。
// 该功能需要 secret GITHUB_DISPATCH_TOKEN（fine-grained PAT，
// 仅授权本仓库 Actions: Read and write）。
import { buildCookie, clearCookie, readAuth, signToken } from './auth.js';
import { handleMarketApi } from './market/api.js';
import {
  beijingNow, isTradingWindow, planChunk, shouldRun, advanceJob,
} from './market/jobs.js';
import {
  listSymbols, ensureJob, activeJob, upsertPrices, updateJob,
} from './market/repo.js';
import { fetchBars, pickBars } from './market/sources.js';

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

function nowStamp() {
  return new Date().toISOString().slice(0, 19);
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

    // /api/market/* 与 /api/sync/start 由独立模块处理（自带鉴权）
    const marketRes = await handleMarketApi(request, env, path);
    if (marketRes) return marketRes;

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

    return json({ error: 'not found' }, 404, origin);
  },

  // Cron：每天 UTC 07:35（北京 15:35）兜底触发 deploy-site workflow_dispatch。
  // GitHub schedule 高负载时会延迟/丢弃；交易日判断仍由 workflow 自己做，
  // 这里每天发，非交易日 workflow 会在「判定 A 股交易日」步骤跳过。
  async scheduled(event, env, ctx) {
    ctx.waitUntil((async () => {
      const r = await dispatchDeploySite(env);
      if (r.ok) {
        console.log('workflow_dispatch 已触发:', DISPATCH_WORKFLOW, 'ref', DISPATCH_REF);
      } else {
        // 抛出让这次 cron 在 Cloudflare dashboard 标记为失败，便于发现
        console.error('workflow_dispatch 失败:', r.status, r.detail);
        throw new Error(`dispatch failed: HTTP ${r.status} ${r.detail.slice(0, 200)}`);
      }
    })());

    // 2) 抓取状态机：每 2 分钟一次的 cron 只在这里干活
    ctx.waitUntil(runMarketSync(env));
  },
};

/** 抓取驱动：读活跃任务 → 抓一片 → upsert → 推进游标。非交易时段秒退。 */
async function runMarketSync(env) {
  // 开关：显式设为 'false' 才关闭（默认开启）。想暂停抓取只需
  // `wrangler secret put SYNC_ENABLED` 写入 false，无需改代码。
  if (env.SYNC_ENABLED === 'false') return;
  const bj = beijingNow();
  const job = await activeJob(env.DB);

  // 15:35 之后自动建当天任务（幂等），无需外部触发
  if (!job && isTradingWindow(bj)) {
    const codes = await listSymbols(env.DB);
    if (codes.length) await ensureJob(env.DB, bj.date, codes.length);
  }
  const active = job || await activeJob(env.DB);
  if (!shouldRun(active, bj)) return;

  const codes = await listSymbols(env.DB);
  // 目标日期以「任务 id」为准，不用 bj.date：隔夜未完成的 job 的 id 是更早的日期，
  // 用今天去抓却推进昨天的游标会写错数据。
  const targetDate = active.id;
  const { slice } = planChunk(codes, active.cursor);
  if (!slice.length) {
    await updateJob(env.DB, { ...active, status: 'done', cursor: active.total });
    return;
  }

  const rows = [];
  const failed = [];
  // 并发池 6（Free 计划同时连接上限）
  for (let i = 0; i < slice.length; i += 6) {
    const batch = slice.slice(i, i + 6);
    const results = await Promise.all(batch.map(async (code) => {
      try {
        const { rows: bars } = await fetchBars(code, targetDate);
        return { code, rows: pickBars(bars, targetDate) };
      } catch {
        return { code, rows: [] };
      }
    }));
    for (const r of results) {
      if (r.rows.length) rows.push(...r.rows);
      else failed.push(r.code);
    }
  }

  await upsertPrices(env.DB, rows);
  const next = advanceJob(active, {
    advance: slice.length,
    failedCodes: failed,
    hardFailure: rows.length === 0 ? 'no rows from any source' : null,
  });
  await updateJob(env.DB, next);
  console.log(`sync ${active.id}: cursor=${next.cursor}/${active.total} rows=${rows.length} failed=${failed.length}`);
}
