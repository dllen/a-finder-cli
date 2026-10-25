// 可转债页面：榜单 + 持仓 + 强赎预警

import { fetchCbPicks, fetchCbPositions, fetchCbAlerts, fetchCbDates } from './api.js';
import { $, fmt, esc, setStatus } from './dom.js';
import { scoreBand } from './table.js';

const ALERT_CLS = {
  emergency: 'bg-danger',
  important: 'bg-warning text-dark',
  watch: 'bg-info text-dark',
  normal: 'bg-secondary',
};

function pct(v) { return v == null ? '—' : (v * 100).toFixed(1) + '%'; }

// ---------------------------------------------------------------------------
// 摘要卡
// ---------------------------------------------------------------------------

function renderSummary(picksCount, positionsCount, alertsCount, latestDate) {
  const el = $('cb-summary');
  if (!el) return;
  const card = (title, value, sub) => `
    <div class="col">
      <div class="card shadow-sm h-100">
        <div class="card-body py-2 px-3">
          <div class="text-muted small mb-1">${title}</div>
          <div class="fs-4 fw-bold">${value}</div>
          <div class="text-muted small">${sub}</div>
        </div>
      </div>
    </div>`;
  el.innerHTML =
    card('转债榜单', picksCount, latestDate ? `最新 ${latestDate}` : '暂无数据') +
    card('持仓', positionsCount, positionsCount ? '只' : '空仓') +
    card('预警', alertsCount, alertsCount ? '需处理' : '无');
}

// ---------------------------------------------------------------------------
// 预警
// ---------------------------------------------------------------------------

function renderAlerts(alerts, generatedAt) {
  const el = $('cb-alerts');
  if (!el) return;
  if (!alerts || !alerts.length) {
    el.innerHTML = '<div class="empty-state">无预警：持仓全部正常</div>';
    return;
  }
  let html = '<div class="app-card mb-2">';
  alerts.forEach(a => {
    const cls = ALERT_CLS[a.level] || 'bg-secondary';
    html += `<div class="border-bottom py-2 d-flex align-items-start gap-2">` +
      `<span class="badge ${cls}">${esc(a.level_label || a.level)}</span>` +
      `<div class="flex-grow-1">` +
        `<div class="fw-semibold">${esc(a.code)} ${esc(a.name)}` +
        `<span class="text-muted ms-2">${a.shares != null ? a.shares + ' 张' : ''}</span></div>` +
        `<div class="small">${esc(a.message)}</div>` +
        `<div class="small text-muted">现价 ${fmt(a.price)}</div>` +
      `</div></div>`;
  });
  html += '</div>';
  if (generatedAt) {
    html += `<div class="text-muted small mb-3">快照时间 ${esc(generatedAt)}</div>`;
  }
  el.innerHTML = html;
}

// ---------------------------------------------------------------------------
// 持仓
// ---------------------------------------------------------------------------

function renderPositions(rows) {
  const el = $('cb-positions');
  if (!el) return;
  if (!rows || !rows.length) {
    el.innerHTML = '<div class="empty-state">暂无转债持仓</div>';
    return;
  }
  let html = '<div class="table-responsive app-card d-none d-md-block mb-3"><table class="app-table"><thead><tr>' +
    '<th>代码</th><th>名称</th><th>正股</th><th>建仓日</th><th class="num">建仓价</th>' +
    '<th class="num">张数</th><th class="num">止损</th><th class="num">止盈</th><th>备注</th>' +
    '</tr></thead><tbody>';
  rows.forEach(r => {
    html += `<tr><td class="code">${esc(r.code)}</td><td>${esc(r.name || '—')}</td>` +
      `<td>${esc(r.stock_name || r.stock_code || '—')}</td>` +
      `<td>${esc(r.entry_date || '—')}</td>` +
      `<td class="num">${fmt(r.entry_price)}</td>` +
      `<td class="num">${r.shares}</td>` +
      `<td class="num">${fmt(r.stop_price)}</td>` +
      `<td class="num">${fmt(r.tp_price)}</td>` +
      `<td>${esc(r.note || '')}</td></tr>`;
  });
  html += '</tbody></table></div>';

  html += '<div class="app-card d-md-none mb-3">';
  rows.forEach(r => {
    html += `<div class="plan-card"><div class="card-header-row">` +
      `<span class="code fw-semibold">${esc(r.code)}</span>` +
      `<span class="card-title-text">${esc(r.name || '')}</span>` +
      `<span class="num">${r.shares} 张</span></div>` +
      `<div class="kv-grid">` +
        `<span class="k">正股</span><span class="v wrap">${esc(r.stock_name || r.stock_code || '—')}</span>` +
        `<span class="k">建仓</span><span class="v num">${esc(r.entry_date || '—')} @ ${fmt(r.entry_price)}</span>` +
        `<span class="k">止损</span><span class="v num">${fmt(r.stop_price)}</span>` +
        `<span class="k">止盈</span><span class="v num">${fmt(r.tp_price)}</span>` +
      `</div></div>`;
  });
  html += '</div>';
  el.innerHTML = html;
}

