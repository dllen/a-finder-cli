// 10w 决策台：今日执行清单 + 执行对照 + 实际持仓录入

import { fetchDecidePlan, fetchDecidePositions, fetchDecideComparison, isStatic } from './api.js';
import { $, fmt, esc, setStatus } from './dom.js';

const SEV_CLS = { danger: 'bg-danger', warn: 'bg-warning text-dark', info: 'bg-info text-dark' };
const SEV_LABEL = { danger: '危险', warn: '警告', info: '提示' };
const KIND_LABEL = {
  missing_buy: '漏买', unplanned: '计划外持仓', too_many_positions: '持仓超限',
  single_weight_over: '单票超限', missing_stop: '缺止损', stop_breached: '跌破止损',
  exposure_over: '总仓位超限', cb_over: '转债超配', cb_too_many: '转债只数超限',
  tp_hit: '已达止盈', shares_mismatch: '股数不符',
};

function money(v) {
  return v == null ? '—' : '¥' + Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 0 });
}

// ---------------------------------------------------------------------------
// 摘要卡
// ---------------------------------------------------------------------------

function renderSummary(c) {
  const el = $('decide-summary');
  if (!el) return;
  const card = (title, value, sub, cls) => `
    <div class="col">
      <div class="card shadow-sm h-100 ${cls || ''}">
        <div class="card-body py-2 px-3">
          <div class="text-muted small mb-1">${title}</div>
          <div class="fs-5 fw-bold">${value}</div>
          <div class="text-muted small">${sub}</div>
        </div>
      </div>
    </div>`;
  el.innerHTML =
    card('股票占用', money(c.stock_used), `${c.actual_count} 只`) +
    card('转债占用', money(c.cb_used), `${c.cb_count} 只`) +
    card('现金余额', money(c.cash), c.cash < 0 ? '已超配' : '可用',
         c.cash < 0 ? 'text-danger' : '') +
    card('持仓数', c.actual_count, `计划 ${c.plan_count} 只`) +
    (c.actual_count === 0 && (c.pending_build || []).length
      ? card('待建仓', c.pending_build.length, '尚未录入持仓', 'border-info')
      : card('偏差', c.deviations.length,
          `危险 ${c.danger_count || 0} · 警告 ${c.warn_count || 0} · 提示 ${c.info_count || 0}`,
          c.danger_count ? 'border-danger' : ''));
}

// ---------------------------------------------------------------------------
// 今日执行清单
// ---------------------------------------------------------------------------

function renderPlan(date, plan) {
  const el = $('decide-plan');
  if (!el) return;
  const rows = plan.rows || [];
  const skipped = plan.skipped || [];
  if (!rows.length) {
    el.innerHTML = '<div class="empty-state">该日期暂无「买入信号」榜单</div>';
    return;
  }

  // 汇总条：目标单票 / 计划合计 / 剩余股票现金
  let html = `<div class="d-flex flex-wrap gap-3 align-items-center mb-2 small">
    <span>目标单票 <strong>${money(plan.target_amount)}</strong></span>
    <span>计划合计 <strong>${money(plan.planned_amount)}</strong></span>
    <span>股票仓 <strong>${money(plan.stock_capital)}</strong></span>
    <span class="${(plan.remaining_stock_cash || 0) < 0 ? 'text-danger' : 'text-muted'}">
      剩余 <strong>${money(plan.remaining_stock_cash)}</strong></span>
  </div>`;

  html += '<div class="table-responsive app-card d-none d-md-block mb-3"><table class="app-table"><thead><tr>' +
    '<th>排名</th><th>代码</th><th>名称</th><th class="num">计划价</th><th class="num">股数</th>' +
    '<th class="num">金额</th><th class="num">止损</th><th class="num">止盈</th><th class="num">评分</th>' +
    '</tr></thead><tbody>';
  rows.forEach(r => {
    html += `<tr><td><span class="rank-badge">${r.rank == null ? '' : r.rank}</span></td>` +
      `<td class="code">${esc(r.code)}</td>` +
      `<td>${esc(r.name || '—')}</td>` +
      `<td class="num">${fmt(r.plan_price)}</td>` +
      `<td class="num">${r.shares ?? '—'}</td>` +
      `<td class="num">${money(r.amount)}</td>` +
      `<td class="num">${fmt(r.stop_price)}</td>` +
      `<td class="num">${fmt(r.tp_price)}</td>` +
      `<td class="num">${r.score == null ? '—' : r.score.toFixed(1)}</td></tr>`;
  });
  html += '</tbody></table></div>';

  html += '<div class="app-card d-md-none mb-3">';
  rows.forEach(r => {
    html += `<div class="plan-card"><div class="card-header-row">` +
      `<span class="rank-badge">${r.rank == null ? '' : r.rank}</span>` +
      `<span class="code fw-semibold">${esc(r.code)}</span>` +
      `<span class="card-title-text">${esc(r.name || '')}</span>` +
      `<span class="score-chip">${r.score == null ? '—' : r.score.toFixed(0)}</span></div>` +
      `<div class="kv-grid">` +
        `<span class="k">计划价</span><span class="v num">${fmt(r.plan_price)}</span>` +
        `<span class="k">股数</span><span class="v num">${r.shares ?? '—'}（${money(r.amount)}）</span>` +
        `<span class="k">止损</span><span class="v num">${fmt(r.stop_price)}</span>` +
        `<span class="k">止盈</span><span class="v num">${fmt(r.tp_price)}</span>` +
      `</div></div>`;
  });
  html += '</div>';

  if (skipped.length) {
    html += `<div class="alert alert-warning py-2 mb-0">
      <strong>跳过 ${skipped.length} 只</strong>（一手金额超单票上限，10w 本金买不起）：
      ${skipped.map(s => `${esc(s.code)} ${esc(s.name)}（一手 ${money(s.lot_cost)}）`).join('、')}
    </div>`;
  }

  el.innerHTML = html;
}

