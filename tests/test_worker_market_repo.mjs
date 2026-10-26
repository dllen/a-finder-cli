// D1 读写层单测：用假 DB 捕获 prepare/bind/run 的调用形态
import { test } from 'node:test';
import assert from 'node:assert';
import {
  listSymbols, ensureJob, activeJob, upsertPrices, upsertSymbols,
  updateJob, coverageStats,
} from '../worker/market/repo.js';

function fakeDB({ first = null, all = [] } = {}) {
  const calls = [];
  return {
    calls,
    prepare(sql) {
      const rec = { sql, binds: [] };
      calls.push(rec);
      return {
        bind(...args) { rec.binds = args; return this; },
        async first() { return first; },
        async all() { return { results: all }; },
        async run() { return { success: true }; },
      };
    },
    async batch(stmts) {
      calls.push({ sql: 'BATCH', count: stmts.length });
      return stmts.map(() => ({ success: true }));
    },
  };
}

test('listSymbols 按代码排序取回', async () => {
  const DB = fakeDB({ all: [{ code: '000001' }, { code: '600519' }] });
  const codes = await listSymbols(DB);
  assert.deepEqual(codes, ['000001', '600519']);
  assert.match(DB.calls[0].sql, /FROM market_symbols/);
});

test('ensureJob 用 INSERT OR IGNORE 保证幂等', async () => {
  const DB = fakeDB();
  await ensureJob(DB, '2026-10-26', 301);
  assert.match(DB.calls[0].sql, /INSERT OR IGNORE INTO sync_jobs/);
  assert.deepEqual(DB.calls[0].binds, ['2026-10-26', 301]);
});

test('activeJob 只找 pending/running 的最新一条', async () => {
  const DB = fakeDB({ first: { id: '2026-10-26', status: 'running', cursor: 40, total: 301 } });
  const job = await activeJob(DB);
  assert.equal(job.cursor, 40);
  assert.match(DB.calls[0].sql, /status IN \('pending','running'\)/);
});

test('upsertPrices 一次 batch 完成，主键冲突即更新', async () => {
  const DB = fakeDB();
  const rows = [
    { code: '600519', trade_date: '2026-10-23', open: 1, close: 2, high: 3, low: 1,
      volume: 10, amount: 100, turnover: 0.5, pct_change: 1.2 },
    { code: '000001', trade_date: '2026-10-23', open: 1, close: 2, high: 3, low: 1,
      volume: 20, amount: 200, turnover: 0.7, pct_change: -0.3 },
  ];
  await upsertPrices(DB, rows);
  const batch = DB.calls.find((c) => c.sql === 'BATCH');
  assert.equal(batch.count, rows.length);
  const ins = DB.calls.filter((c) => /INSERT INTO market_prices/.test(c.sql));
  assert.equal(ins.length, rows.length);
  assert.match(ins[0].sql, /ON CONFLICT\(code, trade_date\) DO UPDATE/);
  assert.equal(ins[0].binds.length, 10);
});

test('upsertPrices 空数组不发 SQL', async () => {
  const DB = fakeDB();
  await upsertPrices(DB, []);
  assert.equal(DB.calls.length, 0);
});

test('updateJob 写回游标与状态', async () => {
  const DB = fakeDB();
  await updateJob(DB, { id: '2026-10-26', status: 'running', cursor: 80, attempts: 0, note: null });
  const call = DB.calls[0];
  assert.match(call.sql, /UPDATE sync_jobs/);
  assert.equal(call.binds[0], 'running');
  assert.equal(call.binds[1], 80);
});

test('upsertSymbols 批量写成分股', async () => {
  const DB = fakeDB();
  await upsertSymbols(DB, [{ code: '600519', name: '茅台' }]);
  const batch = DB.calls.find((c) => c.sql === 'BATCH');
  assert.equal(batch.count, 1);
  const ins = DB.calls.find((c) => /INSERT INTO market_symbols/.test(c.sql));
  assert.match(ins.sql, /ON CONFLICT\(code\) DO UPDATE/);
  assert.deepEqual(ins.binds, ['600519', '茅台']);
});

test('coverageStats 返回最新日期与当日代码数', async () => {
  const DB = fakeDB({ first: { latest: '2026-10-23', codes: 301, days: 277 } });
  const stats = await coverageStats(DB);
  assert.deepEqual(stats, { latest: '2026-10-23', codes: 301, days: 277 });
});
