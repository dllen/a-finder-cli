// D1 读写：market_symbols / market_prices / sync_jobs。
// 所有 SQL 集中在这里，便于测试与审查。

/** 全部成分股代码（按代码升序，游标分片依赖这个稳定顺序） */
export async function listSymbols(DB) {
  const { results } = await DB.prepare(
    'SELECT code FROM market_symbols ORDER BY code',
  ).all();
  return (results || []).map((r) => r.code);
}

/** 建当天任务；INSERT OR IGNORE 保证 cron 重复触发时不会重置进度 */
export async function ensureJob(DB, id, total) {
  await DB.prepare(
    'INSERT OR IGNORE INTO sync_jobs (id, status, cursor, total, attempts, updated_at) ' +
    "VALUES (?, 'pending', 0, ?, 0, datetime('now'))",
  ).bind(id, total).run();
}

/** 当前活跃任务（没跑到 done/failed 的那条）；没有则返回 null */
export async function activeJob(DB) {
  return DB.prepare(
    'SELECT id, status, cursor, total, attempts, note FROM sync_jobs ' +
    "WHERE status IN ('pending','running') ORDER BY id DESC LIMIT 1",
  ).first();
}

/** 写回游标/状态/失败计数 */
export async function updateJob(DB, job) {
  await DB.prepare(
    "UPDATE sync_jobs SET status=?, cursor=?, attempts=?, note=?, updated_at=datetime('now') " +
    'WHERE id=?',
  ).bind(job.status, job.cursor, job.attempts || 0, job.note || null, job.id).run();
}

// 主键 (code, trade_date) 冲突即覆盖：重抓同一天是常态（补数/修数据），
// 用 batch 一次提交，Free 计划下也只需 1 次 D1 写往返。
const UPSERT = 'INSERT INTO market_prices ' +
  '(code, trade_date, open, close, high, low, volume, amount, turnover, pct_change) ' +
  'VALUES (?,?,?,?,?,?,?,?,?,?) ' +
  'ON CONFLICT(code, trade_date) DO UPDATE SET ' +
  'open=excluded.open, close=excluded.close, high=excluded.high, low=excluded.low, ' +
  'volume=excluded.volume, amount=excluded.amount, turnover=excluded.turnover, ' +
  'pct_change=excluded.pct_change';

export async function upsertPrices(DB, rows, batchSize = 200) {
  if (!rows.length) return;
  // 回填一轮可能带几千行，单次 batch 过大会失败；按 batchSize 拆成多次 batch()。
  // 每次 batch() 只计 1 次查询（Free 计划上限 50 次/调用），4000 行 = 20 次。
  for (let i = 0; i < rows.length; i += batchSize) {
    const stmts = rows.slice(i, i + batchSize).map((r) => DB.prepare(UPSERT).bind(
      r.code, r.trade_date, r.open, r.close, r.high, r.low,
      r.volume, r.amount, r.turnover, r.pct_change,
    ));
    await DB.batch(stmts);
  }
}


const UPSERT_SYMBOL = 'INSERT INTO market_symbols (code, name, updated_at) ' +
  "VALUES (?, ?, datetime('now')) " +
  'ON CONFLICT(code) DO UPDATE SET name=excluded.name, updated_at=excluded.updated_at';

export async function upsertSymbols(DB, symbols) {
  if (!symbols.length) return;
  await DB.batch(symbols.map((s) => DB.prepare(UPSERT_SYMBOL).bind(s.code, s.name || null)));
}

/** 覆盖情况：最新交易日、该日代码数、总交易日数（健康检查用） */
export async function coverageStats(DB) {
  const row = await DB.prepare(
    'SELECT MAX(trade_date) AS latest, ' +
    'COUNT(DISTINCT trade_date) AS days FROM market_prices',
  ).first();
  const codes = await DB.prepare(
    'SELECT COUNT(DISTINCT code) AS codes FROM market_prices WHERE trade_date=?',
  ).bind(row?.latest).first();
  return { latest: row?.latest || null, codes: codes?.codes || 0, days: row?.days || 0 };
}

/** since 之后有数据的交易日（新→旧），给 GH 侧增量取数用 */
export async function datesSince(DB, since) {
  const { results } = await DB.prepare(
    'SELECT DISTINCT trade_date FROM market_prices WHERE trade_date >= ? ' +
    'ORDER BY trade_date DESC',
  ).bind(since).all();
  return (results || []).map((r) => r.trade_date);
}

/** 某交易日的全部行，按代码排序 */
export async function rowsForDate(DB, date) {
  const { results } = await DB.prepare(
    'SELECT code, trade_date, open, close, high, low, volume, amount, turnover, pct_change ' +
    'FROM market_prices WHERE trade_date=? ORDER BY code',
  ).bind(date).all();
  return results || [];
}

/** 最新交易日在 market_symbols 里却没有行情的代码（停牌/抓取失败的诊断用） */
export async function missingCodes(DB, date) {
  if (!date) return [];
  const { results } = await DB.prepare(
    'SELECT s.code FROM market_symbols s ' +
    'LEFT JOIN market_prices p ON p.code = s.code AND p.trade_date = ? ' +
    'WHERE p.code IS NULL ORDER BY s.code',
  ).bind(date).all();
  return (results || []).map((r) => r.code);
}
