// 持仓组合页入口

import { fetchPortfolioSummary, fetchPortfolioDetail, fetchPortfolioDates, isStatic } from './api.js?v=fa665a3';
import { fillDateSelect } from './dom.js?v=fa665a3';
import { setDateFilterState } from './date-filter.js?v=fa665a3';

const TIER_PALETTE = [
  '#60a5fa', '#34d399', '#fbbf24', '#f472b6', '#a78bfa',
  '#22d3ee', '#fb923c', '#4ade80', '#f87171', '#c084fc',
];

function tierCard(t, idx) {
  const color = TIER_PALETTE[idx % TIER_PALETTE.length];
  const sign = t.return_rate >= 0 ? '+' : '';
  return `
    <div class="card pf-tier-card" data-label="${t.label}" style="min-width:120px; cursor:pointer; border-left: 4px solid ${color};">
      <div class="card-body p-2 text-center">
        <div class="fw-bold fs-5" style="color:${color};">${t.label}</div>
        <div class="small text-muted">${(t.capital / 10000).toFixed(0)}万</div>
        <div class="fs-6 mt-1 ${t.return_rate >= 0 ? 'text-success' : 'text-danger'}">
          ${sign}${(t.return_rate * 100).toFixed(2)}%
        </div>
        <div class="small text-muted">¥${t.total_value.toFixed(0)}</div>
      </div>
    </div>`;
}

let _pfChart = null;

function drawReturnsChart(tiers) {
  const canvas = document.getElementById('pf-returns-chart');
  if (!canvas || typeof Chart === 'undefined') return;
  const ctx = canvas.getContext('2d');
  if (_pfChart) _pfChart.destroy();
  _pfChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: tiers.map(t => t.label),
      datasets: [{
        label: '累计收益率',
        data: tiers.map(t => +(t.return_rate * 100).toFixed(2)),
        backgroundColor: tiers.map((_, i) => TIER_PALETTE[i % TIER_PALETTE.length]),
        borderColor: tiers.map((_, i) => TIER_PALETTE[i % TIER_PALETTE.length]),
        borderWidth: 1,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: ctx => `${ctx.parsed.y.toFixed(2)}%` } },
      },
      scales: { y: { ticks: { callback: v => v + '%' } } },
    },
  });
}

async function selectTier(label, date, strategy) {
  document.querySelectorAll('.pf-tier-card').forEach(c => c.classList.remove('border-primary', 'shadow'));
  const active = document.querySelector(`.pf-tier-card[data-label="${label}"]`);
  if (active) active.classList.add('border-primary', 'shadow');
  try {
    const data = await fetchPortfolioDetail(label, date, strategy);
    renderDetail(data);
  } catch (e) { /* silent */ }
}

function renderDetail(d) {
  const detail = document.getElementById('pf-detail');
  if (!detail) return;
  const pnl = d.pnl;
  const used = pnl.initial_capital - pnl.cash_remaining;
  const util = (used / pnl.initial_capital * 100).toFixed(1);
  const nameLabel = it => `${it.code}<br><small class="text-muted">${it.name || ''}</small>`;

  detail.innerHTML = `
    <h5>${d.label} <small class="text-muted">资金 ${pnl.initial_capital.toLocaleString()} 元</small></h5>
    <div class="row g-2 mb-3">
      <div class="col-auto"><span class="badge bg-secondary">已用 ¥${used.toLocaleString()}</span></div>
      <div class="col-auto"><span class="badge bg-info">现金 ¥${pnl.cash_remaining.toLocaleString()}</span></div>
      <div class="col-auto"><span class="badge bg-warning text-dark">利用率 ${util}%</span></div>
      <div class="col-auto"><span class="badge ${pnl.unrealized_pnl >= 0 ? 'bg-success' : 'bg-danger'}">
        未实现 ¥${pnl.unrealized_pnl.toFixed(0)}
      </span></div>
      <div class="col-auto"><span class="badge bg-dark">已实现 ¥${pnl.realized_pnl.toFixed(0)}</span></div>
    </div>
    <h6>当日计划</h6>
    <table class="table table-sm">
      <thead><tr><th>动作</th><th>代码 / 名称</th><th>价格</th><th>仓位%</th><th>止损</th><th>止盈</th><th>状态</th></tr></thead>
      <tbody>
        ${d.plan_rows.map(r => `<tr>
          <td>${r.action}</td><td>${nameLabel(r)}</td>
          <td>${(r.plan_price || 0).toFixed(2)}</td>
          <td>${(r.size_pct * 100).toFixed(1)}%</td>
          <td>${(r.stop_price || 0).toFixed(2)}</td>
          <td>${(r.tp_price || 0).toFixed(2)}</td>
          <td>${r.status}</td>
        </tr>`).join('')}
      </tbody>
    </table>
    <h6>当前持仓</h6>
    <table class="table table-sm">
      <thead><tr><th>代码 / 名称</th><th>成本</th><th>现价</th><th>股数</th><th>浮盈</th></tr></thead>
      <tbody>
        ${d.holdings.items.map(it => `<tr>
          <td>${nameLabel(it)}</td>
          <td>${(it.entry_price || 0).toFixed(2)}</td>
          <td>${(it.current_price || 0).toFixed(2)}</td>
          <td>${it.shares}</td>
          <td class="${(it.floating_pnl || 0) >= 0 ? 'text-success' : 'text-danger'}">
            ¥${(it.floating_pnl || 0).toFixed(0)}
          </td>
        </tr>`).join('')}
      </tbody>
    </table>`;
}

async function loadDates() {
  try {
    const d = await fetchPortfolioDates();
    const v = fillDateSelect(d.dates || [], 'pf-date');
    if (!v) {
      setDateFilterState('pf-date', 'error', '暂无档位计划');
      return '';
    }
    setDateFilterState('pf-date', '', '');
    return v;
  } catch {
    setDateFilterState('pf-date', 'error', '计划日期加载失败');
    return '';
  }
}

async function loadPortfolio() {
  const dateEl = document.getElementById('pf-date');
  const stratEl = document.getElementById('pf-strategy');
  const date = dateEl?.value || '';
  const strategy = stratEl?.value || '';
  try {
    const data = await fetchPortfolioSummary(date, strategy);
    const cards = document.getElementById('pf-tier-cards');
    if (cards) {
      cards.innerHTML = data.tiers.map((t, i) => tierCard(t, i)).join('');
      cards.querySelectorAll('.pf-tier-card').forEach(el => {
        el.addEventListener('click', () => selectTier(el.dataset.label, date, strategy));
      });
    }
    drawReturnsChart(data.tiers);
    if (data.tiers.length) selectTier('10W', date, strategy);
  } catch (e) {
    console.error('loadPortfolio failed:', e);
  }
}

// Init
document.addEventListener('DOMContentLoaded', async () => {
  const staticMode = isStatic();
  if (staticMode) {
    // 静态站只有一份组合快照，没有日期/策略维度
    const d = document.getElementById('pf-date');
    const s = document.getElementById('pf-strategy');
    if (d) d.closest('.col-auto').style.display = 'none';
    if (s) s.closest('.col-auto').style.display = 'none';
  }

  document.getElementById('pf-date')?.addEventListener('change', loadPortfolio);
  document.getElementById('pf-strategy')?.addEventListener('change', loadPortfolio);

  // API 模式：先用计划日期填充日期筛选器，默认最新一天
  if (!staticMode) await loadDates();
  loadPortfolio();
});
