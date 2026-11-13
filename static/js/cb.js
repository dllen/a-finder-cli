// 可转债页面：榜单 + 持仓 + 强赎预警

import { fetchCbPicks, fetchCbPositions, fetchCbAlerts, fetchCbDates } from './api.js';
import { $, fmt, esc, setStatus, fmtScore } from './dom.js';
import { setDateFilterState } from './date-filter.js';

const view = { pickRows: [], pickDate: '', positions: [], alerts: [], alertsAt: '' };

function pct(v) { return v == null ? '无' : (v * 100).toFixed(1) + '%'; }

function figure(label, value, meta) {
  return '<div class="figure">' +
    `<p class="figure__label">${label}</p>` +
    `<p class="figure__value">${value}</p>` +
    `<p class="figure__meta">${meta}</p>` +
    '</div>';
}

function renderSummary(picksCount, positionsCount, alertsCount, latestDate) {
  const el = $('cb-summary');
  if (!el) return;
  el.innerHTML =
    figure('转债榜单', picksCount,
      latestDate ? `<span class="figure__meta-label">交易日 </span>${esc(latestDate)}` : '暂无数据') +
    figure('持仓', positionsCount, positionsCount ? '只' : '空仓') +
    figure('预警', alertsCount, alertsCount ? '需处理' : '无');
}

function renderAlerts(alerts, generatedAt) {
  const el = $('cb-alerts');
  if (!el) return;
  if (!alerts || !alerts.length) {
    el.innerHTML = '<p class="empty-line">无预警，持仓全部正常</p>';
    return;
  }
  const chipOf = (level) => level === 'emergency' ? 'chip chip--bad'
    : (level === 'important' ? 'chip chip--warm' : 'chip');
  let html = '<ul class="trail">';
  alerts.forEach(a => {
    html += '<li class="trail__row">' +
      `<span class="${chipOf(a.level)}">${esc(a.level_label || a.level)}</span>` +
      '<div class="trail__body">' +
      `<p class="trail__title"><span class="code">${esc(a.code)}</span> ${esc(a.name)}` +
      `${a.shares != null ? ` <span class="record__meta">${a.shares} 张</span>` : ''}</p>` +
      `<p class="trail__note">${esc(a.message)}</p>` +
      `<p class="trail__meta">现价 ${fmt(a.price)}</p>` +
      '</div></li>';
  });
  html += '</ul>';
  if (generatedAt) html += `<p class="trail__meta">快照时间 ${esc(generatedAt)}</p>`;
  el.innerHTML = html;
}

function renderPositions(rows) {
  const el = $('cb-positions');
  if (!el) return;
  if (!rows || !rows.length) {
    el.innerHTML = '<p class="empty-line">暂无转债持仓</p>';
    return;
  }
  let html = '<div class="table-wrap"><table class="ledger"><thead><tr>' +
    '<th>代码</th><th>名称</th><th>正股</th><th>建仓日</th><th class="num">建仓价</th>' +
    '<th class="num">张数</th><th class="num">止损</th><th class="num">止盈</th><th>备注</th>' +
    '</tr></thead><tbody>';
  rows.forEach(r => {
    html += `<tr><td class="code">${esc(r.code)}</td><td class="name">${esc(r.name || '无')}</td>` +
      `<td class="strategy">${esc(r.stock_name || r.stock_code || '无')}</td>` +
      `<td class="mono">${esc(r.entry_date || '无')}</td>` +
      `<td class="num">${fmt(r.entry_price)}</td>` +
      `<td class="num">${r.shares}</td>` +
      `<td class="num">${fmt(r.stop_price)}</td>` +
      `<td class="num">${fmt(r.tp_price)}</td>` +
      `<td class="strategy">${esc(r.note || '')}</td></tr>`;
  });
  html += '</tbody></table></div>';

  html += '<ul class="ledger-list">';
  rows.forEach(r => {
    html += '<li class="ledger-row">' +
      '<div class="ledger-row__head">' +
      `<span class="code">${esc(r.code)}</span>` +
      `<span class="ledger-row__name">${esc(r.name || '')}</span>` +
      `<span class="record__meta">${r.shares} 张</span>` +
      '</div>' +
      '<div class="kv-grid">' +
      `<span class="k">正股</span><span class="v wrap">${esc(r.stock_name || r.stock_code || '无')}</span>` +
      `<span class="k">建仓</span><span class="v">${esc(r.entry_date || '无')} @ ${fmt(r.entry_price)}</span>` +
      `<span class="k">止损</span><span class="v">${fmt(r.stop_price)}</span>` +
      `<span class="k">止盈</span><span class="v">${fmt(r.tp_price)}</span>` +
      '</div>' +
      (r.note ? `<p class="trail__meta">${esc(r.note)}</p>` : '') +
      '</li>';
  });
  html += '</ul>';
  el.innerHTML = html;
}

