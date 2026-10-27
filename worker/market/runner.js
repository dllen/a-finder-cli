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
 * 跑一轮抓取。cron 与 /api/market/tick 共用，返回值是诊断信息。
 *
 * 关于 CPU 预算（Free 计划 cron 只有 10ms）：这个函数里每一次 await 都在花钱。
 * 心跳这类"排查用"的写入必须靠 SYNC_DEBUG 开关，默认不写 —— 曾经因为每轮写心跳
 * 导致预算耗尽、抓取执行不到 updateJob（表现：游标永不推进）。
 */
export async function runMarketSync(env, opts = {}) {
  const bj = beijingNow();
  const debug = env.SYNC_DEBUG === 'true';
  const beat = async (stage) => {
    if (!debug) return;
    try {
      await env.DB.prepare(
        "INSERT OR REPLACE INTO worker_heartbeat (name, at, note) VALUES ('cron', datetime('now'), ?)",
      ).bind(stage).run();
    } catch { /* 心跳失败不影响抓取 */ }
  };

  try {
    return await runOnce(env, bj, opts, beat);
  } catch (err) {
    const msg = String(err && err.stack ? err.stack : err);
    await beat(`error:${msg.slice(0, 160)}`);
    console.error('runMarketSync 未捕获异常:', msg.slice(0, 400));
    return { error: msg.slice(0, 400) };
  }
}

async function runOnce(env, bj, opts, beat) {
  // 开关：显式设为 'false' 才关闭（默认开启）
  if (env.SYNC_ENABLED === 'false') return { skipped: 'disabled' };

  // 优先级：当天日报 > 回填。回填可能跨夜（301 只 / 2 只每轮），
  // 若不优先日报，当天行情会被回填拖住不抓。
  let active = null;
  const todayJob = await getJob(env.DB, bj.date);
  await beat(`today:${todayJob ? todayJob.status : 'none'}`);

  if (todayJob && (todayJob.status === 'pending' || todayJob.status === 'running')) {
    active = todayJob;
  } else if (!todayJob && isTradingWindow(bj)) {
    const codes = await listSymbols(env.DB);
    if (codes.length) {
      await ensureJob(env.DB, bj.date, codes.length);
      active = await getJob(env.DB, bj.date);
    }
  }
  if (!active) active = await activeJob(env.DB);
  await beat(`active:${active ? active.id + '/' + active.status : 'none'}`);

  if (!shouldRun(active, bj)) {
    // 每分钟一条轻量日志，配合 observability 可确认 cron 活着
    console.log('tick: no runnable job', active ? `${active.id}/${active.status}` : 'none');
    return {
      skipped: 'no-runnable-job',
      beijing: bj,
      job: active ? { id: active.id, status: active.status, cursor: active.cursor } : null,
    };
  }

  const codes = await listSymbols(env.DB);
  const meta = parseJobId(active.id);
  const size = chunkSizeFor(meta.kind);

  if (opts.dry) {
    return {
      dry: true, beijing: bj, job: active.id, kind: meta.kind,
      cursor: active.cursor, total: active.total, codes: codes.length, chunkSize: size,
    };
  }

  // 目标日期以「任务 id」为准：隔夜未完成的任务 id 是更早的日期，
  // 用今天去抓却推进旧任务的游标会写错数据。
  const targetDate = meta.date;
  const { slice } = planChunk(codes, active.cursor, size);
  if (!slice.length) {
    await updateJob(env.DB, { ...active, status: 'done', cursor: active.total });
    console.log(`sync ${active.id}: 已完成（无剩余代码）`);
    return { finished: true, job: active.id };
  }

  const rows = [];
  const failed = [];
  // 并发池 6（Free 计划同时连接上限）
  for (let i = 0; i < slice.length; i += 6) {
    const batch = slice.slice(i, i + 6);
    const results = await Promise.all(batch.map(async (code) => {
      try {
        if (meta.kind === 'backfill') {
          const beg = dayOffsetStr(targetDate, -meta.days);
          const { rows: bars } = await fetchRange(code, beg, targetDate);
          return { code, rows: bars };
        }
        const { rows: bars } = await fetchBars(code, targetDate);
        return { code, rows: pickBars(bars, targetDate) };
      } catch (e) {
        console.error(`fetch ${code} 失败:`, String(e).slice(0, 160));
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
  const next = advanceJob(active, {
    advance: slice.length,
    failedCodes: failed,
    hardFailure: rows.length === 0 ? 'no rows from any source' : null,
  });
  await updateJob(env.DB, next);
  console.log(`sync ${active.id}: cursor=${next.cursor}/${active.total} rows=${rows.length} failed=${failed.length} status=${next.status}`);
  return {
    job: active.id, kind: meta.kind, cursor: next.cursor, total: active.total,
    rows: rows.length, failed: failed.length, status: next.status,
  };
}
