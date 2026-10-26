// 抓取状态机单测（纯函数，不碰网络与 D1）
import { test } from 'node:test';
import assert from 'node:assert';
import {
  CHUNK_SIZE, beijingNow, isTradingWindow, planChunk, advanceJob, shouldRun,
} from '../worker/market/jobs.js';

test('CHUNK_SIZE 在 Free 计划子请求限额内', () => {
  assert.ok(CHUNK_SIZE <= 45, '必须留出余量（限额 50）');
});

test('beijingNow 返回北京时间的字段', () => {
  const b = beijingNow(new Date('2026-10-26T07:40:00Z'));   // UTC 07:40 = 北京 15:40
  assert.equal(b.date, '2026-10-26');
  assert.equal(b.hour, 15);
  assert.equal(b.minute, 40);
  assert.equal(b.weekday, 1);        // 周一
});

test('isTradingWindow：15:35–23:00 的工作日才抓', () => {
  assert.ok(isTradingWindow({ weekday: 1, hour: 15, minute: 40 }));
  assert.ok(isTradingWindow({ weekday: 5, hour: 22, minute: 59 }));
  assert.ok(!isTradingWindow({ weekday: 1, hour: 15, minute: 30 }));  // 收盘数据未稳定
  assert.ok(!isTradingWindow({ weekday: 1, hour: 23, minute: 1 }));
  assert.ok(!isTradingWindow({ weekday: 0, hour: 16, minute: 0 }));   // 周日
  assert.ok(!isTradingWindow({ weekday: 6, hour: 16, minute: 0 }));
});

test('shouldRun：没有活跃任务就不跑；有则跑', () => {
  assert.equal(shouldRun(null, { weekday: 1, hour: 16, minute: 0 }), false);
  assert.equal(shouldRun({ status: 'done' }, { weekday: 1, hour: 16, minute: 0 }), false);
  assert.equal(shouldRun({ status: 'running' }, { weekday: 1, hour: 16, minute: 0 }), true);
  assert.equal(shouldRun({ status: 'running' }, { weekday: 1, hour: 12, minute: 0 }), false);
});

test('planChunk：按游标切出本片代码与下一游标', () => {
  const codes = Array.from({ length: 90 }, (_, i) => String(600000 + i));
  const first = planChunk(codes, 0);
  assert.equal(first.slice.length, CHUNK_SIZE);
  assert.equal(first.nextCursor, CHUNK_SIZE);
  assert.equal(first.done, false);

  const last = planChunk(codes, 80);
  assert.equal(last.slice.length, 10);
  assert.equal(last.done, true);
});

test('planChunk：游标越界视为完成且不返回代码', () => {
  const out = planChunk(['600519'], 5);
  assert.deepEqual(out.slice, []);
  assert.equal(out.done, true);
});

test('advanceJob：推进游标', () => {
  const job = { id: '2026-10-26', status: 'running', cursor: 0, total: 301, attempts: 0 };
  const next = advanceJob(job, { advance: 40, failedCodes: [] });
  assert.equal(next.cursor, 40);
  assert.equal(next.status, 'running');
  const done = advanceJob({ ...job, cursor: 280 }, { advance: 40, failedCodes: [] });
  assert.equal(done.cursor, 301);
  assert.equal(done.status, 'done');
});

test('advanceJob：整片失败累计 attempts，超过 3 次判 failed', () => {
  const job = { id: 'd', status: 'running', cursor: 0, total: 301, attempts: 2 };
  const failed = advanceJob(job, { advance: 0, failedCodes: [], hardFailure: 'source down' });
  assert.equal(failed.attempts, 3);
  assert.equal(failed.status, 'failed');
  assert.match(failed.note, /source down/);
});

test('advanceJob：个别代码失败仍推进，并记录到 note', () => {
  const job = { id: 'd', status: 'running', cursor: 0, total: 3, attempts: 0 };
  const next = advanceJob(job, { advance: 3, failedCodes: ['000001'] });
  assert.equal(next.cursor, 3);
  assert.equal(next.status, 'done');
  assert.match(next.note, /000001/);
});