// ---------------------------------------------------------------------------
// 执行对照
// ---------------------------------------------------------------------------

function renderComparison(c) {
  const el = $('decide-comparison');
  if (!el) return;
  const devs = c.deviations || [];
  const pending = c.pending_build || [];

  // 空仓：展示待建仓清单（不是"漏买"偏差）
  if (!c.actual_count && pending.length) {
    let html = `<div class="alert alert-info py-2 mb-2">
      当前空仓，以下 ${pending.length} 只是今日计划买入的标的 —— 这是<strong>待建仓清单</strong>，录入了实际持仓后才会开始对照。
    </div>`;
    html += '<div class="table-responsive app-card"><table class="app-table"><thead><tr>' +
      '<th>代码</th><th>名称</th><th class="num">计划价</th><th class="num">建议股数</th>' +
      '<th class="num">金额</th><th class="num">止损</th><th class="num">止盈</th>' +
      '</tr></thead><tbody>';
    let total = 0;
    pending.forEach(p => {
      total += p.amount || 0;
      html += `<tr><td class="code">${esc(p.code)}</td><td>${esc(p.name || '—')}</td>` +
        `<td class="num">${fmt(p.plan_price)}</td>` +
        `<td class="num">${p.shares ?? '—'}</td>` +
        `<td class="num">${money(p.amount)}</td>` +
        `<td class="num">${fmt(p.stop_price)}</td>` +
        `<td class="num">${fmt(p.tp_price)}</td></tr>`;
    });
    html += `</tbody><tfoot><tr><td colspan="4" class="text-end fw-semibold">合计</td>` +
      `<td class="num fw-semibold">${money(total)}</td><td colspan="2"></td></tr></tfoot></table></div>`;
    const cap = c.total_capital || 0;
    if (total > cap) {
      html += `<div class="alert alert-danger py-2 mt-2">合计 ${money(total)} 超出本金 ${money(cap)}，需按比例缩减</div>`;
    }
    el.innerHTML = html;
    return;
  }

  if (!devs.length) {
    el.innerHTML = '<div class="empty-state">无偏差：计划与实际一致</div>';
    return;
  }
  let html = '<div class="app-card mb-2">';
  devs.forEach(d => {
    const cls = SEV_CLS[d.severity] || 'bg-secondary';
    html += `<div class="border-bottom py-2 d-flex align-items-start gap-2">` +
      `<span class="badge ${cls}">${SEV_LABEL[d.severity] || d.severity}</span>` +
      `<div class="flex-grow-1">` +
        `<div class="fw-semibold">${esc(KIND_LABEL[d.kind] || d.kind)}` +
        `${d.code ? ` <span class="text-muted">${esc(d.code)} ${esc(d.name || '')}</span>` : ''}</div>` +
        `<div class="small">${esc(d.message)}</div>` +
      `</div></div>`;
  });
  html += '</div>';
  el.innerHTML = html;
}

// ---------------------------------------------------------------------------
// 实际持仓
// ---------------------------------------------------------------------------

function positionsTable(rows, opts) {
  const o = opts || {};
  if (!rows || !rows.length) {
    return `<div class="empty-state">${o.emptyText || '暂无持仓记录'}</div>`;
  }
  let html = '<div class="table-responsive app-card"><table class="app-table"><thead><tr>' +
    '<th>代码</th><th>名称</th><th class="num">股数</th><th class="num">成本价</th>' +
    '<th class="num">金额</th><th class="num">止损价</th><th>建仓日</th><th>操作</th>' +
    '</tr></thead><tbody>';
  rows.forEach(r => {
    const amount = (r.entry_price || 0) * (r.shares || 0);
    html += `<tr><td class="code">${esc(r.code)}</td><td>${esc(r.name || '—')}</td>` +
      `<td class="num">${r.shares}</td><td class="num">${fmt(r.entry_price)}</td>` +
      `<td class="num">${money(amount)}</td>` +
      `<td class="num">${r.stop_price == null ? '<span class="text-warning">未设</span>' : fmt(r.stop_price)}</td>` +
      `<td>${esc(r.entry_date || '—')}</td>` +
      `<td>${o.readonly ? '' : `<button class="btn btn-sm btn-outline-danger" data-del="${esc(r.code)}" data-cb="${o.isCb ? '1' : '0'}">删除</button>`}</td>` +
      `</tr>`;
  });
  html += '</tbody></table></div>';
  return html;
}