// ---------------------------------------------------------------------------
// 榜单
// ---------------------------------------------------------------------------

export function renderPicks(date, rows) {
  const el = $('cb-board');
  if (!el) return;
  if (!rows || !rows.length) {
    el.innerHTML = '<div class="empty-state">该日期暂无转债榜单</div>';
    return;
  }
  const maxScore = rows.reduce((m, r) =>
    r.total_score != null && r.total_score > m ? r.total_score : m, 0);

  let html = '<div class="table-responsive app-card d-none d-md-block mb-3"><table class="app-table"><thead><tr>' +
    '<th>排名</th><th>代码</th><th>名称</th><th class="num">现价</th><th class="num">总评分</th>' +
    '<th class="num">债底</th><th class="num">股性</th><th class="num">条款</th>' +
    '<th class="num">仓位</th><th class="num">张数</th><th class="num">止损</th><th class="num">止盈</th>' +
    '<th>理由</th></tr></thead><tbody>';
  rows.forEach((r, i) => {
    const band = scoreBand(r.total_score, maxScore);
    html += `<tr class="${band}">` +
      `<td><span class="rank-badge">${i + 1}</span></td>` +
      `<td class="code">${esc(r.code)}</td><td>${esc(r.name)}</td>` +
      `<td class="num">${fmt(r.price)}</td>` +
      `<td class="num"><span class="score-chip">${r.total_score == null ? '—' : r.total_score}</span></td>` +
      `<td class="num">${r.a_score == null ? '—' : r.a_score}</td>` +
      `<td class="num">${r.b_score == null ? '—' : r.b_score}</td>` +
      `<td class="num">${r.c_score == null ? '—' : r.c_score}</td>` +
      `<td class="num">${pct(r.size_pct)}</td>` +
      `<td class="num">${r.shares}</td>` +
      `<td class="num">${fmt(r.stop_price)}</td>` +
      `<td class="num">${fmt(r.tp_price)}</td>` +
      `<td class="small">${esc(r.reason || '')}` +
      `${r.risk_note ? ` <span class="text-warning">${esc(r.risk_note)}</span>` : ''}</td>` +
      `</tr>`;
  });
  html += '</tbody></table></div>';

  html += '<div class="app-card d-md-none mb-3">';
  rows.forEach((r, i) => {
    const band = scoreBand(r.total_score, maxScore).replace('row-', 'band-');
    html += `<div class="plan-card ${band}"><div class="card-header-row">` +
      `<span class="rank-badge">${i + 1}</span>` +
      `<span class="code fw-semibold">${esc(r.code)}</span>` +
      `<span class="card-title-text">${esc(r.name)}</span>` +
      `<span class="score-chip">${r.total_score == null ? '—' : r.total_score}</span></div>` +
      `<div class="kv-grid">` +
        `<span class="k">现价</span><span class="v num">${fmt(r.price)}</span>` +
        `<span class="k">评分</span><span class="v num">债底 ${r.a_score ?? '—'} / 股性 ${r.b_score ?? '—'} / 条款 ${r.c_score ?? '—'}</span>` +
        `<span class="k">仓位</span><span class="v num">${pct(r.size_pct)} · ${r.shares} 张</span>` +
        `<span class="k">止损</span><span class="v num">${fmt(r.stop_price)}</span>` +
        `<span class="k">止盈</span><span class="v num">${fmt(r.tp_price)}</span>` +
      `</div>` +
      (r.reason ? `<div class="small text-muted mt-1">${esc(r.reason)}</div>` : '') +
      (r.risk_note ? `<div class="small text-warning mt-1">${esc(r.risk_note)}</div>` : '') +
      `</div>`;
  });
  html += '</div>';
  el.innerHTML = html;
}

// ---------------------------------------------------------------------------
// 加载
// ---------------------------------------------------------------------------

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

export async function loadAll() {
  setStatus('加载中…', 'alert-info');
  try {
    const [datesRes, posRes, alertsRes] = await Promise.all([
      fetchCbDates().catch(() => ({ dates: [] })),
      fetchCbPositions().catch(() => ({ rows: [] })),
      fetchCbAlerts().catch(() => ({ alerts: [], generated_at: '' })),
    ]);
    fillDateOptions(datesRes.dates || []);
    const positions = posRes.rows || [];
    const alerts = alertsRes.alerts || [];

    const date = ($('cb-date') && $('cb-date').value) || (datesRes.dates || [])[0] || '';
    const picks = date ? await loadPicks(date) : { date: '', rows: [] };

    renderSummary((picks.rows || []).length, positions.length, alerts.length, picks.date);
    renderAlerts(alerts, alertsRes.generated_at);
    renderPositions(positions);
    setStatus('', '');
  } catch (e) {
    setStatus('加载失败：' + (e && e.message ? e.message : e), 'alert-danger');
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const sel = $('cb-date');
  if (sel) sel.addEventListener('change', () => loadPicks(sel.value));
  const btn = $('cb-refresh');
  if (btn) btn.addEventListener('click', loadAll);
  loadAll();
});
