// 抓取状态机：全部是纯函数，便于在没有 D1/网络的条件下测试。
// 状态落在 D1 的 sync_jobs 表：status / cursor / total / attempts / note。

/** 每次调用抓多少只。Free 计划子请求上限 50，留余量给健康检查。 */
export const CHUNK_SIZE = 40;

/** 抓取时间窗（北京时间）：15:35 起（东财当日 K 线收盘后才稳定），23:00 止 */
const WINDOW_START_MIN = 15 * 60 + 35;
const WINDOW_END_MIN = 23 * 60;

/** 把任意时刻转成北京时间的日期/时分/星期（0=周日） */
export function beijingNow(now = new Date()) {
  const fmt = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false, weekday: 'short',
  });
  const parts = Object.fromEntries(
    fmt.formatToParts(now).map((p) => [p.type, p.value]),
  );
  const weekdayMap = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 };
  return {
    date: `${parts.year}-${parts.month}-${parts.day}`,
    hour: Number(parts.hour),
    minute: Number(parts.minute),
    weekday: weekdayMap[parts.weekday] ?? 0,
  };
}

export function isTradingWindow(bj) {
  if (bj.weekday === 0 || bj.weekday === 6) return false;
  const minutes = bj.hour * 60 + bj.minute;
  return minutes >= WINDOW_START_MIN && minutes < WINDOW_END_MIN;
}

/** 有活跃任务且在时间窗内才需要干活 */
export function shouldRun(job, bj) {
  if (!job) return false;
  if (job.status !== 'pending' && job.status !== 'running') return false;
  return isTradingWindow(bj);
}

/** 按游标切出本片代码；done 表示本片之后没有剩余 */
export function planChunk(codes, cursor) {
  const start = Math.min(cursor, codes.length);
  const slice = codes.slice(start, start + CHUNK_SIZE);
  const nextCursor = start + slice.length;
  return { slice, nextCursor, done: nextCursor >= codes.length };
}

function mergeNote(existing, additions) {
  const prev = existing ? String(existing) : '';
  if (!additions.length) return prev || null;
  const merged = prev ? `${prev} ${additions.join(' ')}` : additions.join(' ');
  return merged.slice(-900);   // 控制 sync_jobs.note 体积
}

/**
 * 推进任务状态。
 * advance：本片实际处理并推进的代码数（整片失败传 0）
 * failedCodes：本片内单只失败的代码
 * hardFailure：整片失败原因（数据源全挂）
 *
 * attempts 只在整片失败（hardFailure）时累加：单只失败会正常推进游标，不该算作
 * 任务级失败，否则名单末尾几只连续缺数就会把整个任务判死。累计到第 3 次整片
 * 失败即判 failed，等人工介入。
 */
export function advanceJob(job, { advance = 0, failedCodes = [], hardFailure = null } = {}) {
  const attempts = hardFailure ? (job.attempts || 0) + 1 : (job.attempts || 0);
  if (hardFailure && attempts >= 3) {
    return {
      ...job, attempts, status: 'failed',
      note: mergeNote(job.note, [`hard:${hardFailure}`]),
    };
  }
  const cursor = Math.min(job.total, (job.cursor || 0) + advance);
  const status = cursor >= job.total ? 'done' : 'running';
  return {
    ...job,
    attempts,
    cursor,
    status,
    note: mergeNote(job.note, failedCodes.map((c) => `miss:${c}`)),
  };
}
