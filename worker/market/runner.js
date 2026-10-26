// 抓取驱动：从 index.js 抽出，便于 (a) cron 调用 (b) 手动 tick 诊断。
import {
  beijingNow, isTradingWindow, planChunk, shouldRun, advanceJob,
  parseJobId, chunkSizeFor,
} from './jobs.js';
import {
  listSymbols, ensureJob, activeJob, getJob, upsertPrices, updateJob,
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
export async function runMarketSync(env, opts = {}) {
  // 开关：显式设为 'false' 才关闭（默认开启）。想暂停抓取只需
  // `wrangler secret put SYNC_ENABLED` 写入 false，无需改代码。
  if (env.SYNC_ENABLED === 'false') return { skipped: 'disabled' };
  const bj = beijingNow();
  // 心跳：仅 SYNC_DEBUG=true 时写。
  // 教训：Free 计划 cron 的 CPU 预算只有 10ms，每次 tick 多一次 D1 写入就会把预算吃光，
  // 表现为「心跳写了、后面全部执行不到」（抓取游标永不推进）。
  const beat = env.SYNC_DEBUG === 'true'
    ? async (stage) => {
        try {
          await env.DB.prepare(
            "INSERT OR REPLACE INTO worker_heartbeat (name, at, note) VALUES ('cron', datetime('now'), ?)",
          ).bind(stage).run();
        } catch { /* 心跳失败不影响抓取 */ }
      }
    : async () => {};
  await beat('enter');

  // 优先级：当天的日报 > 回填。回填可能跨天（301 只 / 5 只每轮 ≈ 2 小时），
  // 若不优先日报，当天行情会被回填拖住不抓。
  let active = null;
  const todayJob = await getJob(env.DB, bj.date);
  const todayRunning = todayJob && (todayJob.status === 'pending' || todayJob.status === 'running');
  if (todayRunning) {
    active = todayJob;
  } else if (!todayJob && isTradingWindow(bj)) {
    const codes = await listSymbols(env.DB);
    if (codes.length) {
      await ensureJob(env.DB, bj.date, codes.length);
      active = await getJob(env.DB, bj.date);
    }
  }
  if (!active) active = await activeJob(env.DB);
  if (!shouldRun(active, bj)) {
    await beat(`skip:${active ? active.id + '/' + active.status : 'no-active-job'}`);
    return {
      skipped: 'no-active-job-in-window',
      beijing: bj,
      job: active ? { id: active.id, status: active.status, cursor: active.cursor } : null,
    };
  }

  await beat(`job:${active.id}:${active.cursor}`);
  const codes = await listSymbols(env.DB);
  if (opts.dry) {
    // 只报告门禁判断结果，不抓取（HTTP 有 10ms CPU 限额，重活只能靠 cron）
    return {
      dry: true, beijing: bj, job: active.id, kind: parseJobId(active.id).kind,
      cursor: active.cursor, total: active.total, codes: codes.length,
      chunkSize: chunkSizeFor(parseJobId(active.id).kind),
    };
  }
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

  await beat(`plan:${meta.kind}:${slice.length}`);
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

  await beat(`fetched:${rows.length}:failed:${failed.length}`);
  await upsertPrices(env.DB, rows);
  await beat(`written:${rows.length}`);
  const next = advanceJob(active, {
    advance: slice.length,
    failedCodes: failed,
    hardFailure: rows.length === 0 ? 'no rows from any source' : null,
  });
  await updateJob(env.DB, next);
  await beat(`advanced:${next.cursor}/${active.total}`);
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
