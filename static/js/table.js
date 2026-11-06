// Shared ledger rendering and sorting for both screens.

import { esc, fmt } from './dom.js?v=7c3c4f1';

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

function retParts(v) {
  if (v == null) return { cls: 'flat', text: '无' };
  const cls = v > 0 ? 'up' : (v < 0 ? 'down' : 'flat');
  return { cls, text: (v > 0 ? '+' : '') + v.toFixed(2) };
}

export function retCell(r) {
  const { cls, text } = retParts(r.ret_pct);
  return `<td class="num ${cls}">${text}</td>`;
}

function sortHead(cols, sortState) {
  let html = '<th class="rank-col">排名</th>';
  cols.forEach(c => {
    const active = sortState.key === c.key;
    const dir = active ? (sortState.dir === -1 ? 'desc' : 'asc') : '';
    const aria = dir === 'desc' ? 'descending' : (dir === 'asc' ? 'ascending' : 'none');
    html += `<th class="${c.cls} sortable${active ? ' is-sorted' : ''}" aria-sort="${aria}"` +
      `${dir ? ` data-dir="${dir}"` : ''}>` +
      `<button type="button" class="th-sort" data-sort="${c.key}">${esc(c.label)}` +
      (active ? '<span class="sort-mark" aria-hidden="true"></span>' : '') + '</button></th>';
  });
  return html + '</tr>';
}

export function renderPicksBoard(container, groups, cols, sortState, filterText) {
  let html = '';
  let anyGroup = false;
  let anyVisible = false;

  ['均线', '买入信号', '信号策略', '多因子'].forEach(kind => {
    const rs = groups[kind] || [];
    if (!rs.length) return;
    if (!rs.some(r => r.score != null)) return;
    anyGroup = true;

    const topScore = rs.reduce((m, r) => r.score != null && r.score > m ? r.score : m, 0);
    const filtered = rs.filter(r => matchFilter(r, filterText));
    const rows = sortRows(filtered, sortState.key, sortState.dir, cols);
    if (!rows.length) return;
    anyVisible = true;

    const count = filterText ? `${rows.length} / ${rs.length} 条` : `${rs.length} 条`;

    html += '<section class="group">' +
      `<div class="group__head"><h2 class="group__title">${esc(kind)}</h2>` +
      `<span class="group__count">${count}</span></div>`;

    html += '<div class="table-wrap"><table class="ledger"><thead><tr>' +
      sortHead(cols, sortState) + '</thead><tbody>';
    rows.forEach((r, i) => {
      const top = r.score != null && r.score === topScore;
      html += `<tr class="${top ? 'is-top' : ''}">` +
        `<td class="rank-col"><span class="rank">${i + 1}</span></td>` +
        `<td class="code">${esc(r.code)}</td><td class="name">${esc(r.name)}</td>` +
        `<td class="strategy">${esc(r.strategy)}</td>` +
        `<td class="num">${fmt(r.buy)}</td><td class="num">${fmt(r.stop)}</td><td class="num">${fmt(r.target)}</td>` +
        retCell(r) +
        `<td class="num"><span class="score">${r.score == null ? '无' : r.score}</span></td></tr>`;
    });
    html += '</tbody></table></div>';

    html += '<ul class="ledger-list">';
    rows.forEach((r, i) => {
      const top = r.score != null && r.score === topScore;
      const ret = retParts(r.ret_pct);
      html += '<li class="ledger-row">' +
        '<div class="ledger-row__head">' +
        `<span class="rank">${i + 1}</span>` +
        `<span class="code">${esc(r.code)}</span>` +
        `<span class="ledger-row__name">${esc(r.name)}</span>` +
        `<span class="score${top ? ' is-top' : ''}">${r.score == null ? '无' : r.score}</span>` +
        `<span class="ret ${ret.cls}">${ret.text}</span>` +
        '</div>' +
        '<div class="kv-grid">' +
        `<span class="k">策略</span><span class="v wrap">${esc(r.strategy)}</span>` +
        `<span class="k">买入</span><span class="v">${fmt(r.buy)}</span>` +
        `<span class="k">止损</span><span class="v">${fmt(r.stop)}</span>` +
        `<span class="k">目标</span><span class="v">${fmt(r.target)}</span>` +
        '</div></li>';
    });
    html += '</ul></section>';
  });

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
