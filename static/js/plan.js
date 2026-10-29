// 交易计划页入口

import { fetchPlan, fetchPlanDates, fetchHoldings, postPlanBuild, pollJob } from './api.js?v=fa665a3';
import { $, fmt, esc, fillDateSelect, setStatus, setProgress, setLog, showBoardLoading, whenBoardReady } from './dom.js?v=fa665a3';
import { setDateFilterState } from './date-filter.js?v=fa665a3';
import { startDashboard } from './dashboard.js?v=fa665a3';

const CAPITAL_TIERS = [50000,100000,150000,200000,250000,300000,350000,400000,450000,500000];
const CAPITAL_LABELS = {};
CAPITAL_TIERS.forEach(c => { CAPITAL_LABELS[c] = (c / 10000) + 'W'; });

const state = { data: null, filter: '', capital: 100000, onlyAffordable: false, tierFilter: [] };

const ACTION_META = {
  buy:  { label: '买入', cls: 'bg-success' },
  hold: { label: '持有', cls: 'bg-secondary' },
  exit: { label: '退出', cls: 'bg-warning text-dark' },
};
function actionMeta(a) { return ACTION_META[a] || { label: a, cls: 'bg-light text-dark' }; }
function statusBadge(s) { return s === 'ok' ? 'bg-success' : 'bg-danger'; }

function sizeShares(capital, sizePct, price) {
  if (!(capital > 0) || !(sizePct > 0) || !(price > 0)) return 0;
  return Math.floor(capital * sizePct / (price * 100)) * 100;
}

function rowShares(r) {
  if (r.action !== 'buy') return (r.shares == null ? 0 : r.shares);
  return sizeShares(state.capital, r.size_pct, r.plan_price);
}

function insufficientLot(r) {
  return r.action === 'buy' && (r.size_pct || 0) > 0 && rowShares(r) === 0;
}

function tierSharesHtml(r) {
  if (r.action !== 'buy' || !(r.plan_price > 0)) return '';
  const heads = [], bodyCells = [];
  CAPITAL_TIERS.forEach(c => {
    const label = CAPITAL_LABELS[c] || (c / 10000) + 'W';
    heads.push(`<th class="tier-label" data-tier="${c}">${label}</th>`);
    const s = sizeShares(c, r.size_pct, r.plan_price);
    bodyCells.push(`<td class="num" data-tier="${c}">${s > 0 ? s : '<span class="text-warning">0</span>'}</td>`);
  });
  return `<details class="tier-shares-wrap"><summary class="text-muted">各档股数 5W-50W</summary>` +
    `<table class="tier-shares-table table table-sm table-borderless mb-0 mt-1"><thead><tr>${heads.join('')}</tr></thead><tbody><tr>${bodyCells.join('')}</tr></tbody></table></details>`;
}

function matchFilter(r, q) {
  if (!q) return true;
  q = q.toLowerCase();
  return String(r.code).toLowerCase().includes(q) || String(r.name || '').toLowerCase().includes(q);
}

function rowHtml(r) {
  const meta = actionMeta(r.action);
  const sizePct = r.size_pct == null ? '—' : (r.size_pct * 100).toFixed(1) + '%';
  const sharesHtml = insufficientLot(r) ? '0 <span class="text-warning small">资金不足一手</span>' : rowShares(r);
  let rationale = '<span class="text-muted">—</span>';
  let strategy = '—';
  try {
    const obj = JSON.parse(r.rationale_json || '{}');
    if (obj && obj.strategy) strategy = String(obj.strategy);
    const keys = Object.keys(obj || {});
    rationale = keys.length
      ? `<details><summary class="text-muted">${keys.length} 字段</summary><pre class="mb-0 small">${JSON.stringify(obj, null, 2)}</pre></details>`
      : '<span class="text-muted">—</span>';
  } catch (e) {
    rationale = `<small class="text-muted font-monospace">${esc(r.rationale_json || '—')}</small>`;
  }
  return `<tr><td class="code">${esc(r.code)}</td>` +
    `<td>${r.name ? esc(r.name) : '<span class="text-muted">—</span>'}</td>` +
    `<td><span class="badge ${meta.cls}">${meta.label}</span></td>` +
    `<td>${esc(strategy)}</td>` +
    `<td class="num">${fmt(r.plan_price)}</td>` +
    `<td class="num">${sizePct}</td>` +
    `<td class="num">${sharesHtml}</td>` +
    `<td class="num">${fmt(r.stop_price)}</td>` +
    `<td class="num">${fmt(r.tp_price)}</td>` +
    `<td class="num">${fmt(r.rr_ratio)}</td>` +
    `<td><span class="badge ${statusBadge(r.status)}">${r.status}</span>${r.reason ? ' <small class="text-muted">' + esc(r.reason) + '</small>' : ''}</td>` +
    `<td>${tierSharesHtml(r)}${rationale}</td></tr>`;
}

