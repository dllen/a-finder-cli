// The sheet's two records: data freshness and the bond register.

import { fetchDashboard, isStatic, cbHref } from './api.js';
import { esc } from './dom.js';

function freshnessChip(f) {
  if (!f) return '';
  const map = {
    fresh: ['新鲜', 'chip'],
    warm: ['滞后', 'chip chip--warm'],
    stale: ['过期', 'chip chip--bad'],
  };
  const [label, cls] = map[f] || ['未知', 'chip'];
  return `<span class="${cls}">${label}</span>`;
}

function renderDashboard(d) {
  const lr = d.last_refresh || null;
  const cb = d.cb || null;

  const statusValue = lr
    ? `${freshnessChip(lr.freshness)} <span class="mono">${esc(lr.date)}</span>`
    : '<span class="record__meta">无数据</span>';
  const statusMeta = lr ? `${lr.ago_hours} 小时前更新` : '';

  const cbValue = cb
    ? `<span class="mono">${cb.picks_count}</span> 只榜单 <span class="mono">${cb.positions_count}</span> 只持仓`
    : '<span class="record__meta">转债数据未初始化</span>';
  const cbMeta = cb
    ? `最新 <span class="mono">${cb.latest_date || '无'}</span>，<a href="${cbHref()}">可转债页</a>`
    : '';

  const el = document.getElementById('dashboard');
  if (!el) return;
  el.innerHTML =
    '<div class="record">' +
    '<p class="record__label">运行状态</p>' +
    `<p class="record__value">${statusValue}</p>` +
    `<p class="record__meta">${statusMeta}</p>` +
    '</div>' +
    '<div class="record">' +
    '<p class="record__label">可转债</p>' +
    `<p class="record__value">${cbValue}</p>` +
    `<p class="record__meta">${cbMeta}</p>` +
    '</div>';
}

export function startDashboard() {
  function tick() {
    if (document.hidden) return;
    fetchDashboard()
      .then(renderDashboard)
      .catch(() => {
        const el = document.getElementById('dashboard');
        if (el) el.innerHTML = '<p class="record__meta">运行状态读取失败</p>';
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
