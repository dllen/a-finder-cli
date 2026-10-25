// 数据源抽象：API 模式（Flask）与静态模式（gh-pages JSON）共用。
// window.APP_MODE / window.DATA_PREFIX 由 base.html 注入。

export function isStatic() { return window.APP_MODE === 'static'; }
function dsPath(file) { return (window.DATA_PREFIX || '') + file; }

async function getJSON(url, params) {
  const qs = params ? '?' + new URLSearchParams(params).toString() : '';
  const resp = await fetch(url + qs);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

export function fetchPicks(date) {
  return isStatic()
    ? getJSON(dsPath('data/picks-' + date + '.json'))
    : getJSON('/api/picks', { date });
}

export function fetchDates() {
  return isStatic()
    ? getJSON(dsPath('data/dates.json'))
    : getJSON('/api/dates');
}

export function fetchPlanDates() {
  return isStatic()
    ? getJSON(dsPath('data/plan-dates.json'))
    : getJSON('/api/plan/dates');
}

export function fetchPlan(date, includeFailed) {
  return isStatic()
    ? getJSON(dsPath('data/plan-' + date + '.json'))
    : getJSON('/api/plan/' + date, { include_failed: includeFailed ? '1' : '0' });
}

export function fetchDashboard() {
  return isStatic()
    ? getJSON(dsPath('data/dashboard.json'))
    : getJSON('/api/dashboard');
}

export function fetchHoldings() {
  return isStatic()
    ? getJSON(dsPath('data/holdings.json'))
    : getJSON('/api/holdings');
}

export function fetchPortfolioSummary(date, strategy) {
  return isStatic()
    ? getJSON(dsPath('data/portfolio/summary.json'))
    : getJSON('/api/portfolio/summary', { date, strategy });
}

export function fetchPortfolioDetail(label, date, strategy) {
  return isStatic()
    ? getJSON(dsPath('data/portfolio/' + label + '.json'))
    : getJSON('/api/portfolio/' + encodeURIComponent(label), { date, strategy });
}

export function fetchStats() {
  return isStatic()
    ? getJSON(dsPath('data/stats.json'))
    : getJSON('/api/stats');
}

export async function postRefresh(sync) {
  const resp = await fetch('/api/refresh', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sync }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw { status: resp.status, ...err };
  }
  return resp.json();
}

export async function postPlanBuild(params) {
  const resp = await fetch('/api/plan/build', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw { status: resp.status, ...err };
  }
  return resp.json();
}

export async function pollJob(jobId, onProgress, onComplete, onError) {
  try {
    const data = await getJSON('/api/jobs/' + jobId);
    onProgress(data);
    if (data.status === 'pending' || data.status === 'running') {
      setTimeout(() => pollJob(jobId, onProgress, onComplete, onError), 1000);
    } else if (data.status === 'done') {
      onComplete(data);
    } else {
      onError(data);
    }
  } catch (e) {
    onError({ message: '查询任务状态失败' });
  }
}

export function picksHref() { return isStatic() ? 'index.html' : '/'; }
export function planHref() { return isStatic() ? 'plan.html' : '/plan'; }
export function cbHref() { return isStatic() ? 'cb.html' : '/cb'; }

// ---------------------------------------------------------------------------
// 可转债（Convertible Bond）
// ---------------------------------------------------------------------------

export function fetchCbPicks(date) {
  return isStatic()
    ? getJSON(dsPath('data/cb-picks-' + date + '.json'))
    : getJSON('/api/cb/picks', { date });
}

export function fetchCbDates() {
  return isStatic()
    ? getJSON(dsPath('data/cb-dates.json'))
    : getJSON('/api/cb/dates');
}

export function fetchCbPositions() {
  return isStatic()
    ? getJSON(dsPath('data/cb-positions.json'))
    : getJSON('/api/cb/positions');
}

export function fetchCbAlerts() {
  return isStatic()
    ? getJSON(dsPath('data/cb-alerts.json'))
    : getJSON('/api/cb/alerts');
}

// 静态模式下隐藏写入控件、修正导航链接
if (isStatic()) {
  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.write-control').forEach(el => el.style.display = 'none');
    const picksLink = document.querySelector('a[data-nav="picks"]');
    if (picksLink) picksLink.href = 'index.html';
    const planLink = document.querySelector('a[data-nav="plan"]');
    if (planLink) planLink.href = 'plan.html';
    const cbLink = document.querySelector('a[data-nav="cb"]');
    if (cbLink) cbLink.href = 'cb.html';
  });
}

// ---------------------------------------------------------------------------
// 10w 决策台
// ---------------------------------------------------------------------------

export function fetchDecideDates() {
  return isStatic()
    ? getJSON(dsPath('data/decide-dates.json'))
    : getJSON('/api/decide/plan');
}

export async function fetchDecidePlan(date) {
  if (!isStatic()) {
    return getJSON('/api/decide/plan', { date });
  }
  // 静态站：没有 date 时先从 decide-dates.json 取最新，避免拼出 decide-undefined.json
  let d = date;
  if (!d) {
    const datesRes = await getJSON(dsPath('data/decide-dates.json'));
    d = (datesRes && datesRes.dates && datesRes.dates[0]) || '';
  }
  if (!d) return { date: '', rows: [], skipped: [], dates: [] };
  const plan = await getJSON(dsPath('data/decide-' + d + '.json'));
  const datesRes = await getJSON(dsPath('data/decide-dates.json')).catch(() => ({ dates: [d] }));
  return Object.assign({ dates: datesRes.dates || [d] }, plan);
}

export function fetchDecidePositions() {
  return isStatic()
    ? getJSON(dsPath('data/decide-positions.json'))
    : getJSON('/api/decide/positions');
}

export function fetchDecideComparison(date) {
  return isStatic()
    ? getJSON(dsPath('data/decide-' + date + '.json'))
    : getJSON('/api/decide/comparison', { date });
}

// ---------------------------------------------------------------------------
// Cloudflare Worker（持仓写入；D1 是唯一真相源）
// ---------------------------------------------------------------------------

// 线上静态站：同源（Worker 挂在 ycu.space/api/*）
// 本地 Flask：跨域到 ycu.space
export const API_BASE = isStatic() ? '' : 'https://ycu.space';

async function reqJSON(url, opts) {
  const resp = await fetch(url, Object.assign({ credentials: 'include' }, opts));
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw Object.assign({ status: resp.status }, data);
  return data;
}

export function fetchDecidePositionsRemote() {
  return reqJSON(API_BASE + '/api/decide/positions');
}

export function postDecidePosition(payload) {
  return reqJSON(API_BASE + '/api/decide/positions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export function deleteDecidePosition(code, isCb) {
  const q = isCb ? '?cb=1' : '';
  return reqJSON(
    API_BASE + '/api/decide/positions/' + encodeURIComponent(code) + q,
    { method: 'DELETE' },
  );
}

export function loginDecide(user, password) {
  return reqJSON(API_BASE + '/api/decide/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user, password }),
  });
}

export function logoutDecide() {
  return reqJSON(API_BASE + '/api/decide/logout', { method: 'POST' });
}

export function fetchDecideAuthState() {
  return reqJSON(API_BASE + '/api/decide/health');
}

export function fetchDecideConfig() {
  return isStatic()
    ? getJSON(dsPath('data/decide-config.json'))
    : getJSON('/api/decide/config');
}
