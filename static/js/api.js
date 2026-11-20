// 数据源抽象：API 模式（Flask）与静态模式（gh-pages JSON）共用。
// window.APP_MODE / window.DATA_PREFIX 由 base.html 注入。
//
// ⚠️ 2026-11-05：「交易计划」「持仓组合」「决策台」已下线（用户要求）。
// 相关函数（fetchPlan / fetchHoldings / fetchPortfolio* / fetchDecide* /
// postPlanBuild / planHref）已删除，对应的 Flask 路由与静态 JSON 也一并移除。
// 恢复功能时请从 git 历史（commit 前 89150fb）取回本文件对应片段。

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

export function fetchDashboard() {
  return isStatic()
    ? getJSON(dsPath('data/dashboard.json'))
    : getJSON('/api/dashboard');
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
    const cbLink = document.querySelector('a[data-nav="cb"]');
    if (cbLink) cbLink.href = 'cb.html';
  });
}

// ---------------------------------------------------------------------------
// 刷新数据（本机 agent 跑流水线，Worker 只做排队与状态）
//
// 两个端点都在 Worker 上（`/api/*` 走脚本路由），静态站没有它们 ——
// 所以调用方必须先用 isStatic() 判断，静态站根本不该走到这里。
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

export function fetchRefresh() {
  return reqJSON(API_BASE + '/api/refresh', { method: 'POST' });
}

export function fetchRefreshStatus(jobId) {
  return reqJSON(API_BASE + '/api/refresh/status?job_id=' + encodeURIComponent(jobId));
}
