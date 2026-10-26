// 行情抓取与解析。
// Task 1 实测：东财从 Cloudflare 不可达（520/502），主源为腾讯不复权日线。
// 降级链：newfqkline(bfq) → fqkline(bfq) → 放弃（返回 none）。
// 一律用 bfq 不复权：Python 侧 daily_prices 存的是东财 fqt=0 口径，写复权价会让历史序列断裂。
const UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 ' +
  '(KHTML, like Gecko) Chrome/124.0 Safari/537.36';
const BASE = 'https://web.ifzq.gtimg.cn/appstock/app';

export function tencentSymbol(code) {
  return /^(6|688)/.test(code) ? `sh${code}` : `sz${code}`;
}

const num = (v) => {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
};

function nodeOf(payload, code) {
  return payload?.data?.[tencentSymbol(code)] || null;
}

/**
 * newfqkline(bfq) 行格式（11 字段，不复权）：
 * [日期, 开, 收, 高, 低, 量(手), {}, 换手率%, 成交额(万元), 0.00, 0.00]
 */
export function parseNewfq(payload, code) {
  const node = nodeOf(payload, code);
  const bars = node?.day || node?.qfqday;
  if (!Array.isArray(bars)) return [];
  const rows = [];
  for (const b of bars) {
    if (!Array.isArray(b) || b.length < 6) continue;
    rows.push({
      code,
      trade_date: String(b[0]),
      open: num(b[1]),
      close: num(b[2]),
      high: num(b[3]),
      low: num(b[4]),
      volume: num(b[5]),
      turnover: b.length > 7 ? num(b[7]) : 0,
      amount: b.length > 8 ? num(b[8]) * 10000 : 0,   // 万元 → 元
      pct_change: null,
    });
  }
  return rows;
}

/** fqkline 降级源：只有 6 字段 [日期, 开, 收, 高, 低, 量(手)] */
export function parseFq(payload, code) {
  const node = nodeOf(payload, code);
  const bars = node?.day || node?.qfqday;
  if (!Array.isArray(bars)) return [];
  const rows = [];
  for (const b of bars) {
    if (!Array.isArray(b) || b.length < 6) continue;
    rows.push({
      code,
      trade_date: String(b[0]),
      open: num(b[1]),
      close: num(b[2]),
      high: num(b[3]),
      low: num(b[4]),
      volume: num(b[5]),
      turnover: 0,
      amount: 0,
      pct_change: null,
    });
  }
  return rows;
}

/** 只保留目标交易日的行（窗口抓多天是为了拿到前收盘价并兼容节假日） */
export function pickBars(rows, targetDate) {
  return rows.filter((r) => r.trade_date === targetDate);
}

/** 用前一行收盘价补 pct_change；窗口首行无法计算，保持 null 交给 GH 补 */
export function computePctChange(rows) {
  return rows.map((r, i) => {
    if (r.pct_change !== null && r.pct_change !== undefined) return r;
    if (i === 0) return { ...r, pct_change: null };
    const prev = rows[i - 1];
    if (!prev.close) return { ...r, pct_change: null };
    return { ...r, pct_change: (r.close - prev.close) / prev.close * 100 };
  });
}

// 网络异常一律降级为 null：调用方按「这个源没数据」继续走降级链，不抛错。
async function getJson(url, referer) {
  let resp;
  try {
    resp = await fetch(url, {
      headers: { 'User-Agent': UA, ...(referer ? { Referer: referer } : {}) },
    });
  } catch {
    return null;
  }
  if (!resp.ok) return null;
  try {
    return await resp.json();
  } catch {
    return null;
  }
}

function dayOffset(dateStr, days) {
  const d = new Date(`${dateStr}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/**
 * 抓一只股票在 targetDate 的日线（不复权）。
 * 抓 8 天窗口：既拿到前收盘价算 pct_change，也能覆盖长假后首日。
 * 返回 { source, rows }，rows 已只含目标日并已补齐 pct_change。
 */
export async function fetchBars(code, targetDate) {
  const symbol = tencentSymbol(code);
  const beg = dayOffset(targetDate, -8);

  // 主源：newfqkline + bfq（含换手率/成交额）
  const primary = `${BASE}/newfqkline/get?param=${symbol},day,${beg},${targetDate},10,bfq`;
  let rows = parseNewfq(await getJson(primary, 'https://gu.qq.com/'), code);
  if (rows.length) {
    return { source: 'tencent-newfq', rows: pickBars(computePctChange(rows), targetDate) };
  }

  // 降级源：fqkline + bfq（仅 OHLCV）
  const fallback = `${BASE}/fqkline/get?param=${symbol},day,${beg},${targetDate},10,bfq`;
  rows = parseFq(await getJson(fallback, 'https://gu.qq.com/'), code);
  if (rows.length) {
    return { source: 'tencent-fq', rows: pickBars(computePctChange(rows), targetDate) };
  }

  return { source: 'none', rows: [] };
}

/**
 * 回填用：抓一只股票在 [beg, end] 区间内的全部日线（不复权）。
 * 与 fetchBars 的区别：不过滤目标日，返回区间内每一行，并补齐 pct_change。
 * 单只一年约 250 行、20KB，调用方必须控制每轮抓多少只（Free 计划 CPU 10ms）。
 */
export async function fetchRange(code, beg, end) {
  const symbol = tencentSymbol(code);
  // 640 是腾讯该接口的单次根数上限（传 800 会返回空/错误）
  const url = `${BASE}/newfqkline/get?param=${symbol},day,${beg},${end},640,bfq`;
  const rows = parseNewfq(await getJson(url, 'https://gu.qq.com/'), code);
  if (rows.length) {
    return { source: 'tencent-newfq', rows: computePctChange(rows) };
  }
  const fb = `${BASE}/fqkline/get?param=${symbol},day,${beg},${end},640,bfq`;
  const rows2 = parseFq(await getJson(fb, 'https://gu.qq.com/'), code);
  return { source: rows2.length ? 'tencent-fq' : 'none', rows: computePctChange(rows2) };
}
