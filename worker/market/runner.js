// 抓取驱动：从 index.js 抽出，便于 (a) cron 调用 (b) 手动 tick 诊断。
import {
  beijingNow, isTradingWindow, planChunk, shouldRun, advanceJob,
  parseJobId, chunkSizeFor,
} from './jobs.js';
import {
  listSymbols, ensureJob, activeJob, upsertPrices, updateJob,
} from './repo.js';
import { fetchBars, fetchRange, pickBars } from './sources.js';

/** YYYY-MM-DD 平移若干天（回填窗口起点） */
function dayOffsetStr(dateStr, days) {
  const d = new Date(`${dateStr}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/**
 * 跑一轮抓取。返回诊断信息，便于 /api/market/tick 与 cron 共用。
 * 非交易时段、无活跃任务、开关关闭都直接返回 reason。
 */
export async function runMarketSync(env) {
  // 开关：显式设为 'false' 才关闭（默认开启）。想暂停抓取只需
  // `wrangler secret put SYNC_ENABLED` 写入 false，无需改代码。
  if (env.SYNC_ENABLED === 'false') return { skipped: 'disabled' };
  const bj = beijingNow();
  const job = await activeJob(env.DB);

  // 15:35 之后自动建当天任务（幂等），无需外部触发
  if (!job && isTradingWindow(bj)) {
    const codes = await listSymbols(env.DB);
    if (codes.length) await ensureJob(env.DB, bj.date, codes.length);
  }
  const active = job || await activeJob(env.DB);
  if (!shouldRun(active, bj)) {
    return {
      skipped: 'no-active-job-in-window',
      beijing: bj,
      job: active ? { id: active.id, status: active.status, cursor: active.cursor } : null,
    };
  }

  const codes = await listSymbols(env.DB);
  // 目标日期以「任务 id」为准，不用 bj.date：隔夜未完成的 job 的 id 是更早的日期，
  // 用今天去抓却推进昨天的游标会写错数据。
  const meta = parseJobId(active.id);
  const targetDate = meta.date;
  const size = chunkSizeFor(meta.kind);
  const { slice } = planChunk(codes, active.cursor, size);
  if (!slice.length) {
    await updateJob(env.DB, { ...active, status: 'done', cursor: active.total });
    return { finished: true, job: active.id };
  }

  const rows = [];
  const failed = [];
  try {
  // 并发池 6（Free 计划同时连接上限）
  for (let i = 0; i < slice.length; i += 6) {
    const batch = slice.slice(i, i + 6);
    const results = await Promise.all(batch.map(async (code) => {
      try {
        if (meta.kind === 'backfill') {
          // 回填：抓最近 meta.days 天的全部行（每轮只 6 只，避免 CPU 超限）
          const beg = dayOffsetStr(targetDate, -meta.days);
          const { rows: bars } = await fetchRange(code, beg, targetDate);
          return { code, rows: bars };
        }
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
  return {
    job: active.id, kind: meta.kind, cursor: next.cursor, total: active.total,
    rows: rows.length, failed: failed.length, status: next.status,
  };
  } catch (err) {
    // 任何未捕获错误都写回任务，否则任务会静默卡在 running（本次回填就是这样卡的）
    const failedState = advanceJob(active, {
      advance: 0, failedCodes: [], hardFailure: String(err).slice(0, 200),
    });
    await updateJob(env.DB, { ...failedState, status: failedState.status === 'done' ? 'running' : failedState.status });
    console.error(`sync ${active.id} 异常:`, String(err).slice(0, 300));
    return { job: active.id, error: String(err).slice(0, 300), attempts: failedState.attempts };
  }
}
