// 决策台持仓 CSV 导出单测（node --test）
import { test } from 'node:test';
import assert from 'node:assert';
import { csvCell, toCsv } from '../static/js/decide_export.js';

test('csvCell 转义逗号/引号/换行', () => {
  assert.equal(csvCell('普通'), '普通');
  assert.equal(csvCell('a,b'), '"a,b"');
  assert.equal(csvCell('he said "hi"'), '"he said ""hi"""');
  assert.equal(csvCell('line1\nline2'), '"line1\nline2"');
  assert.equal(csvCell(null), '');
  assert.equal(csvCell(undefined), '');
  assert.equal(csvCell(0), '0');
});

test('toCsv 带表头、kind 列、BOM 与 CRLF', () => {
  const csv = toCsv(
    [{ code: '600519', name: '贵州茅台', shares: 100, entry_price: 1300,
       stop_price: 1200, entry_date: '2026-10-20', note: null, updated_at: '2026-10-20 15:00:00' }],
    [],
  );
  assert.ok(csv.startsWith('\ufeff'), '应带 BOM（Excel 中文不乱码）');
  const lines = csv.replace('\ufeff', '').trimEnd().split('\r\n');
  assert.equal(lines.length, 2);
  assert.ok(lines[0].startsWith('kind,code,name,shares,entry_price'));
  assert.ok(lines[1].startsWith('stock,600519,贵州茅台,100'));
  assert.ok(csv.includes('\r\n'), 'CRLF 行尾');
});

test('toCsv 同时导出股票与转债，空值留空', () => {
  const csv = toCsv(
    [{ code: '600519', name: '茅台', shares: 100, entry_price: 1, stop_price: null }],
    [{ code: '113059', name: '转债甲', shares: 50, entry_price: 108.5, stop_price: null }],
  );
  const body = csv.replace('\ufeff', '');
  assert.ok(body.includes('stock,600519'));
  assert.ok(body.includes('cb,113059'));
  // stop_price 为空 → CSV 里是连续两个逗号
  assert.ok(body.includes('茅台,100,1,,'));
});

test('toCsv 空输入也产出只有表头的文件', () => {
  const csv = toCsv([], []);
  const lines = csv.replace('\ufeff', '').trimEnd().split('\r\n');
  assert.equal(lines.length, 1);
  assert.ok(lines[0].includes('code'));
});