function renderPositions(rows, cbRows) {
  const stockEl = $('decide-positions');
  const cbEl = $('decide-cb-positions');
  if (stockEl) {
    stockEl.innerHTML = positionsTable(rows, { emptyText: '暂无实际持仓，点上方按钮添加' });
    if (!isStatic()) {
      stockEl.insertAdjacentHTML('beforeend',
        `<div class="mt-2"><button id="add-position" class="btn btn-sm btn-outline-primary">＋ 添加持仓</button></div>`);
    }
  }
  if (cbEl) {
    cbEl.innerHTML = positionsTable(cbRows, { emptyText: '暂无转债持仓', isCb: true, readonly: isStatic() });
    if (!isStatic()) {
      cbEl.insertAdjacentHTML('beforeend',
        `<div class="mt-2"><button id="add-cb-position" class="btn btn-sm btn-outline-primary">＋ 添加转债</button></div>`);
    }
  }
}

// ---------------------------------------------------------------------------
// 录入表单
// ---------------------------------------------------------------------------

function showAddForm(isCb) {
  const title = isCb ? '添加转债持仓' : '添加股票持仓';
  const fields = [
    ['code', '代码', 'text'],
    ['name', '名称', 'text'],
    ['shares', '股数', 'number'],
    ['entry_price', '成本价', 'number'],
    ['stop_price', '止损价（可空）', 'number'],
    ['entry_date', '建仓日', 'date'],
  ].map(([k, label, type]) =>
    `<div class="col-auto"><label class="form-label mb-0">${label}</label>` +
    `<input type="${type}" class="form-control form-control-sm" id="fld-${k}" data-key="${k}"></div>`
  ).join('');
  document.body.insertAdjacentHTML('beforeend',
    `<div class="modal fade" id="pos-modal" tabindex="-1">
      <div class="modal-dialog"><div class="modal-content">
        <div class="modal-header"><h6 class="modal-title">${title}</h6>
          <button type="button" class="btn-close" data-bs-dismiss="modal"></button></div>
        <div class="modal-body"><div class="row g-2">${fields}</div></div>
        <div class="modal-footer">
          <button class="btn btn-sm btn-secondary" data-bs-dismiss="modal">取消</button>
          <button class="btn btn-sm btn-primary" id="pos-save">保存</button>
        </div>
      </div></div>
    </div>`);
  const modal = document.getElementById('pos-modal');
  modal.classList.add('show');
  modal.style.display = 'block';
  document.getElementById('pos-save').addEventListener('click', async () => {
    const payload = { is_cb: !!isCb };
    modal.querySelectorAll('[data-key]').forEach(inp => {
      const k = inp.dataset.key;
      const v = inp.value.trim();
      payload[k] = v === '' ? null : (inp.type === 'number' ? Number(v) : v);
    });
    const resp = await fetch('/api/decide/positions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      setStatus('保存失败：' + (err.error || resp.status), 'alert-danger');
      return;
    }
    modal.remove();
    loadAll();
  });
}

// ---------------------------------------------------------------------------
// 加载
// ---------------------------------------------------------------------------

function fillDateOptions(dates) {
  const sel = $('decide-date');
  if (!sel) return;
  sel.innerHTML = '';
  (dates || []).forEach(d => sel.appendChild(new Option(d, d)));
  if (dates && dates.length) sel.value = dates[0];
}

async function loadAll() {
  setStatus('加载中…', 'alert-info');
  try {
    const planRes = await fetchDecidePlan().catch(() => ({ date: '', rows: [], dates: [] }));
    fillDateOptions(planRes.dates || []);
    const date = ($('decide-date') && $('decide-date').value) || planRes.date || '';

    const cmpRes = await fetchDecideComparison(date).catch(() => ({ deviations: [] }));
    const posRes = await fetchDecidePositions().catch(() => ({ rows: [], cb_rows: [] }));

    renderPlan(date, planRes);
    renderComparison(cmpRes);
    renderPositions(posRes.rows || [], posRes.cb_rows || []);
    renderSummary(cmpRes);
    setStatus('', '');
  } catch (e) {
    setStatus('加载失败：' + (e && e.message ? e.message : e), 'alert-danger');
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const sel = $('decide-date');
  if (sel) sel.addEventListener('change', loadAll);
  const btn = $('decide-refresh');
  if (btn) btn.addEventListener('click', loadAll);

  document.addEventListener('click', async e => {
    const add = e.target.closest('#add-position');
    if (add) { showAddForm(false); return; }
    const addCb = e.target.closest('#add-cb-position');
    if (addCb) { showAddForm(true); return; }
    const del = e.target.closest('[data-del]');
    if (del) {
      const code = del.dataset.del;
      const isCb = del.dataset.cb === '1';
      await fetch('/api/decide/positions/' + encodeURIComponent(code) + (isCb ? '?cb=1' : ''),
        { method: 'DELETE' });
      loadAll();
    }
  });

  loadAll();
});