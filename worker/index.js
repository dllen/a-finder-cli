// Cloudflare Worker：10w 决策台持仓的线上读写。
// 读公开（首屏渲染需要），写需密码登录后的 HttpOnly Cookie。
// 密码与签名密钥通过 `wrangler secret put DECIDE_PASSWORD / DECIDE_SECRET` 注入。
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
  const token = parseCookie(request.headers.get('Cookie'), COOKIE_NAME);
  if (!token) return false;
  return (await verifyToken(token, env.DECIDE_SECRET)) !== null;
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

    // ---- health ----
    if (path === '/api/decide/health' && method === 'GET') {
      let d1 = 'ok';
      try {
        await env.DB.prepare('SELECT 1').first();
      } catch (e) {
        d1 = 'error: ' + (e && e.message ? e.message : e);
      }
      return json(
        { ok: d1 === 'ok', d1, authed: await isAuthed(request, env) },
        200, origin,
      );
    }

    // ---- login ----
    if (path === '/api/decide/login' && method === 'POST') {
      const body = await request.json().catch(() => ({}));
      if (!env.DECIDE_PASSWORD || body.password !== env.DECIDE_PASSWORD) {
        return json({ error: '密码错误' }, 401, origin);
      }
      const token = await signToken(
        { exp: Date.now() + MAX_AGE * 1000 }, env.DECIDE_SECRET,
      );
      return json({ ok: true }, 200, origin, {
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

    return json({ error: 'not found' }, 404, origin);
  },
};
