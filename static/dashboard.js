function freshnessBadge(f) {
  if (!f) return '<span class="badge bg-secondary">—</span>';
  return {fresh: 'bg-success', warm: 'bg-warning text-dark', stale: 'bg-danger'}[f] || 'bg-secondary';
}
function freshnessCn(f) { return {fresh:'新鲜', warm:'滞后', stale:'过期'}[f] || '—'; }

function sparkline(pnl) {
  if (!pnl.length) return '<span class="text-muted small">无</span>';
  const xs = [...pnl].reverse();
  const w = 80, h = 24, max = Math.max(...xs.map(p => Math.abs(p.pnl_amt)), 1);
  const step = w / Math.max(xs.length - 1, 1);
  const mid = h / 2;
  const pts = xs.map((p, i) => `${i * step},${mid - (p.pnl_amt / max) * mid}`).join(' ');
  const sum = xs.reduce((s, p) => s + p.pnl_amt, 0);
  const color = sum >= 0 ? '#198754' : '#dc3545';
  return `<svg width="${w}" height="${h}" style="vertical-align:middle"><polyline fill="none" stroke="${color}" stroke-width="1.5" points="${pts}"/></svg> <small class="${sum>=0?'text-success':'text-danger'}">${sum>=0?'+':''}${sum.toFixed(2)}元</small>`;
}

function renderDashboard(d) {
  const lr = d.last_refresh;
  const tp = d.today_plan;
  const op = d.open_positions;
  const pnl = d.pnl_5d;

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
         <span class="badge ${freshnessBadge(lr.freshness)} me-2">${freshnessCn(lr.freshness)}</span>
         <strong>${lr.date}</strong>
       </div>
       <small class="text-muted">${lr.ago_hours}h 前</small>`
    : '<span class="text-muted">无数据</span>';

  const tpHtml = `
    <div class="d-flex gap-2 flex-wrap mb-1">
      <span class="badge bg-success">买入 ${tp.buy}</span>
      <span class="badge bg-secondary">持有 ${tp.hold}</span>
      <span class="badge bg-warning text-dark">退出 ${tp.exit}</span>
      ${tp.failed ? `<span class="badge bg-danger">失败 ${tp.failed}</span>` : ''}
    </div>
    <small class="text-muted">合计仓位 ${(tp.size_total*100).toFixed(1)}% · <a href="${dsPlanHref()}">查看 →</a></small>`;

  const opHtml = op.count
    ? `<div><strong>${op.count}</strong> <small class="text-muted">只 · ${op.shares_total} 股</small></div>
       <small class="${(op.floating_pnl||0)>=0?'text-success':'text-danger'}">
         浮动 ${(op.floating_pnl||0)>=0?'+':''}${fmt(op.floating_pnl)} 元
       </small>
       <small class="text-muted d-block">均价浮动 ${op.avg_unrealized_pct==null?'—':(op.avg_unrealized_pct>=0?'+':'')+op.avg_unrealized_pct+'%'}</small>`
    : '<span class="text-muted">无持仓</span>';

  const pnlHtml = pnl.length
    ? sparkline(pnl)
    : '<span class="text-muted small">暂无收益</span>';

  $('#dashboard').html(`
    <div class="row row-cols-1 row-cols-md-2 row-cols-xl-4 g-2 mb-3">
      ${card('运行状态', lrHtml)}
      ${card('今日 plan', tpHtml)}
      ${card('持仓概览', opHtml)}
      ${card('最近 5 日收益', pnlHtml)}
    </div>`);
}

function startDashboard() {
  function tick() {
    if (document.hidden) return;
    dsFetchDashboard()
      .done(renderDashboard)
      .fail(function () {
        $('#dashboard').html('<div class="text-muted small mb-3">dashboard 刷新失败</div>');
      });
  }
  window.refreshDashboard = tick;
  tick();
  if (!isStatic()) {
    setInterval(tick, 15000);
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden) tick();
    });
  }
}