// 通用表格渲染、排序、评分分档

import { esc, fmt } from './dom.js?v=fa665a3';

export function scoreBand(score, max) {
  if (score == null || max <= 0) return 'row-neutral';
  const ratio = score / max;
  if (ratio >= 0.85) return 'row-hot';
  if (ratio >= 0.7) return 'row-warm';
  if (ratio >= 0.5) return 'row-mild';
  return 'row-neutral';
}

export function sortRows(rows, key, dir, cols) {
  if (!key) return rows;
  const col = cols.find(c => c.key === key);
  const type = col ? col.type : 'num';
  return rows.slice().sort((a, b) => {
    const va = a[key], vb = b[key];
    if (va == null && vb == null) return 0;
    if (va == null) return 1;
    if (vb == null) return -1;
    if (type === 'num') return (va - vb) * dir;
    return String(va).localeCompare(String(vb), 'zh-CN') * dir;
  });
}

export function retCell(r) {
  if (r.ret_pct == null) return '<td class="num text-muted">—</td>';
  const cls = r.ret_pct >= 0 ? 'text-success' : 'text-danger';
  const sign = r.ret_pct >= 0 ? '+' : '';
  return `<td class="num ${cls}">${sign}${r.ret_pct.toFixed(2)}</td>`;
}

export function renderPicksBoard(container, groups, cols, sortState, filterText) {
  let html = '';
  let anyGroup = false;
  let anyVisible = false;

  ['均线', '买入信号', '信号策略', '多因子'].forEach(kind => {
    const rs = groups[kind] || [];
    if (!rs.length) return;
    const hasScore = rs.some(r => r.score != null);
    if (!hasScore) return;
    anyGroup = true;

    const maxScore = rs.reduce((m, r) => r.score != null && r.score > m ? r.score : m, 0);
    const filtered = rs.filter(r => matchFilter(r, filterText));
    const rows = sortRows(filtered, sortState.key, sortState.dir, cols);
    if (!rows.length) return;
    anyVisible = true;

    const badge = (filterText && rows.length !== rs.length)
      ? `${rows.length} / ${rs.length}`
      : `Top ${rows.length}`;

    // Desktop table
    html += `<h2 class="section-title">${kind} <span class="badge bg-light text-muted">${badge}</span></h2>`;
    html += '<div class="table-responsive app-card d-none d-md-block mb-3"><table class="app-table"><thead><tr><th>排名</th>';
    cols.forEach(c => {
      const active = sortState.key === c.key;
      const arrow = active ? (sortState.dir === -1 ? ' ▼' : ' ▲') : '';
      html += `<th class="${c.cls} sortable${active ? ' sorted' : ''}" data-sort="${c.key}">${c.label}${arrow}</th>`;
    });
    html += '</tr></thead><tbody>';
    rows.forEach((r, i) => {
      html += `<tr class="${scoreBand(r.score, maxScore)}"><td><span class="rank-badge">${i + 1}</span></td>` +
        `<td class="code">${esc(r.code)}</td><td>${esc(r.name)}</td><td>${esc(r.strategy)}</td>` +
        `<td class="num">${fmt(r.buy)}</td><td class="num">${fmt(r.stop)}</td><td class="num">${fmt(r.target)}</td>` +
        retCell(r) +
        `<td class="num"><span class="score-chip">${r.score == null ? '—' : r.score}</span></td></tr>`;
    });
    html += '</tbody></table></div>';

    // Mobile cards
    html += '<div class="app-card d-md-none mb-3">';
    rows.forEach((r, i) => {
      const bandCls = scoreBand(r.score, maxScore).replace('row-', 'band-');
      const retCls = r.ret_pct == null ? 'text-muted' : (r.ret_pct >= 0 ? 'text-success' : 'text-danger');
      const retSign = r.ret_pct == null ? '' : (r.ret_pct >= 0 ? '+' : '');
      const retVal = r.ret_pct == null ? '—' : retSign + r.ret_pct.toFixed(2);
      html += `<div class="pick-card ${bandCls}">` +
        `<div class="card-header-row">` +
          `<span class="rank-badge">${i + 1}</span>` +
          `<span class="code fw-semibold">${esc(r.code)}</span>` +
          `<span class="card-title-text">${esc(r.name)}</span>` +
          `<span class="card-score">${r.score == null ? '—' : r.score}</span>` +
        `</div>` +
        `<div class="kv-grid">` +
          `<span class="k">策略</span><span class="v wrap">${esc(r.strategy)}</span>` +
          `<span class="k">买入</span><span class="v num">${fmt(r.buy)}</span>` +
          `<span class="k">止损</span><span class="v num">${fmt(r.stop)}</span>` +
          `<span class="k">目标</span><span class="v num">${fmt(r.target)}</span>` +
          `<span class="k">涨跌</span><span class="v num ${retCls}">${retVal}</span>` +
        `</div></div>`;
    });
    html += '</div>';
  });

  if (anyGroup) {
    html = '<div class="legend d-flex flex-wrap align-items-center gap-3 mb-2">' +
      '<span class="text-muted small">评分分档：</span>' +
      '<span class="legend-item"><i class="sw sw-hot"></i>高 (≥85%)</span>' +
      '<span class="legend-item"><i class="sw sw-warm"></i>中 (≥70%)</span>' +
      '<span class="legend-item"><i class="sw sw-mild"></i>低 (≥50%)</span>' +
      '<span class="legend-item"><i class="sw sw-neutral"></i>其余</span>' +
      '<span class="text-muted small ms-auto d-none d-md-inline">点击表头排序</span>' +
      '</div>' + html;
  }

  container.innerHTML = anyVisible ? html
    : `<div class="empty-state">${anyGroup ? '无匹配筛选结果' : '该日期暂无选股数据'}</div>`;
}

function matchFilter(r, q) {
  if (!q) return true;
  q = q.toLowerCase();
  return String(r.code).toLowerCase().includes(q) ||
         String(r.name).toLowerCase().includes(q) ||
         String(r.strategy).toLowerCase().includes(q);
}
