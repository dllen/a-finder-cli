// Worker 行情解析单测（node --test）
import { test } from 'node:test';
import assert from 'node:assert';
import { readFileSync } from 'node:fs';
import {
  parseNewfq, parseFq, pickBars, computePctChange, tencentSymbol, fetchBars,
} from '../worker/market/sources.js';

const newfq = JSON.parse(readFileSync(
  new URL('./fixtures/tx_newfq_bfq_600519.json', import.meta.url)));
const fq = JSON.parse(readFileSync(
  new URL('./fixtures/tx_fq_bfq_600519.json', import.meta.url)));

// 注意：newfqkline 接口会忽略起始日期、按 count=10 返回最近 10 根日线，
// 所以真实 fixture 是 10 行（2026-10-12 ~ 2026-10-23），不是计划里写的 5 行。
test('parseNewfq 映射 11 字段：OHLCV + 换手率 + 成交额（万元→元）', () => {
  const rows = parseNewfq(newfq, '600519');
  assert.equal(rows.length, 10);
  const r = rows.find((x) => x.trade_date === '2026-10-23');
  assert.ok(r, '应含 2026-10-23');
  assert.equal(r.open, 1311.84);
  assert.equal(r.close, 1335.0);
  assert.equal(r.high, 1343.88);
  assert.equal(r.low, 1310.0);
  assert.equal(r.volume, 57686);            // 单位：手
  assert.equal(r.turnover, 0.46);           // 换手率 %（10-15 那行，见下一条）
});

test('parseNewfq 成交额按万元换算成元', () => {
  const rows = parseNewfq(newfq, '600519');
  const r = rows.find((x) => x.trade_date === '2026-10-12');
  assert.equal(r.amount, 404285.76 * 10000);
  assert.equal(r.turnover, 0.25);
});

test('parseFq 降级源只有 6 字段：amount/turnover 记 0', () => {
  const rows = parseFq(fq, '600519');
  assert.equal(rows.length, 5);
  const r = rows[0];
  assert.equal(r.amount, 0);
  assert.equal(r.turnover, 0);
  assert.equal(typeof r.close, 'number');
});

test('parseNewfq 无数据返回空数组', () => {
  assert.deepEqual(parseNewfq({ code: 0, data: null }, '600519'), []);
  assert.deepEqual(parseNewfq({}, '600519'), []);
  assert.deepEqual(parseFq({}, '600519'), []);
});

test('pickBars 只保留目标交易日', () => {
  const rows = parseNewfq(newfq, '600519');
  const picked = pickBars(rows, '2026-10-23');
  assert.equal(picked.length, 1);
  assert.equal(picked[0].trade_date, '2026-10-23');
  assert.deepEqual(pickBars(rows, '1999-01-01'), []);
});

test('computePctChange 用窗口内前一行收盘价补算', () => {
  const rows = parseNewfq(newfq, '600519');
  const filled = computePctChange(rows);
  const target = filled.find((r) => r.trade_date === '2026-10-23');
  const prev = filled.find((r) => r.trade_date === '2026-10-22');
  const expected = (target.close - prev.close) / prev.close * 100;
  assert.ok(Math.abs(target.pct_change - expected) < 1e-9);
  // 窗口首行没有前值 → 保持 null
  assert.equal(filled[0].pct_change, null);
});

test('tencentSymbol 沪市/深市/科创板前缀', () => {
  assert.equal(tencentSymbol('600519'), 'sh600519');
  assert.equal(tencentSymbol('688981'), 'sh688981');
  assert.equal(tencentSymbol('000001'), 'sz000001');
  assert.equal(tencentSymbol('300750'), 'sz300750');
});

test('fetchBars 在网络不可用时返回 none 且不抛错', async () => {
  const orig = globalThis.fetch;
  globalThis.fetch = async () => { throw new Error('offline'); };
  try {
    const out = await fetchBars('600519', '2026-10-23');
    assert.equal(out.source, 'none');
    assert.deepEqual(out.rows, []);
  } finally {
    globalThis.fetch = orig;
  }
});