function drawPlan(data) {
  const allRows = data.rows || [];
  let rows = allRows
    .filter(r => matchFilter(r, state.filter))
    .filter(r => !state.onlyAffordable || !insufficientLot(r))
    .filter(r => {
      if (!state.tierFilter.length) return true;
      for (const t of state.tierFilter) {
        if (sizeShares(t, r.size_pct, r.plan_price) === 0) return false;
      }
      return true;
    });

  const board = $('board');
  if (!board) return;

  if (!rows.length) {
    board.innerHTML = `<div class="empty-state">${allRows.length ? '无匹配筛选结果' : '该日期暂无 plan'}</div>`;
    return;
  }

  const groups = { buy: [], hold: [], exit: [] };
  rows.forEach(r => (groups[r.action] || (groups[r.action] = [])).push(r));

  // Summary based on ALL rows (not filtered)
  let buySize = 0, buyShares = 0, usedCapital = 0, failed = 0;
  allRows.forEach(r => {
    if (r.action === 'buy' && r.status === 'ok' && r.size_pct != null) {
      buySize += r.size_pct;
      const sh = rowShares(r);
      buyShares += sh;
      usedCapital += sh * (r.plan_price || 0);
    }
    if (r.status === 'failed') failed++;
  });
  const cash = state.capital - usedCapital;
  const util = state.capital > 0 ? usedCapital / state.capital : 0;
  const capLabel = CAPITAL_LABELS[state.capital] || (state.capital / 10000 + 'W');
  const counts = Object.entries(groups).filter(([, v]) => v.length).map(([k, v]) => actionMeta(k).label + ' ' + v.length).join(' · ');

  let html = `<div class="card summary-card mb-3"><div class="card-body py-2">` +
    `<span class="me-3"><strong>${data.plan_date}</strong></span>` +
    `<span class="text-muted me-3">${rows.length} 行</span>` +
    `<span class="text-muted me-3">${counts}</span>` +
    `<span class="me-3">资金 ${capLabel}</span>` +
    `<span class="me-3 ${util > 1 ? 'text-danger' : 'text-muted'}">已用 ¥${fmt(usedCapital)} (${(util * 100).toFixed(1)}%)</span>` +
    `<span class="me-3 ${cash < 0 ? 'text-danger' : 'text-muted'}">现金 ¥${fmt(cash)}</span>` +
    `<span class="text-muted me-3">买入合计仓位 ${(buySize * 100).toFixed(1)}% · ${buyShares} 股</span>` +
    `<span class="text-muted">失败 ${failed}</span>` +
    `</div></div>`;

  ['buy', 'hold', 'exit'].forEach(a => {
    const rs = groups[a] || [];
    if (!rs.length) return;
    const meta = actionMeta(a);
    html += `<h2 class="section-title"><span class="badge ${meta.cls}">${meta.label}</span> <small class="text-muted">${rs.length} 只</small></h2>`;
    // Desktop table
    html += `<div class="table-responsive app-card d-none d-md-block mb-3"><table class="app-table"><thead><tr>` +
      `<th>代码</th><th>名称</th><th>方向</th><th>策略</th><th class="num">计划价</th><th class="num">仓位</th><th class="num">股数</th><th class="num">止损</th><th class="num">止盈</th><th class="num">RR</th><th>状态</th><th>理由</th>` +
      `</tr></thead><tbody>${rs.map(rowHtml).join('')}</tbody></table></div>`;
    // Mobile cards
    html += '<div class="app-card d-md-none mb-3">';
    rs.forEach(r => {
      const sizePct = r.size_pct == null ? '—' : (r.size_pct * 100).toFixed(1) + '%';
      const m = actionMeta(r.action);
      let cardStrategy = '—';
      try { const o = JSON.parse(r.rationale_json || '{}'); if (o && o.strategy) cardStrategy = String(o.strategy); } catch (e) {}
      html += `<div class="plan-card">` +
        `<div class="card-header-row">` +
          `<span class="code fw-semibold">${esc(r.code)}</span>` +
          `<span class="card-title-text">${esc(r.name || '')}</span>` +
          `<span class="badge ${m.cls}">${m.label}</span>` +
          `<span class="badge ${statusBadge(r.status)}">${esc(r.status)}</span>` +
        `</div>` +
        `<div class="kv-grid">` +
          `<span class="k">策略</span><span class="v wrap">${esc(cardStrategy)}</span>` +
          `<span class="k">计划价</span><span class="v num">${fmt(r.plan_price)}</span>` +
          `<span class="k">仓位</span><span class="v num">${sizePct}</span>` +
          `<span class="k">股数</span><span class="v num">${insufficientLot(r) ? '0 <span class="text-warning small">资金不足一手</span>' : rowShares(r)}</span>` +
          `<span class="k">止损</span><span class="v num">${fmt(r.stop_price)}</span>` +
          `<span class="k">止盈</span><span class="v num">${fmt(r.tp_price)}</span>` +
          `<span class="k">RR</span><span class="v num">${fmt(r.rr_ratio)}</span>` +
        `</div>` +
        (r.reason ? `<div class="small text-muted mt-1">${esc(r.reason)}</div>` : '') +
        tierSharesHtml(r) +
      `</div>`;
    });
    html += '</div>';
  });
  board.innerHTML = html;
}

