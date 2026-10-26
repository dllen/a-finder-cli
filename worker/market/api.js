// /api/market/* ：只读导出 + 手动触发抓取。全部要求 X-Sync-Token。
import {
  datesSince, rowsForDate, coverageStats, listSymbols, ensureJob, upsertSymbols,
} from './repo.js';

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

// DATE_RE 只保证形状；这里再校验是不是真实存在的日历日期（拒绝 2026-13-99）。
function isValidDate(s) {
  if (!DATE_RE.test(s)) return false;
  const d = new Date(`${s}T00:00:00Z`);
  return !Number.isNaN(d.getTime()) && d.toISOString().slice(0, 10) === s;
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
  });
}

/**
 * 软鉴权：
 *   - 配置了 Worker secret SYNC_TOKEN → 所有端点必须带匹配的 X-Sync-Token
 *   - 未配置（默认）→ 只读端点公开（A 股日线是公开信息，个人自托管场景足够）
 *   - 写端点（symbols / sync-start）在未配置 token 时也放行，避免自托管被自己锁死
 * 想收紧：`wrangler secret put SYNC_TOKEN`，同时给 GitHub 仓库配 D1_SYNC_TOKEN（同值）。
 */
function authed(request, env) {
  if (!env.SYNC_TOKEN) return true;
  return request.headers.get('X-Sync-Token') === env.SYNC_TOKEN;
}

/** 返回 Response 表示已处理；返回 null 表示不是本模块的路由。 */
export async function handleMarketApi(request, env, path) {
  if (!path.startsWith('/api/market/') && path !== '/api/sync/start') return null;

  // health 公开：只返回聚合计数（最新日期/当日代码数），用于监控与巡检，
  // 不含任何行情明细；其余端点仍需 X-Sync-Token。
  if (path === '/api/market/health' && request.method === 'GET') {
    const stats = await coverageStats(env.DB);
    return json({
      latest_date: stats.latest,
      codes_on_latest: stats.codes,
      days: stats.days,
    });
  }

  if (!authed(request, env)) return json({ error: 'unauthorized' }, 401);

  if (path === '/api/market/dates') {
    const since = new URL(request.url).searchParams.get('since') || '1970-01-01';
    return json({ dates: await datesSince(env.DB, since) });
  }

  const dayMatch = path.match(/^\/api\/market\/day\/([^/]+)$/);
  if (dayMatch) {
    const date = dayMatch[1];
    if (!isValidDate(date)) return json({ error: 'bad date' }, 400);
    return json({ date, rows: await rowsForDate(env.DB, date) });
  }

  if (path === '/api/market/symbols' && request.method === 'POST') {
    const body = await request.json().catch(() => ({}));
    const symbols = (body.symbols || []).filter((x) => x && x.code);
    if (!symbols.length) return json({ error: 'symbols required' }, 400);
    await upsertSymbols(env.DB, symbols);
    return json({ ok: true, count: symbols.length });
  }

  if (path === '/api/sync/start' && request.method === 'POST') {
    const body = await request.json().catch(() => ({}));
    const date = body.date || new Date().toISOString().slice(0, 10);
    if (!isValidDate(date)) return json({ error: 'bad date' }, 400);
    const codes = await listSymbols(env.DB);
    await ensureJob(env.DB, date, codes.length);
    return json({ ok: true, job: { id: date, total: codes.length } });
  }

  return null;
}
