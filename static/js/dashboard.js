// 顶部 Dashboard 卡片刷新（替代原 static/dashboard.js）
//
// ⚠️ 2026-11-05：「交易计划」「持仓组合」「决策台」已下线（用户要求）。
// 原来这里是 4 张卡（运行状态 / 今日 plan / 持仓概览 / 5日收益）+ 可转债 ——
// 前三张都读 trade_plan / open_positions / trade_events，那三张表已清空，
// 继续渲染只会显示空壳。现在只保留「运行状态」与「可转债」两张。

import { fetchDashboard, isStatic, cbHref } from './api.js';

function freshnessBadge(f) {
  if (!f) return '<span class="badge bg-secondary">—</span>';
  const cls = { fresh: 'bg-success', warm: 'bg-warning text-dark', stale: 'bg-danger' }[f] || 'bg-secondary';
  return `<span class="badge ${cls}">${freshnessCn(f)}</span>`;
}

function freshnessCn(f) {
  return { fresh: '新鲜', warm: '滞后', stale: '过期' }[f] || '—';
}

function renderDashboard(d) {
  const lr = d.last_refresh;
  const cb = d.cb;

  const card = (title, body) => `
    <div class="col">
      <div class="card dashboard-card h-100 shadow-sm">
        <div class="card-body py-2 px-2 px-md-3">
          <div class="text-muted small mb-1">${title}</div>
          ${body}
        </div>
      </div>
    </div>`;

  const lrHtml = lr
    ? `<div class="d-flex align-items-center">
         ${freshnessBadge(lr.freshness)}<strong class="ms-2">${lr.date}</strong>
       </div>
       <small class="text-muted">${lr.ago_hours}h 前</small>`
    : '<span class="text-muted">无数据</span>';

  const cbHtml = cb
    ? `<div><strong>${cb.picks_count}</strong> <small class="text-muted">只榜单</small>` +
      ` <strong class="ms-2">${cb.positions_count}</strong> <small class="text-muted">只持仓</small></div>` +
      `<small class="text-muted">${cb.latest_date ? '榜单 ' + cb.latest_date : '暂无榜单'} · <a href="${cbHref()}">查看 →</a></small>`
    : '<span class="text-muted small">转债数据未初始化</span>';

  const el = document.getElementById('dashboard');
  if (el) {
    el.innerHTML = `
      <div class="row row-cols-1 row-cols-md-2 g-2 mb-3">
        ${card('运行状态', lrHtml)}
        ${card('可转债', cbHtml)}
      </div>`;
  }
}

export function startDashboard() {
  function tick() {
    if (document.hidden) return;
    fetchDashboard()
      .then(renderDashboard)
      .catch(() => {
        const el = document.getElementById('dashboard');
        if (el) el.innerHTML = '<div class="text-muted small mb-3">dashboard 刷新失败</div>';
      });
  }
  window.refreshDashboard = tick;
  tick();
  if (!isStatic()) {
    setInterval(tick, 15000);
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) tick();
    });
  }
}