function render(date) {
  showBoardLoading();
  const includeFailed = $('include-failed')?.checked;
  fetchPlan(date, includeFailed).then(data => {
    whenBoardReady(() => { state.data = data; drawPlan(data); });
  }).catch(() => setStatus('加载失败', 'alert-danger'));
}

function loadDates() {
  fetchPlanDates().then(d => {
    const v = fillDateSelect(d.dates);
    setDateFilterState('d', '', '');
    if (v) render(v);
    else { const b = $('board'); if (b) b.innerHTML = '<div class="empty-state">暂无 plan 数据</div>'; }
  }).catch(() => {
    setDateFilterState('d', 'error', '日期加载失败');
    const b = $('board'); if (b) b.innerHTML = '<div class="empty-state">日期加载失败</div>';
  });
}

function buildPlan() {
  const date = $('d')?.value;
  if (!date) { setStatus('请先选日期', 'alert-warning'); return; }
  const btn = $('btn-build');
  if (btn) btn.disabled = true;
  setStatus('任务已提交…', 'alert-info');
  setLog(null);
  setProgress(null);

  postPlanBuild({ plan_date: date, capital: state.capital }).then(data => {
    pollJob(data.job_id,
      d => { setProgress(d.progress); setLog(d.log); setStatus('任务进行中…', 'alert-info'); },
      d => {
        setStatus(d.message, 'alert-success');
        if (btn) btn.disabled = false;
        if (window.refreshDashboard) window.refreshDashboard();
        loadDates();
        loadHoldings();
      },
      d => {
        setStatus('任务失败：' + (d.message || ''), 'alert-danger');
        if (btn) btn.disabled = false;
      }
    );
  }).catch(err => {
    if (err.status === 409) setStatus('已有任务进行中', 'alert-warning');
    else setStatus('提交失败', 'alert-danger');
    if (btn) btn.disabled = false;
  });
}

function drawHoldings(d) {
  const rows = d.holdings || [];
  const s = d.summary || {};
  const el = $('holdings');
  if (!el) return;

  let sumHtml = `<div class="card summary-card mb-3"><div class="card-body py-2">` +
    `<span class="me-3"><strong>持仓 ${s.open_count}</strong></span>` +
    `<span class="me-3">总股数 ${s.shares_total || 0}</span>` +
    `<span class="me-3">浮动 ${s.floating_pnl >= 0 ? '+' : ''}${fmt(s.floating_pnl)} 元</span>` +
    `<span class="me-3">已实现 ${s.realized_pnl >= 0 ? '+' : ''}${fmt(s.realized_pnl)} 元</span>` +
    `<span class="me-3"><strong>总收益 ${s.total_pnl >= 0 ? '+' : ''}${fmt(s.total_pnl)} 元</strong></span>` +
    `<span class="${s.return_pct >= 0 ? 'text-success' : 'text-danger'}">${s.return_pct >= 0 ? '+' : ''}${fmt(s.return_pct)}%</span>` +
    `</div></div>`;

  if (!rows.length) { el.innerHTML = sumHtml + '<div class="empty-state">暂无持仓</div>'; return; }

  let h = sumHtml + `<div class="table-responsive app-card d-none d-md-block mb-3"><table class="app-table"><thead><tr>` +
    `<th>代码</th><th>名称</th><th class="num">股数</th><th class="num">加权均价</th><th class="num">现价</th>` +
    `<th class="num">止损</th><th class="num">止盈</th><th class="num">浮动盈亏</th><th class="num">止损预期</th><th class="num">止盈预期</th>` +
    `</tr></thead><tbody>` +
    rows.map(r =>
      `<tr><td class="code">${esc(r.code)}</td><td>${esc(r.name || '—')}</td>` +
      `<td class="num">${r.shares}</td><td class="num">${fmt(r.entry_price)}</td><td class="num">${fmt(r.current_price)}</td>` +
      `<td class="num">${fmt(r.stop_price)}</td><td class="num">${fmt(r.tp_price)}</td>` +
      `<td class="num ${r.floating_pnl >= 0 ? 'text-success' : 'text-danger'}">${r.floating_pnl == null ? '—' : (r.floating_pnl >= 0 ? '+' : '') + fmt(r.floating_pnl)}</td>` +
      `<td class="num ${r.stop_pnl >= 0 ? 'text-success' : 'text-danger'}">${r.stop_pnl == null ? '—' : (r.stop_pnl >= 0 ? '+' : '') + fmt(r.stop_pnl)}</td>` +
      `<td class="num ${r.tp_pnl >= 0 ? 'text-success' : 'text-danger'}">${r.tp_pnl == null ? '—' : (r.tp_pnl >= 0 ? '+' : '') + fmt(r.tp_pnl)}</td></tr>`
    ).join('') + `</tbody></table></div>`;

  // Mobile cards
  h += '<div class="app-card d-md-none mb-3">';
  rows.forEach(r => {
    h += `<div class="plan-card">` +
      `<div class="card-header-row">` +
        `<span class="code fw-semibold">${esc(r.code)}</span>` +
        `<span class="card-title-text">${esc(r.name || '')}</span>` +
        `<span class="num ${r.floating_pnl >= 0 ? 'text-success' : 'text-danger'}">${r.floating_pnl == null ? '—' : (r.floating_pnl >= 0 ? '+' : '') + fmt(r.floating_pnl)}</span>` +
      `</div>` +
      `<div class="kv-grid">` +
        `<span class="k">股数</span><span class="v num">${r.shares}</span>` +
        `<span class="k">均价</span><span class="v num">${fmt(r.entry_price)}</span>` +
        `<span class="k">现价</span><span class="v num">${fmt(r.current_price)}</span>` +
        `<span class="k">止损</span><span class="v num">${fmt(r.stop_price)}</span>` +
        `<span class="k">止盈</span><span class="v num">${fmt(r.tp_price)}</span>` +
      `</div></div>`;
  });
  h += '</div>';
  el.innerHTML = h;
}

