// /api/market/* 鉴权与形态单测
import { test } from 'node:test';
import assert from 'node:assert';
import { handleMarketApi } from '../worker/market/api.js';

function env(overrides = {}) {
  const calls = [];
  const DB = {
    calls,
    prepare(sql) {
      const rec = { sql, binds: [] };
      calls.push(rec);
      return {
        bind(...a) { rec.binds = a; return this; },
        async first() {
          if (/MAX\(trade_date\)/.test(sql)) {
            return { latest: '2026-10-23', days: 277, codes: 301 };
          }
          if (/COUNT\(DISTINCT code\)/.test(sql)) return { codes: 301 };
          return null;
        },
        async all() { return { results: [{ trade_date: '2026-10-23' }] }; },
        async run() { return { success: true }; },
      };
    },
    async batch(s) { return s.map(() => ({ success: true })); },
  };
  return { DB, SYNC_TOKEN: 'token-123', ...overrides };
}

test('无 token 访问需鉴权端点 → 401', async () => {
  const res = await handleMarketApi(
    new Request('https://x/api/market/dates'), env(), '/api/market/dates',
  );
  assert.equal(res.status, 401);
});

test('token 错误 → 401', async () => {
  const res = await handleMarketApi(
    new Request('https://x/api/market/day/2026-10-23', { headers: { 'X-Sync-Token': 'bad' } }),
    env(), '/api/market/day/2026-10-23',
  );
  assert.equal(res.status, 401);
});

test('health 公开（无 token 也可读聚合计数）', async () => {
  const res = await handleMarketApi(
    new Request('https://x/api/market/health'), env(), '/api/market/health',
  );
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.latest_date, '2026-10-23');
  assert.equal(body.codes_on_latest, 301);
});

test('health 返回覆盖统计', async () => {
  const res = await handleMarketApi(
    new Request('https://x/api/market/health', { headers: { 'X-Sync-Token': 'token-123' } }),
    env(), '/api/market/health',
  );
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.latest_date, '2026-10-23');
  assert.equal(body.codes_on_latest, 301);
});

test('dates 返回日期列表', async () => {
  const res = await handleMarketApi(
    new Request('https://x/api/market/dates?since=2026-10-01',
      { headers: { 'X-Sync-Token': 'token-123' } }),
    env(), '/api/market/dates',
  );
  const body = await res.json();
  assert.deepEqual(body.dates, ['2026-10-23']);
});

test('day/:date 拒绝非法日期', async () => {
  const res = await handleMarketApi(
    new Request('https://x/api/market/day/2026-13-99',
      { headers: { 'X-Sync-Token': 'token-123' } }),
    env(), '/api/market/day/2026-13-99',
  );
  assert.equal(res.status, 400);
});

test('day/:date 合法日期返回 rows 数组', async () => {
  const res = await handleMarketApi(
    new Request('https://x/api/market/day/2026-10-23',
      { headers: { 'X-Sync-Token': 'token-123' } }),
    env(), '/api/market/day/2026-10-23',
  );
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.date, '2026-10-23');
  assert.ok(Array.isArray(body.rows));
});

test('POST /api/market/symbols 批量写入成分股', async () => {
  const res = await handleMarketApi(
    new Request('https://x/api/market/symbols', {
      method: 'POST',
      headers: { 'X-Sync-Token': 'token-123', 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbols: [{ code: '600519', name: '茅台' }] }),
    }),
    env(), '/api/market/symbols',
  );
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.ok, true);
  assert.equal(body.count, 1);
});

test('POST /api/market/symbols 空数组 → 400', async () => {
  const res = await handleMarketApi(
    new Request('https://x/api/market/symbols', {
      method: 'POST',
      headers: { 'X-Sync-Token': 'token-123', 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbols: [] }),
    }),
    env(), '/api/market/symbols',
  );
  assert.equal(res.status, 400);
});

test('未知子路径 → null（交回主分发器 404）', async () => {
  const out = await handleMarketApi(
    new Request('https://x/api/market/unknown', { headers: { 'X-Sync-Token': 'token-123' } }),
    env(), '/api/market/unknown',
  );
  assert.equal(out, null);
});