export function renderPicks(date, rows) {
  const el = $('cb-board');
  if (!el) return;
  if (!rows || !rows.length) {
    el.innerHTML = '<div class="empty-state">该日期暂无转债榜单</div>';
    return;
  }
  const maxScore = rows.reduce((m, r) =>
    r.total_score != null && r.total_score > m ? r.total_score : m, 0);

  let html = '<div class="table-wrap"><table class="ledger"><thead><tr>' +
    '<th class="rank-col">排名</th><th>代码</th><th>名称</th><th class="num">现价</th>' +
    '<th class="num">总评分</th><th class="num">债底</th><th class="num">股性</th><th class="num">条款</th>' +
    '<th class="num">仓位</th><th class="num">张数</th><th class="num">止损</th><th class="num">止盈</th>' +
    '<th>理由</th></tr></thead><tbody>';
  rows.forEach((r, i) => {
    const top = r.total_score != null && r.total_score === maxScore;
    html += `<tr class="${top ? 'is-top' : ''}">` +
      `<td class="rank-col"><span class="rank">${i + 1}</span></td>` +
      `<td class="code">${esc(r.code)}</td><td class="name">${esc(r.name)}</td>` +
      `<td class="num">${fmt(r.price)}</td>` +
      `<td class="num"><span class="score">${fmtScore(r.total_score)}</span></td>` +
      `<td class="num">${fmtScore(r.a_score)}</td>` +
      `<td class="num">${fmtScore(r.b_score)}</td>` +
      `<td class="num">${fmtScore(r.c_score)}</td>` +
      `<td class="num">${pct(r.size_pct)}</td>` +
      `<td class="num">${r.shares}</td>` +
      `<td class="num">${fmt(r.stop_price)}</td>` +
      `<td class="num">${fmt(r.tp_price)}</td>` +
      `<td class="strategy">${esc(r.reason || '')}` +
      `${r.risk_note ? ` <span class="note">${esc(r.risk_note)}</span>` : ''}</td></tr>`;
  });
  html += '</tbody></table></div>';

  html += '<ul class="ledger-list">';
  rows.forEach((r, i) => {
    const top = r.total_score != null && r.total_score === maxScore;
    html += '<li class="ledger-row">' +
      '<div class="ledger-row__head">' +
      `<span class="rank">${i + 1}</span>` +
      `<span class="code">${esc(r.code)}</span>` +
      `<span class="ledger-row__name">${esc(r.name)}</span>` +
      `<span class="score${top ? ' is-top' : ''}">${fmtScore(r.total_score)}</span>` +
      '</div>' +
      '<div class="kv-grid">' +
      `<span class="k">现价</span><span class="v">${fmt(r.price)}</span>` +
      `<span class="k">仓位</span><span class="v">${pct(r.size_pct)}</span>` +
      `<span class="k">债底</span><span class="v">${fmtScore(r.a_score)}</span>` +
      `<span class="k">股性</span><span class="v">${fmtScore(r.b_score)}</span>` +
      `<span class="k">条款</span><span class="v">${fmtScore(r.c_score)}</span>` +
      `<span class="k">张数</span><span class="v">${r.shares}</span>` +
      `<span class="k">止损</span><span class="v">${fmt(r.stop_price)}</span>` +
      `<span class="k">止盈</span><span class="v">${fmt(r.tp_price)}</span>` +
      '</div>' +
      (r.reason ? `<p class="trail__meta">${esc(r.reason)}</p>` : '') +
      (r.risk_note ? `<p class="trail__meta"><span class="note">${esc(r.risk_note)}</span></p>` : '') +
      '</li>';
  });
  html += '</ul>';
  el.innerHTML = html;
}

