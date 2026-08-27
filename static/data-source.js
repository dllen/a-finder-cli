// 数据源抽象：API 模式（本地 Flask）与静态模式（Cloudflare Pages 预生成 JSON）共用同一套页面脚本。
// 页面在加载本文件前会注入 window.APP_MODE / window.DATA_PREFIX（见 app.py 的 __CONFIG__）。
window.APP_MODE = window.APP_MODE || 'api';
window.DATA_PREFIX = window.DATA_PREFIX || '';

function isStatic() { return window.APP_MODE === 'static'; }
function dsPath(file) { return window.DATA_PREFIX + file; }

function dsFetchPicks(date) {
  return isStatic()
    ? $.getJSON(dsPath('data/picks-' + date + '.json'))
    : $.getJSON('/api/picks', { date: date });
}

function dsFetchDates() {
  return isStatic()
    ? $.getJSON(dsPath('data/dates.json'))
    : $.getJSON('/api/dates');
}

function dsFetchPlanDates() {
  return isStatic()
    ? $.getJSON(dsPath('data/plan-dates.json'))
    : $.getJSON('/api/plan/dates');
}

function dsFetchPlan(date, includeFailed) {
  return isStatic()
    ? $.getJSON(dsPath('data/plan-' + date + '.json'))
    : $.getJSON('/api/plan/' + date, { include_failed: includeFailed ? '1' : '0' });
}

function dsFetchDashboard() {
  return isStatic()
    ? $.getJSON(dsPath('data/dashboard.json'))
    : $.getJSON('/api/dashboard');
}

function dsFetchHoldings() {
  return isStatic()
    ? $.getJSON(dsPath('data/holdings.json'))
    : $.getJSON('/api/holdings');
}

function dsPicksHref() { return isStatic() ? 'index.html' : '/'; }
function dsPlanHref() { return isStatic() ? 'plan.html' : '/plan'; }

$(function () {
  if (isStatic()) {
    // 静态部署：隐藏写入类控件（重算/同步/生成 plan/含 failed），并把导航链接改为相对路径。
    $('.write-control').hide();
    $('a[data-nav="picks"]').attr('href', 'index.html');
    $('a[data-nav="plan"]').attr('href', 'plan.html');
  }
});