function loadHoldings() {
  fetchHoldings().then(drawHoldings).catch(() => {
    const el = $('holdings');
    if (el) el.innerHTML = '<div class="text-muted small">持仓加载失败</div>';
  });
}

// Init
document.addEventListener('DOMContentLoaded', () => {
  const dateSelect = $('d');
  if (dateSelect) dateSelect.addEventListener('change', () => render(dateSelect.value));

  const includeFailed = $('include-failed');
  if (includeFailed) includeFailed.addEventListener('change', () => {
    const d = $('d')?.value;
    if (d) render(d);
  });

  const onlyAffordable = $('only-affordable');
  if (onlyAffordable) onlyAffordable.addEventListener('change', () => {
    state.onlyAffordable = onlyAffordable.checked;
    if (state.data) drawPlan(state.data);
  });

  document.querySelectorAll('.tier-filter-cb').forEach(cb => {
    cb.addEventListener('change', () => {
      state.tierFilter = [...document.querySelectorAll('.tier-filter-cb:checked')].map(el => parseInt(el.value, 10));
      const label = $('tier-filter-label');
      if (label) label.textContent = state.tierFilter.length
        ? state.tierFilter.map(c => CAPITAL_LABELS[c] || (c / 10000) + 'W').join('+')
        : '不限';
      if (state.data) drawPlan(state.data);
    });
  });

  const searchInput = $('q');
  if (searchInput) searchInput.addEventListener('input', () => {
    state.filter = searchInput.value.trim();
    if (state.data) drawPlan(state.data);
  });

  const btnBuild = $('btn-build');
  if (btnBuild) btnBuild.addEventListener('click', buildPlan);

  // Bootstrap Popover for tier-filter-help
  const tierHelp = document.getElementById('tier-filter-help');
  if (tierHelp && window.bootstrap && bootstrap.Popover) new bootstrap.Popover(tierHelp);

  // Capital tier buttons
  document.querySelectorAll('#capital-group .capital-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const c = parseInt(btn.dataset.capital, 10);
      if (state.capital === c) return;
      state.capital = c;
      document.querySelectorAll('#capital-group .capital-btn').forEach(b => {
        b.classList.remove('btn-primary');
        b.classList.add('btn-outline-primary');
      });
      btn.classList.remove('btn-outline-primary');
      btn.classList.add('btn-primary');
      if (state.data) drawPlan(state.data);
    });
  });

  // Set initial active capital button
  const activeBtn = document.querySelector(`#capital-group .capital-btn[data-capital="${state.capital}"]`);
  if (activeBtn) {
    activeBtn.classList.remove('btn-outline-primary');
    activeBtn.classList.add('btn-primary');
  }

  startDashboard();
  loadDates();
  loadHoldings();
});