function fillDateOptions(dates) {
  const sel = $('cb-date');
  if (!sel) return;
  sel.innerHTML = '';
  (dates || []).forEach(d => sel.appendChild(new Option(d, d)));
  if (dates && dates.length) sel.value = dates[0];
}

async function loadPicks(date) {
  const data = await fetchCbPicks(date);
  renderPicks(data.date, data.rows || []);
  return data;
}

function skeleton(el, rows) {
  if (!el) return;
  let html = '<div class="skeleton-panel" aria-busy="true" aria-label="加载中">';
  for (let i = 0; i < (rows || 3); i++) html += '<div class="skeleton skeleton-row"></div>';
  el.innerHTML = html + '</div>';
}

async function loadBoard(date) {
  setDateFilterState('cb-date', 'loading');
  skeleton($('cb-board'), 3);
  try {
    const data = await loadPicks(date);
    view.pickRows = data.rows || [];
    view.pickDate = data.date || date;
    renderSummary(view.pickRows.length, view.positions.length, view.alerts.length, view.pickDate);
    setDateFilterState('cb-date', '', '');
  } catch (e) {
    setDateFilterState('cb-date', 'error', '榜单读取失败');
    const board = $('cb-board');
    if (board) board.innerHTML = '<div class="empty-state">转债榜单读取失败</div>';
  }
}

export async function loadAll() {
  const btn = $('cb-refresh');
  if (btn) btn.disabled = true;
  setStatus('加载中…', 'alert-info');
  setDateFilterState('cb-date', 'loading');
  skeleton($('cb-summary'), 1);
  skeleton($('cb-alerts'), 1);
  skeleton($('cb-positions'), 1);
  skeleton($('cb-board'), 3);
  try {
    const [datesRes, posRes, alertsRes] = await Promise.all([
      fetchCbDates().catch(() => ({ error: true, dates: [] })),
      fetchCbPositions().catch(() => ({ error: true, rows: [] })),
      fetchCbAlerts().catch(() => ({ error: true, alerts: [], generated_at: '' })),
    ]);
    fillDateOptions(datesRes.dates || []);
    view.positions = posRes.rows || [];
    view.alerts = alertsRes.alerts || [];
    view.alertsAt = alertsRes.generated_at || '';

    const date = ($('cb-date') && $('cb-date').value) || (datesRes.dates || [])[0] || '';
    if (date) {
      try {
        const picks = await loadPicks(date);
        view.pickRows = picks.rows || [];
        view.pickDate = picks.date || date;
      } catch (e) {
        view.pickRows = [];
        view.pickDate = date;
        const board = $('cb-board');
        if (board) board.innerHTML = '<div class="empty-state">转债榜单读取失败</div>';
      }
    } else {
      view.pickRows = [];
      view.pickDate = '';
      const board = $('cb-board');
      if (board) board.innerHTML = '<div class="empty-state">该日期暂无转债榜单</div>';
    }

    renderSummary(view.pickRows.length, view.positions.length, view.alerts.length, view.pickDate);
    if (posRes.error) {
      const el = $('cb-positions');
      if (el) el.innerHTML = '<div class="empty-state">持仓读取失败</div>';
    } else {
      renderPositions(view.positions);
    }
    if (alertsRes.error) {
      const el = $('cb-alerts');
      if (el) el.innerHTML = '<div class="empty-state">预警读取失败</div>';
    } else {
      renderAlerts(view.alerts, view.alertsAt);
    }
    if (datesRes.error) setDateFilterState('cb-date', 'error', '日期加载失败');
    else setDateFilterState('cb-date', '', '');
    setStatus('', '');
  } catch (e) {
    setStatus('加载失败：' + (e && e.message ? e.message : e), 'alert-danger');
    setDateFilterState('cb-date', 'error', '加载失败');
  } finally {
    if (btn) btn.disabled = false;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const sel = $('cb-date');
  if (sel) sel.addEventListener('change', () => loadBoard(sel.value));
  const btn = $('cb-refresh');
  if (btn) btn.addEventListener('click', loadAll);
  loadAll();
});
