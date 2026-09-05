import sqlite3
import threading
import traceback
import urllib.parse
import uuid
from datetime import datetime as _dt_class

from flask import Flask, jsonify, request

from pick_history import run_picks
from config import CAPITAL_TIERS, DEFAULT_CAPITAL


# ---------------------------------------------------------------------------
# Shared UI shell: logo + top nav + footer + CSS
# ---------------------------------------------------------------------------

_LOGO = (
    '<svg class="brand-logo" viewBox="0 0 24 24" fill="none" '
    'xmlns="http://www.w3.org/2000/svg" aria-hidden="true">'
    '<rect x="4" y="9" width="2.4" height="8" rx="0.6" fill="#5b8def"/>'
    '<rect x="8.6" y="5" width="2.4" height="12" rx="0.6" fill="#34c98e"/>'
    '<rect x="13.2" y="11" width="2.4" height="6" rx="0.6" fill="#ef6c6c"/>'
    '<rect x="17.8" y="3" width="2.4" height="14" rx="0.6" fill="#34c98e"/>'
    '<path d="M3 19 L21 8" stroke="#8896a6" stroke-width="1.3" stroke-dasharray="3 2"/>'
    '</svg>'
)

# 同款 bar-chart 作为 favicon：内联 SVG data URI，免文件、Flask/静态站共用。
# 类名 / aria-hidden 等仅 nav 用得到的属性已剥离；viewBox 24x24 在 16/32px 下都清晰。
_FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    '<rect x="4" y="9" width="2.4" height="8" rx="0.6" fill="#5b8def"/>'
    '<rect x="8.6" y="5" width="2.4" height="12" rx="0.6" fill="#34c98e"/>'
    '<rect x="13.2" y="11" width="2.4" height="6" rx="0.6" fill="#ef6c6c"/>'
    '<rect x="17.8" y="3" width="2.4" height="14" rx="0.6" fill="#34c98e"/>'
    '<path d="M3 19 L21 8" stroke="#8896a6" stroke-width="1.3" stroke-dasharray="3 2" fill="none"/>'
    '</svg>'
)
_FAVICON_HREF = "data:image/svg+xml;utf8," + urllib.parse.quote(_FAVICON_SVG, safe="")

_APP_CSS = """:root{
  --brand:#16263a;
  --brand-2:#1f3350;
  --accent:#2f6f9f;
  --bg:#f5f6f8;
  --surface:#ffffff;
  --border:#e3e6eb;
  --text:#1a2332;
  --muted:#66707c;
}
html{font-size:15px}
@media (min-width:768px){html{font-size:16px}}
body{
  margin:0;background:var(--bg);color:var(--text);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",
    "Hiragino Sans GB","Microsoft YaHei",sans-serif;
  line-height:1.5;
  min-height:100vh;display:flex;flex-direction:column;
}
main{flex:1 0 auto;width:100%}
.app-nav{background:var(--brand);}
.app-nav .navbar-brand{
  display:flex;align-items:center;gap:.5rem;color:#fff;font-weight:600;font-size:1.05rem;
}
.app-nav .navbar-brand:hover{color:#fff}
.brand-logo{width:1.5rem;height:1.5rem;flex:none}
.app-nav .nav-link{color:#c6d0dc;padding:.6rem .75rem}
.app-nav .nav-link:hover{color:#fff}
.app-nav .nav-link.active{color:#fff;font-weight:600}
.navbar-toggler{border-color:rgba(255,255,255,.25);padding:.4rem .55rem}
.navbar-toggler:focus{box-shadow:0 0 0 .15rem rgba(255,255,255,.25)}
.navbar-toggler-icon{background-image:url("data:image/svg+xml,%3csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 30 30'%3e%3cpath stroke='rgba%28255, 255, 255, 0.85%29' stroke-linecap='round' stroke-miterlimit='10' stroke-width='2' d='M4 7h22M4 15h22M4 23h22'/%3e%3c/svg%3e")}
.app-footer{border-top:1px solid var(--border);color:var(--muted);font-size:.85rem;background:var(--surface)}
.page-header{display:flex;flex-direction:column;gap:.25rem;margin-bottom:1rem}
@media (min-width:768px){.page-header{flex-direction:row;align-items:baseline;justify-content:space-between;gap:1rem}}
.page-title{font-weight:600}
.filter-toolbar{display:flex;flex-wrap:wrap;gap:.5rem;align-items:center;margin-bottom:1rem}
.filter-group{display:flex;align-items:center;gap:.5rem}
.filter-toolbar .form-control{min-height:2.5rem}
.filter-toolbar .form-select{min-width:9rem;width:auto;min-height:2.5rem}
.filter-toolbar .form-control[type="search"]{flex:1 1 auto;min-width:12rem}
.filter-toolbar .btn{white-space:nowrap}
.filter-toolbar .form-check{padding-top:.25rem}
@media (max-width:767px){
  .filter-toolbar{flex-direction:column;align-items:stretch}
  .filter-group{align-self:flex-start}
  .filter-toolbar .form-control[type="search"]{width:100%}
  .filter-toolbar .btn,.filter-toolbar .form-check{align-self:flex-start}
}
.section-title{font-size:1rem;font-weight:600;margin-top:1.25rem;margin-bottom:.5rem}
@media (min-width:768px){.section-title{font-size:1.05rem;margin-top:1.5rem}}
.app-card{
  background:var(--surface);border:1px solid var(--border);border-radius:.5rem;
  overflow:hidden;
}
.app-card .app-table{margin-bottom:0}
.app-table{width:100%;border-collapse:collapse}
.app-table thead th{
  position:sticky;top:0;z-index:1;
  background:#fbfcfd;color:var(--muted);font-weight:600;font-size:.75rem;
  text-transform:uppercase;letter-spacing:.02em;
  border-bottom:1px solid var(--border);padding:.55rem .65rem;white-space:nowrap;
}
@media (min-width:768px){.app-table thead th{padding:.6rem .75rem;font-size:.8rem}}
.app-table tbody td{padding:.55rem .65rem;vertical-align:middle;border-bottom:1px solid #eef1f4}
@media (min-width:768px){.app-table tbody td{padding:.65rem .75rem}}
.app-table tbody tr:last-child td{border-bottom:none}
.app-table tbody tr:hover{background:#f6f9fc}
.app-table .num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.app-table th.num{text-align:right}
.app-table td.code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.9rem;white-space:nowrap}
.app-table tbody tr.row-hot{background:#fdecea}
.app-table tbody tr.row-hot:hover{background:#fbdcd8}
.app-table tbody tr.row-warm{background:#fff4e0}
.app-table tbody tr.row-warm:hover{background:#fceccc}
.app-table tbody tr.row-mild{background:#f7faf7}
.app-table tbody tr.row-mild:hover{background:#eef4ee}
.score-chip{display:inline-block;min-width:2.6rem;text-align:right;font-variant-numeric:tabular-nums}
.app-table thead th.sortable{cursor:pointer;user-select:none}
.app-table thead th.sortable:hover{color:var(--text)}
.app-table thead th.sorted{color:var(--accent)}
.legend-item{display:inline-flex;align-items:center;gap:.35rem;font-size:.8rem;color:var(--muted)}
@media (min-width:768px){.legend-item{font-size:.85rem}}
.sw{display:inline-block;width:.9rem;height:.9rem;border-radius:.25rem;border:1px solid rgba(0,0,0,.06)}
.sw-hot{background:#fdecea}
.sw-warm{background:#fff4e0}
.sw-mild{background:#f7faf7}
.sw-neutral{background:#ffffff;border-color:var(--border)}
.rank-badge{
  display:inline-flex;min-width:1.6rem;height:1.6rem;align-items:center;justify-content:center;
  border-radius:.35rem;background:#eef2f6;color:var(--muted);font-size:.75rem;font-weight:600;
}
@media (min-width:768px){.rank-badge{min-width:1.7rem;height:1.7rem;font-size:.8rem}}
.band-hot{background:#fdecea}
.band-warm{background:#fff4e0}
.band-mild{background:#f7faf7}
.band-neutral{background:#ffffff}
.pick-card,.plan-card{padding:.85rem .8rem;border-bottom:1px solid #eef1f4}
.pick-card:last-child,.plan-card:last-child{border-bottom:none}
@media (min-width:768px){.pick-card,.plan-card{padding:.65rem .75rem}}
.card-header-row{display:flex;align-items:center;gap:.5rem;margin-bottom:.35rem;min-height:1.8rem}
.card-header-row .code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.95rem}
.card-title-text{flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.card-score{font-weight:600;font-size:1rem;color:var(--accent)}
.kv-grid{display:grid;grid-template-columns:3.8rem 1fr;gap:.35rem .5rem;font-size:.9rem;align-items:baseline}
.kv-grid .k{color:var(--muted);font-size:.85rem;text-align:right}
.kv-grid .v{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.kv-grid .v.wrap{white-space:normal}
.kv-grid .num{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-variant-numeric:tabular-nums;text-align:right}
.empty-state{text-align:center;color:var(--muted);padding:2.5rem 1rem}
.skeleton{position:relative;overflow:hidden;background:#eef1f4;border-radius:.35rem}
.skeleton::after{
  content:"";position:absolute;inset:0;
  transform:translateX(-100%);
  background:linear-gradient(90deg,transparent,rgba(255,255,255,.6),transparent);
  animation:shimmer 1.2s infinite;
}
@keyframes shimmer{100%{transform:translateX(100%)}}
.skeleton-row{height:2.4rem;margin:.5rem 0}
.skeleton-table{padding:1rem}
.log-scroll{max-height:12rem;overflow-y:auto}
.form-label{font-weight:500;margin-bottom:0}
.summary-card .card-body{display:flex;flex-wrap:wrap;gap:.35rem .9rem}
.dashboard-card .card-body{min-height:4.5rem}
"""


def _footer() -> str:
    year = str(_dt_class.now().year)
    return (
        '<footer class="app-footer">'
        '<div class="container d-flex flex-column flex-md-row justify-content-between '
        'align-items-center py-3 gap-1">'
        f'<span>&copy; {year} 每日机会</span>'
        '<span>沪深300 选股与交易计划 &middot; 数据仅供参考，不构成投资建议</span>'
        '</div></footer>'
    )


def _nav(active: str) -> str:
    picks_cls = "nav-link active" if active == "picks" else "nav-link"
    plan_cls = "nav-link active" if active == "plan" else "nav-link"
    return (
        '<nav class="navbar navbar-expand-md app-nav">'
        '<div class="container">'
        f'<a class="navbar-brand" href="/">{_LOGO}<span>每日机会</span></a>'
        '<button class="navbar-toggler" type="button" data-bs-toggle="collapse" '
        'data-bs-target="#appNav" aria-controls="appNav" aria-expanded="false" '
        'aria-label="切换导航"><span class="navbar-toggler-icon"></span></button>'
        '<div class="collapse navbar-collapse" id="appNav">'
        '<div class="navbar-nav ms-auto">'
        f'<a class="{picks_cls}" data-nav="picks" href="/">每日机会</a>'
        f'<a class="{plan_cls}" data-nav="plan" href="/plan">交易计划</a>'
        '</div></div></div></nav>'
    )


_SHELL = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<link rel="icon" type="image/svg+xml" href="__FAVICON__">
<link rel="stylesheet" href="https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.3/css/bootstrap.min.css">
<style>__CSS__</style>
</head>
<body>
__NAV__
__BODY__
__FOOTER__
<script src="https://cdn.bootcdn.net/ajax/libs/jquery/3.7.1/jquery.min.js"></script>
<script src="https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.3/js/bootstrap.bundle.min.js"></script>
<script>__CONFIG__</script>
<script src="__ASSETS__common.js"></script>
<script src="__ASSETS__data-source.js"></script>
<script src="__ASSETS__dashboard.js"></script>
<script>
__SCRIPT__
</script>
</body>
</html>"""


def _page(title: str, active: str, body: str, script: str, config: str = "", assets: str = "/static/") -> str:
    return (
        _SHELL
        .replace("__TITLE__", title)
        .replace("__CSS__", _APP_CSS)
        .replace("__NAV__", _nav(active))
        .replace("__BODY__", body)
        .replace("__FOOTER__", _footer())
        .replace("__CONFIG__", config)
        .replace("__ASSETS__", assets)
        .replace("__FAVICON__", _FAVICON_HREF)
        .replace("__SCRIPT__", script)
    )


# ---------------------------------------------------------------------------
# Page bodies + scripts
# ---------------------------------------------------------------------------

PAGE_BODY = """<main class="container py-4">
  <div class="page-header">
    <h1 class="h3 mb-0 page-title">每日机会</h1>
    <span class="text-muted small" id="page-date">日期：{{today}}</span>
  </div>

  <div id="dashboard"></div>

  <div class="filter-toolbar">
    <div class="filter-group">
      <label class="form-label" for="d">日期</label>
      <select id="d" class="form-select"></select>
    </div>
    <input id="q" type="search" class="form-control" placeholder="筛选：代码 / 名称 / 策略" aria-label="快速筛选">
    <button id="btn-recalc" class="btn btn-outline-primary write-control">重算榜单</button>
    <button id="btn-sync" class="btn btn-primary write-control">同步行情并重算</button>
  </div>
  <div id="status" class="mb-3"></div>
  <div id="prog" class="mb-3"></div>
  <div id="log" class="mb-3"></div>
  <div id="board"></div>

  <h2 class="section-title mt-4">策略胜率统计 <small class="text-muted">基于历史标注样本</small></h2>
  <div id="stats"></div>
</main>"""

PAGE_SCRIPT = """var PICKS_STATE = { data: null, sort: { key: 'score', dir: -1 }, filter: '' };

function matchFilter(r, q){
  if (!q) return true;
  q = q.toLowerCase();
  return String(r.code).toLowerCase().indexOf(q) >= 0 ||
         String(r.name).toLowerCase().indexOf(q) >= 0 ||
         String(r.strategy).toLowerCase().indexOf(q) >= 0;
}

var PICKS_COLS = [
  { key: 'code',     label: '代码', type: 'str',  cls: '' },
  { key: 'name',     label: '名称', type: 'str',  cls: '' },
  { key: 'strategy', label: '策略', type: 'str',  cls: '' },
  { key: 'buy',      label: '买入', type: 'num',  cls: 'num' },
  { key: 'stop',     label: '止损', type: 'num',  cls: 'num' },
  { key: 'target',   label: '目标', type: 'num',  cls: 'num' },
  { key: 'ret_pct',  label: '涨跌%', type: 'num', cls: 'num' },
  { key: 'score',    label: '评分', type: 'num',  cls: 'num' }
];

function scoreBand(score, max) {
  if (score == null || max <= 0) return 'row-neutral';
  var ratio = score / max;
  if (ratio >= 0.85) return 'row-hot';
  if (ratio >= 0.7) return 'row-warm';
  if (ratio >= 0.5) return 'row-mild';
  return 'row-neutral';
}

function retCell(r){
  if (r.ret_pct == null) return '<td class="num text-muted">—</td>';
  var cls = r.ret_pct >= 0 ? 'text-success' : 'text-danger';
  var sign = r.ret_pct >= 0 ? '+' : '';
  return '<td class="num '+cls+'">'+sign+r.ret_pct.toFixed(2)+'</td>';
}

function sortRows(rs, key, dir) {
  if (!key) return rs;
  var col = PICKS_COLS.find(function(c){ return c.key === key; });
  var type = col ? col.type : 'num';
  return rs.slice().sort(function(a, b){
    var va = a[key], vb = b[key];
    if (va == null && vb == null) return 0;
    if (va == null) return 1;
    if (vb == null) return -1;
    if (type === 'num') return (va - vb) * dir;
    return String(va).localeCompare(String(vb), 'zh-CN') * dir;
  });
}

function drawBoard(){
  var groups = (PICKS_STATE.data && PICKS_STATE.data.groups) || {};
  var html = '';
  var anyGroup = false;   // 过滤前有可见组
  var anyVisible = false; // 过滤后有可见行
  ['均线','买入信号','信号策略','多因子'].forEach(function(kind){
    var rs = groups[kind] || [];
    if (!rs.length) return;
    var hasScore = rs.some(function(r){ return r.score != null; });
    if (!hasScore) return;  // 评分列为空 → 完全隐藏该组
    anyGroup = true;
    var s = PICKS_STATE.sort;
    var maxScore = rs.reduce(function(m,r){ return r.score!=null && r.score>m ? r.score : m; }, 0);
    var rows = sortRows(rs.filter(function(r){ return matchFilter(r, PICKS_STATE.filter); }), s.key, s.dir);
    if (!rows.length) return;
    anyVisible = true;
    var badge = (PICKS_STATE.filter && rows.length !== rs.length) ? (rows.length + ' / ' + rs.length) : ('Top ' + rows.length);
    html += '<h2 class="section-title">'+kind+' <span class="badge bg-light text-muted">'+badge+'</span></h2>';
    html += '<div class="table-responsive app-card d-none d-md-block mb-3"><table class="app-table"><thead><tr><th>排名</th>';
    PICKS_COLS.forEach(function(c){
      var active = s.key === c.key;
      var arrow = active ? (s.dir === -1 ? ' \u25BC' : ' \u25B2') : '';
      html += '<th class="'+c.cls+' sortable'+(active?' sorted':'')+'" data-sort="'+c.key+'">'+c.label+arrow+'</th>';
    });
    html += '</tr></thead><tbody>';
    rows.forEach(function(r, i){
      html += '<tr class="'+scoreBand(r.score, maxScore)+'"><td><span class="rank-badge">'+(i+1)+'</span></td>'+
        '<td class="code">'+esc(r.code)+'</td><td>'+esc(r.name)+'</td><td>'+esc(r.strategy)+'</td>'+
        '<td class="num">'+fmt(r.buy)+'</td><td class="num">'+fmt(r.stop)+'</td><td class="num">'+fmt(r.target)+'</td>'+
        retCell(r)+
        '<td class="num"><span class="score-chip">'+
        (r.score==null?'—':r.score)+'</span></td></tr>';
    });
    html += '</tbody></table></div>';
    // 移动端：grid 卡片，每行数据一张卡片
    html += '<div class="app-card d-md-none mb-3">';
    rows.forEach(function(r, i){
      html += '<div class="pick-card '+scoreBand(r.score, maxScore).replace('row-','band-')+'">'+
        '<div class="card-header-row">'+
          '<span class="rank-badge">'+(i+1)+'</span>'+
          '<span class="code fw-semibold">'+esc(r.code)+'</span>'+
          '<span class="card-title-text">'+esc(r.name)+'</span>'+
          '<span class="card-score">'+(r.score==null?'—':r.score)+'</span>'+
        '</div>'+
        '<div class="kv-grid">'+
          '<span class="k">策略</span><span class="v wrap">'+esc(r.strategy)+'</span>'+
          '<span class="k">买入</span><span class="v num">'+fmt(r.buy)+'</span>'+
          '<span class="k">止损</span><span class="v num">'+fmt(r.stop)+'</span>'+
          '<span class="k">目标</span><span class="v num">'+fmt(r.target)+'</span>'+
          '<span class="k">涨跌</span><span class="v num '+(r.ret_pct==null?'text-muted':(r.ret_pct>=0?'text-success':'text-danger'))+'">'+(r.ret_pct==null?'—':(r.ret_pct>=0?'+':'')+r.ret_pct.toFixed(2))+'</span>'+
        '</div>'+
      '</div>';
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
  $('#board').html(anyVisible ? html : '<div class="empty-state">' + (anyGroup ? '无匹配筛选结果' : '该日期暂无选股数据') + '</div>');
}

function drawStats(d){
  var rows = d.strategies || [];
  var html = '';
  if (rows.length) {
    html += '<div class="table-responsive app-card d-none d-md-block mb-3"><table class="app-table"><thead><tr>' +
      '<th>策略</th><th class="num">样本</th><th class="num">胜率%</th><th class="num">平均收益%</th></tr></thead><tbody>';
    rows.forEach(function(r){
      html += '<tr><td>'+esc(r.strategy)+'</td><td class="num">'+r.n+'</td>' +
        '<td class="num">'+r.win_rate+'</td>' +
        '<td class="num '+(r.expectancy>=0?'text-success':'text-danger')+'">'+(r.expectancy>=0?'+':'')+r.expectancy+'</td></tr>';
    });
    html += '</tbody></table></div>';
  }
  var mo = d.monthly || [];
  if (mo.length) {
    html += '<h2 class="section-title">月度胜率</h2>';
    html += '<div class="table-responsive app-card"><table class="app-table"><thead><tr>' +
      '<th>月份</th><th class="num">样本</th><th class="num">胜率%</th><th class="num">平均收益%</th></tr></thead><tbody>';
    mo.forEach(function(m){
      html += '<tr><td>'+esc(m.month)+'</td><td class="num">'+m.n+'</td>' +
        '<td class="num">'+m.win_rate+'</td>' +
        '<td class="num '+(m.avg_ret>=0?'text-success':'text-danger')+'">'+(m.avg_ret>=0?'+':'')+m.avg_ret+'</td></tr>';
    });
    html += '</tbody></table></div>';
  }
  $('#stats').html(html || '<div class="empty-state">暂无历史标注样本</div>');
}

function render(date){
  showBoardLoading();
  dsFetchPicks(date).done(function(data){
    whenBoardReady(function(){
      PICKS_STATE.data = data;
      PICKS_STATE.sort = { key: 'score', dir: -1 };
      drawBoard();
    });
  });
}
function loadDates(){
  dsFetchDates().done(function(d){
    var v = fillDateSelect(d.dates);
    if (v) render(v);
    else $('#board').html('<div class="empty-state">暂无选股数据</div>');
  }).fail(function(){
    $('#board').html('<div class="empty-state">日期加载失败</div>');
  });
}
function refresh(sync){
  $('#btn-recalc, #btn-sync').prop('disabled', true);
  setStatus('任务已提交…','alert-info');
  $.ajax({
    url: '/api/refresh',
    method: 'POST',
    contentType: 'application/json',
    data: JSON.stringify({sync: sync}),
    dataType: 'json'
  }).done(function(data){
    poll(data.job_id);
  }).fail(function(xhr){
    if (xhr.status === 409) setStatus('已有任务进行中','alert-warning');
    else setStatus('提交失败','alert-danger');
    $('#btn-recalc, #btn-sync').prop('disabled', false);
  });
}
function poll(jobId){
  $.getJSON('/api/jobs/'+jobId, function(data){
    setProgress(data.progress);
    setLog(data.log);
    if (data.status === 'pending' || data.status === 'running'){
      setStatus('任务进行中…','alert-info');
      setTimeout(function(){poll(jobId)}, 1000);
    } else if (data.status === 'done'){
      setStatus(data.message, 'alert-success');
      $('#btn-recalc, #btn-sync').prop('disabled', false);
      window.refreshDashboard && window.refreshDashboard();
      loadDates();
    } else {
      setStatus('任务失败：'+data.message, 'alert-danger');
      $('#btn-recalc, #btn-sync').prop('disabled', false);
    }
  }).fail(function(){
    setStatus('查询任务状态失败','alert-danger');
    $('#btn-recalc, #btn-sync').prop('disabled', false);
  });
}
$(function(){
  $('#d').on('change', function(){ render($(this).val()); });
  $('#q').on('input', function(){ PICKS_STATE.filter = $(this).val().trim(); drawBoard(); });
  $('#btn-recalc').on('click', function(){ refresh(false); });
  $('#btn-sync').on('click', function(){ refresh(true); });
  $('#board').on('click', 'th.sortable', function(){
    var key = $(this).data('sort');
    var s = PICKS_STATE.sort;
    if (s.key === key) {
      s.dir = -s.dir;
    } else {
      s.key = key; s.dir = -1;
    }
    drawBoard();
  });
  startDashboard();
  loadDates();
  $.getJSON('/api/stats', drawStats);
});"""

_TIER_FILTER_OPTIONS = "".join(
    f'<li><label class="dropdown-item"><input type="checkbox" class="form-check-input me-1 tier-filter-cb" value="{c}"> {c//10000}W</label></li>'
    for c in CAPITAL_TIERS
)

PLAN_BODY = """<main class="container py-4">
  <div class="page-header">
    <h1 class="h3 mb-0 page-title">交易计划 <span class="badge bg-secondary align-middle">paper</span></h1>
  </div>

  <div id="dashboard"></div>

  <div class="filter-toolbar">
    <div class="filter-group">
      <label class="form-label" for="d">日期</label>
      <select id="d" class="form-select"></select>
    </div>
    <div class="filter-group">
      <label class="form-label" for="capital-group">资金</label>
      <div id="capital-group" class="btn-group btn-group-sm flex-wrap" role="group" aria-label="资金档位">
        <button type="button" class="btn btn-outline-primary capital-btn" data-capital="50000">5W</button>
        <button type="button" class="btn btn-outline-primary capital-btn" data-capital="100000">10W</button>
        <button type="button" class="btn btn-outline-primary capital-btn" data-capital="150000">15W</button>
        <button type="button" class="btn btn-outline-primary capital-btn" data-capital="200000">20W</button>
        <button type="button" class="btn btn-outline-primary capital-btn" data-capital="250000">25W</button>
        <button type="button" class="btn btn-outline-primary capital-btn" data-capital="300000">30W</button>
        <button type="button" class="btn btn-outline-primary capital-btn" data-capital="350000">35W</button>
        <button type="button" class="btn btn-outline-primary capital-btn" data-capital="400000">40W</button>
        <button type="button" class="btn btn-outline-primary capital-btn" data-capital="450000">45W</button>
        <button type="button" class="btn btn-outline-primary capital-btn" data-capital="500000">50W</button>
      </div>
    </div>
    <div class="filter-group">
      <label class="form-label">档位筛选（交集）</label>
      <div class="dropdown" id="tier-filter-dd">
        <button id="tier-filter-btn" class="btn btn-outline-secondary btn-sm dropdown-toggle" type="button" data-bs-toggle="dropdown" aria-expanded="false">
          <span id="tier-filter-label">不限</span>
        </button>
        <ul class="dropdown-menu p-2" aria-labelledby="tier-filter-btn">{{tier_filter}}</ul>
      </div>
    </div>
    <input id="q" type="search" class="form-control" placeholder="筛选：代码 / 名称" aria-label="快速筛选">
    <button id="btn-build" class="btn btn-primary write-control">生成 plan</button>
    <div class="form-check write-control"><input id="include-failed" type="checkbox" class="form-check-input">
      <label for="include-failed" class="form-check-label">含 failed</label></div>
    <div class="form-check"><input id="only-affordable" type="checkbox" class="form-check-input">
      <label for="only-affordable" class="form-check-label">只显示可建仓</label></div>
  </div>
  <div id="status" class="mb-3"></div>
  <div id="prog" class="mb-3"></div>
  <div id="log" class="mb-3"></div>
  <div id="board"></div>

  <h2 class="section-title mt-4">持仓跟踪</h2>
  <div id="holdings"></div>
</main>"""

PLAN_SCRIPT = """var PLAN_STATE = { data: null, filter: '', capital: 100000, onlyAffordable: false, tierFilter: [] };
var CAPITAL_TIERS = [50000, 100000, 150000, 200000, 250000, 300000, 350000, 400000, 450000, 500000]; // 与 config.py CAPITAL_TIERS 同步（5W-50W step 5W，10 档）
var CAPITAL_LABELS = {};
CAPITAL_TIERS.forEach(function(c){ CAPITAL_LABELS[c] = (c/10000) + 'W'; });
function sizeShares(capital, sizePct, price){ // 与 shared_lib/strategy.py size_shares 同式
  if (!(capital > 0) || !(sizePct > 0) || !(price > 0)) return 0;
  return Math.floor(capital * sizePct / (price * 100)) * 100;
}
function rowShares(r){
  if (r.action !== 'buy') return (r.shares == null ? 0 : r.shares);
  return sizeShares(PLAN_STATE.capital, r.size_pct, r.plan_price);
}
function insufficientLot(r){
  return r.action === 'buy' && (r.size_pct || 0) > 0 && rowShares(r) === 0;
}
function tierSharesHtml(r){
  // 仅 buy 行渲染各档位股数子表；hold/exit 不随资金变化，跳过
  if (r.action !== 'buy' || !(r.plan_price > 0)) return '';
  var heads = [], bodyCells = [];
  CAPITAL_TIERS.forEach(function(c){
    var label = CAPITAL_LABELS[c] || (c/10000) + 'W';
    heads.push('<th class="tier-label" data-tier="'+c+'">'+label+'</th>');
    var s = sizeShares(c, r.size_pct, r.plan_price);
    var cell = s > 0 ? s : '<span class="text-warning">0</span>';
    bodyCells.push('<td class="num" data-tier="'+c+'">'+cell+'</td>');
  });
  return '<details class="tier-shares-wrap"><summary class="text-muted">各档股数 5W-50W</summary>' +
    '<table class="tier-shares-table table table-sm table-borderless mb-0 mt-1"><thead><tr>'+
    heads.join('')+'</tr></thead><tbody><tr>'+bodyCells.join('')+'</tr></tbody></table></details>';
}
function matchFilter(r, q){
  if (!q) return true;
  q = q.toLowerCase();
  return String(r.code).toLowerCase().indexOf(q) >= 0 ||
         String(r.name||'').toLowerCase().indexOf(q) >= 0;
}
var ACTION_META = {
  buy:  {label: '买入', cls: 'bg-success'},
  hold: {label: '持有', cls: 'bg-secondary'},
  exit: {label: '退出', cls: 'bg-warning text-dark'}
};
function actionMeta(a){ return ACTION_META[a] || {label:a, cls:'bg-light text-dark'}; }
function statusBadge(s){ return s==='ok' ? 'bg-success' : 'bg-danger'; }
function row(r){
  var meta = actionMeta(r.action);
  var sizePct = r.size_pct==null ? '—' : (r.size_pct*100).toFixed(1) + '%';
  var sharesHtml = insufficientLot(r) ? '0 <span class="text-warning small">资金不足一手</span>' : rowShares(r);
  // rationale_json 解析一次：策略提取 + 折叠详情共用
  var rationale = '<span class="text-muted">—</span>';
  var strategy = '—';
  try {
    var obj = JSON.parse(r.rationale_json || '{}');
    if (obj && obj.strategy) strategy = String(obj.strategy);
    var keys = Object.keys(obj || {});
    rationale = keys.length
      ? '<details><summary class="text-muted">'+keys.length+' 字段</summary>' +
        '<pre class="mb-0 small">' + JSON.stringify(obj, null, 2) + '</pre></details>'
      : '<span class="text-muted">—</span>';
  } catch(e) {
    rationale = '<small class="text-muted font-monospace">'+(r.rationale_json||'—')+'</small>';
  }
  return '<tr><td class="code">'+esc(r.code)+'</td>' +
    '<td>'+(r.name?esc(r.name):'<span class="text-muted">—</span>')+'</td>' +
    '<td><span class="badge '+meta.cls+'">'+meta.label+'</span></td>' +
    '<td>'+esc(strategy)+'</td>' +
    '<td class="num">'+fmt(r.plan_price)+'</td>' +
    '<td class="num">'+sizePct+'</td>' +
    '<td class="num">'+sharesHtml+'</td>' +
    '<td class="num">'+fmt(r.stop_price)+'</td>' +
    '<td class="num">'+fmt(r.tp_price)+'</td>' +
    '<td class="num">'+fmt(r.rr_ratio)+'</td>' +
    '<td><span class="badge '+statusBadge(r.status)+'">'+r.status+'</span>' +
      (r.reason? ' <small class="text-muted">'+esc(r.reason)+'</small>':'')+'</td>' +
    '<td>'+tierSharesHtml(r)+rationale+'</td></tr>';
}
function render(date){
  showBoardLoading();
  var includeFailed = $('#include-failed').is(':checked') ? '1' : '0';
  dsFetchPlan(date, includeFailed === '1').done(function(data){
    whenBoardReady(function(){ PLAN_STATE.data = data; drawPlan(data); });
  }).fail(function(){ setStatus('加载失败','alert-danger'); });
}
function drawPlan(data){
    var allRows = (data.rows || []);
    var rows = allRows.filter(function(r){ return matchFilter(r, PLAN_STATE.filter); })
                      .filter(function(r){ return !PLAN_STATE.onlyAffordable || !insufficientLot(r); })
                      .filter(function(r){
                        // 档位筛选（交集）：空表示不限；非空时 row 必须在每个选中档位下都能建仓
                        if (!PLAN_STATE.tierFilter.length) return true;
                        for (var i = 0; i < PLAN_STATE.tierFilter.length; i++){
                          if (sizeShares(PLAN_STATE.tierFilter[i], r.size_pct, r.plan_price) === 0) return false;
                        }
                        return true;
                      });
    if (!rows.length) { $('#board').html('<div class="empty-state">' + (allRows.length ? '无匹配筛选结果' : '该日期暂无 plan') + '</div>'); return; }
    var groups = {buy:[], hold:[], exit:[]};
    rows.forEach(function(r){
      (groups[r.action] || (groups[r.action]=[])).push(r);
    });
    // 组合级汇总（买入合计/已用/现金/失败）基于全计划，不受筛选影响
    var buySize = 0, buyShares = 0, usedCapital = 0, failed = 0;
    allRows.forEach(function(r){
      if (r.action === 'buy' && r.status === 'ok' && r.size_pct != null) {
        buySize += r.size_pct;
        var sh = rowShares(r);
        buyShares += sh;
        usedCapital += sh * (r.plan_price || 0);
      }
      if (r.status === 'failed') failed++;
    });
    var cash = PLAN_STATE.capital - usedCapital;
    var util = PLAN_STATE.capital > 0 ? usedCapital / PLAN_STATE.capital : 0;
    var capLabel = CAPITAL_LABELS[PLAN_STATE.capital] || (PLAN_STATE.capital/10000 + 'W');
    var counts = Object.entries(groups).filter(function(e){return e[1].length}).map(function(e){return actionMeta(e[0]).label+' '+e[1].length;}).join(' · ');
    var summary = '<div class="card summary-card mb-3"><div class="card-body py-2">' +
      '<span class="me-3"><strong>'+data.plan_date+'</strong></span>' +
      '<span class="text-muted me-3">'+rows.length+' 行</span>' +
      '<span class="text-muted me-3">'+counts+'</span>' +
      '<span class="me-3">资金 '+capLabel+'</span>' +
      '<span class="me-3 '+(util>1?'text-danger':'text-muted')+'">已用 ¥'+fmt(usedCapital)+' ('+(util*100).toFixed(1)+'%)</span>' +
      '<span class="me-3 '+(cash<0?'text-danger':'text-muted')+'">现金 ¥'+fmt(cash)+'</span>' +
      '<span class="text-muted me-3">买入合计仓位 '+ (buySize*100).toFixed(1) +'% · '+ buyShares +' 股</span>' +
      '<span class="text-muted">失败 '+failed+'</span>' +
      '</div></div>';
    var html = summary;
    ['buy','hold','exit'].forEach(function(a){
      var rs = groups[a] || [];
      if (!rs.length) return;
      var meta = actionMeta(a);
      html += '<h2 class="section-title"><span class="badge '+meta.cls+'">'+meta.label+'</span> <small class="text-muted">'+rs.length+' 只</small></h2>';
      html += '<div class="table-responsive app-card d-none d-md-block mb-3"><table class="app-table"><thead><tr>' +
        '<th>代码</th><th>名称</th><th>方向</th><th>策略</th><th class="num">计划价</th><th class="num">仓位</th><th class="num">股数</th><th class="num">止损</th><th class="num">止盈</th><th class="num">RR</th><th>状态</th><th>理由</th>' +
        '</tr></thead><tbody>' + rs.map(row).join('') + '</tbody></table></div>';
      // 移动端：grid 卡片，每行数据一张卡片
      html += '<div class="app-card d-md-none mb-3">';
      rs.forEach(function(r){
        var sizePct = r.size_pct==null ? '—' : (r.size_pct*100).toFixed(1) + '%';
        var meta = actionMeta(r.action);
        var cardStrategy = '—';
        try { var _so = JSON.parse(r.rationale_json || '{}'); if (_so && _so.strategy) cardStrategy = String(_so.strategy); } catch(e) {}
        html += '<div class="plan-card">'+
          '<div class="card-header-row">'+
            '<span class="code fw-semibold">'+esc(r.code)+'</span>'+
            '<span class="card-title-text">'+esc(r.name||'')+'</span>'+
            '<span class="badge '+meta.cls+'">'+meta.label+'</span>'+
            '<span class="badge '+statusBadge(r.status)+'">'+esc(r.status)+'</span>'+
          '</div>'+
          '<div class="kv-grid">'+
            '<span class="k">策略</span><span class="v wrap">'+esc(cardStrategy)+'</span>'+
            '<span class="k">计划价</span><span class="v num">'+fmt(r.plan_price)+'</span>'+
            '<span class="k">仓位</span><span class="v num">'+sizePct+'</span>'+
            '<span class="k">股数</span><span class="v num">'+(insufficientLot(r)?'0 <span class="text-warning small">资金不足一手</span>':rowShares(r))+'</span>'+
            '<span class="k">止损</span><span class="v num">'+fmt(r.stop_price)+'</span>'+
            '<span class="k">止盈</span><span class="v num">'+fmt(r.tp_price)+'</span>'+
            '<span class="k">RR</span><span class="v num">'+fmt(r.rr_ratio)+'</span>'+
          '</div>'+
          (r.reason? '<div class="small text-muted mt-1">'+esc(r.reason)+'</div>':'')+
          tierSharesHtml(r)+
        '</div>';
      });
      html += '</div>';
    });
    $('#board').html(html);
}
function loadDates(){
  dsFetchPlanDates().done(function(d){
    var v = fillDateSelect(d.dates);
    if (v) render(v);
    else $('#board').html('<div class="empty-state">暂无 plan 数据</div>');
  }).fail(function(){
    $('#board').html('<div class="empty-state">日期加载失败</div>');
  });
}
function buildPlan(){
  var date = $('#d').val();
  if (!date) { setStatus('请先选日期','alert-warning'); return; }
  $('#btn-build').prop('disabled', true);
  setStatus('任务已提交…','alert-info');
  setLog(null);
  setProgress(null);
  $.ajax({
    url: '/api/plan/build',
    method: 'POST',
    contentType: 'application/json',
    data: JSON.stringify({plan_date: date, capital: PLAN_STATE.capital}),
    dataType: 'json'
  }).done(function(data){
    pollBuild(data.job_id);
  }).fail(function(xhr){
    if (xhr.status === 409) setStatus('已有任务进行中','alert-warning');
    else setStatus('提交失败','alert-danger');
    $('#btn-build').prop('disabled', false);
  });
}
function pollBuild(jobId){
  $.getJSON('/api/jobs/'+jobId, function(data){
    setProgress(data.progress);
    setLog(data.log);
    if (data.status === 'pending' || data.status === 'running'){
      setStatus('任务进行中…','alert-info');
      setTimeout(function(){pollBuild(jobId)}, 1000);
    } else if (data.status === 'done'){
      setStatus(data.message, 'alert-success');
      $('#btn-build').prop('disabled', false);
      window.refreshDashboard && window.refreshDashboard();
      loadDates();
      loadHoldings();
    } else {
      setStatus('任务失败：'+data.message, 'alert-danger');
      $('#btn-build').prop('disabled', false);
    }
  }).fail(function(){
    setStatus('查询任务状态失败','alert-danger');
    $('#btn-build').prop('disabled', false);
  });
}
function drawHoldings(d){
  var rows = d.holdings || [];
  var s = d.summary || {};
  var sumHtml = '<div class="card summary-card mb-3"><div class="card-body py-2">' +
    '<span class="me-3"><strong>持仓 '+ s.open_count +'</strong></span>' +
    '<span class="me-3">总股数 '+ (s.shares_total||0) +'</span>' +
    '<span class="me-3">浮动 ' + (s.floating_pnl>=0?'+':'') + fmt(s.floating_pnl) + ' 元</span>' +
    '<span class="me-3">已实现 ' + (s.realized_pnl>=0?'+':'') + fmt(s.realized_pnl) + ' 元</span>' +
    '<span class="me-3"><strong>总收益 ' + (s.total_pnl>=0?'+':'') + fmt(s.total_pnl) + ' 元</strong></span>' +
    '<span class="'+(s.return_pct>=0?'text-success':'text-danger')+'">'+(s.return_pct>=0?'+':'')+fmt(s.return_pct)+'%</span>' +
    '</div></div>';
  if (!rows.length) { $('#holdings').html(sumHtml + '<div class="empty-state">暂无持仓</div>'); return; }
  var h = sumHtml + '<div class="table-responsive app-card d-none d-md-block mb-3"><table class="app-table"><thead><tr>' +
    '<th>代码</th><th>名称</th><th class="num">股数</th><th class="num">加权均价</th><th class="num">现价</th>' +
    '<th class="num">止损</th><th class="num">止盈</th><th class="num">浮动盈亏</th><th class="num">止损预期</th><th class="num">止盈预期</th>' +
    '</tr></thead><tbody>' +
    rows.map(function(r){
      return '<tr><td class="code">'+esc(r.code)+'</td><td>'+esc(r.name||'—')+'</td>' +
        '<td class="num">'+r.shares+'</td><td class="num">'+fmt(r.entry_price)+'</td><td class="num">'+fmt(r.current_price)+'</td>' +
        '<td class="num">'+fmt(r.stop_price)+'</td><td class="num">'+fmt(r.tp_price)+'</td>' +
        '<td class="num '+(r.floating_pnl>=0?'text-success':'text-danger')+'">'+(r.floating_pnl==null?'—':(r.floating_pnl>=0?'+':'')+fmt(r.floating_pnl))+'</td>' +
        '<td class="num '+(r.stop_pnl>=0?'text-success':'text-danger')+'">'+(r.stop_pnl==null?'—':(r.stop_pnl>=0?'+':'')+fmt(r.stop_pnl))+'</td>' +
        '<td class="num '+(r.tp_pnl>=0?'text-success':'text-danger')+'">'+(r.tp_pnl==null?'—':(r.tp_pnl>=0?'+':'')+fmt(r.tp_pnl))+'</td></tr>';
    }).join('') + '</tbody></table></div>';
  // 移动端：卡片
  h += '<div class="app-card d-md-none mb-3">';
  rows.forEach(function(r){
    h += '<div class="plan-card">'+
      '<div class="card-header-row">'+
        '<span class="code fw-semibold">'+esc(r.code)+'</span>'+
        '<span class="card-title-text">'+esc(r.name||'')+'</span>'+
        '<span class="num '+(r.floating_pnl>=0?'text-success':'text-danger')+'">'+(r.floating_pnl==null?'—':(r.floating_pnl>=0?'+':'')+fmt(r.floating_pnl))+'</span>'+
      '</div>'+
      '<div class="kv-grid">'+
        '<span class="k">股数</span><span class="v num">'+r.shares+'</span>'+
        '<span class="k">均价</span><span class="v num">'+fmt(r.entry_price)+'</span>'+
        '<span class="k">现价</span><span class="v num">'+fmt(r.current_price)+'</span>'+
        '<span class="k">止损</span><span class="v num">'+fmt(r.stop_price)+'</span>'+
        '<span class="k">止盈</span><span class="v num">'+fmt(r.tp_price)+'</span>'+
      '</div>'+
    '</div>';
  });
  h += '</div>';
  $('#holdings').html(h);
}
function loadHoldings(){ dsFetchHoldings().done(drawHoldings).fail(function(){ $('#holdings').html('<div class="text-muted small">持仓加载失败</div>'); }); }
$(function(){
  $('#d').on('change', function(){ render($(this).val()); });
  $('#include-failed').on('change', function(){ var d = $('#d').val(); if (d) render(d); });
  $('#only-affordable').on('change', function(){ PLAN_STATE.onlyAffordable = $(this).is(':checked'); if (PLAN_STATE.data) drawPlan(PLAN_STATE.data); });
  $('.tier-filter-cb').on('change', function(){
    PLAN_STATE.tierFilter = $('.tier-filter-cb:checked').map(function(){ return parseInt($(this).val(), 10); }).get();
    $('#tier-filter-label').text(PLAN_STATE.tierFilter.length
      ? PLAN_STATE.tierFilter.map(function(c){ return CAPITAL_LABELS[c] || (c/10000)+'W'; }).join('+')
      : '不限');
    if (PLAN_STATE.data) drawPlan(PLAN_STATE.data);
  });
  $('#q').on('input', function(){ PLAN_STATE.filter = $(this).val().trim(); if (PLAN_STATE.data) drawPlan(PLAN_STATE.data); });
  $('#btn-build').on('click', buildPlan);
  $('#capital-group .capital-btn').on('click', function(){
    var c = parseInt($(this).attr('data-capital'), 10);
    if (PLAN_STATE.capital === c) return;
    PLAN_STATE.capital = c;
    $('#capital-group .capital-btn').removeClass('btn-primary').addClass('btn-outline-primary');
    $(this).removeClass('btn-outline-primary').addClass('btn-primary');
    if (PLAN_STATE.data) drawPlan(PLAN_STATE.data);
  });
  $('#capital-group .capital-btn[data-capital="'+PLAN_STATE.capital+'"]')
    .removeClass('btn-outline-primary').addClass('btn-primary');
  startDashboard();
  loadDates();
  loadHoldings();
});"""


JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def _start_plan_job(db_path, plan_date, capital):
    from config import MAX_SINGLE, MAX_TOTAL, RR_TARGET, SLIPPAGE
    from plan_builder import build_plan

    with JOBS_LOCK:
        if any(j["status"] in ("pending", "running") for j in JOBS.values()):
            return None
        job_id = uuid.uuid4().hex
        JOBS[job_id] = {"status": "pending", "message": "", "progress": 0, "log": []}

    def on_progress(pct, msg):
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if job is None:
                return
            job["progress"] = pct
            job["log"].append(msg)
            if len(job["log"]) > 200:
                job["log"] = job["log"][-200:]

    def work():
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "running"
        try:
            on_progress(10, f"读取 {plan_date} 的 picks / open positions")
            params = {
                "max_single": MAX_SINGLE,
                "max_total": MAX_TOTAL,
                "rr_target": RR_TARGET,
                "regime": "sideways",
                "capital": capital,
            }
            on_progress(40, f"参数：{params} slippage={SLIPPAGE}")
            result = build_plan(plan_date, db_path, params, slippage=SLIPPAGE)
            on_progress(95, f"picks={result.num_picks} open={result.num_open_positions}")
            msg = (
                f"plan_date={plan_date} picks={result.num_picks} "
                f"open={result.num_open_positions} sanity={result.sanity_passed}"
            )
            if result.sanity_reasons:
                msg += f" reasons={result.sanity_reasons}"
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if job is not None:
                    job["status"] = "done"
                    job["message"] = msg
                    job["progress"] = 100
        except Exception as e:  # noqa: BLE001
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if job is not None:
                    job["status"] = "error"
                    job["message"] = traceback.format_exc(limit=3)

    threading.Thread(target=work, daemon=True).start()
    return job_id


def _start_job(db_path, top, do_sync):
    with JOBS_LOCK:
        if any(j["status"] in ("pending", "running") for j in JOBS.values()):
            return None
        job_id = uuid.uuid4().hex
        JOBS[job_id] = {"status": "pending", "message": "", "progress": 0, "log": []}

    def on_progress(pct, msg):
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if job is None:
                return
            job["progress"] = pct
            job["log"].append(msg)
            if len(job["log"]) > 200:
                job["log"] = job["log"][-200:]

    def work():
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "running"
        try:
            result = run_picks(db_path, top, do_sync, on_progress)
            msg = f"日期 {result.get('date') or '-'}：均线 {result.get('ma') or 0} 条 / 买入信号 {result.get('buy') or 0} 条 / 信号策略 {result.get('signal') or 0} 条 / 多因子 {result.get('multi') or 0} 条"
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if job is not None:
                    job["status"] = "done"
                    job["message"] = msg
                    job["progress"] = 100
        except Exception as e:  # noqa: BLE001
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if job is not None:
                    job["status"] = "error"
                    job["message"] = traceback.format_exc(limit=3)

    threading.Thread(target=work, daemon=True).start()
    return job_id


def open_conn(db_path):
    # ponytail: use open_db so fresh/existing DBs get schema + pending migrations applied.
    # Was raw sqlite3.connect, which broke when the dashboard migration added updated_at.
    from db_repository import open_db
    return open_db(db_path)


def list_dates(conn):
    rows = conn.execute("SELECT DISTINCT date FROM daily_picks ORDER BY date DESC").fetchall()
    return [r[0] for r in rows]


def picks_for_date(conn, date):
    rows = conn.execute(
        "SELECT rank, kind, code, name, strategy, buy, stop, target, score FROM daily_picks WHERE date = ? ORDER BY kind, rank",
        (date,),
    ).fetchall()
    return [
        {"rank": r[0], "kind": r[1], "code": r[2], "name": r[3], "strategy": r[4],
         "buy": r[5], "stop": r[6], "target": r[7], "score": r[8]}
        for r in rows
    ]


def latest_close_map(conn):
    rows = conn.execute(
        "SELECT code, close FROM daily_prices "
        "WHERE (code, trade_date) IN (SELECT code, MAX(trade_date) FROM daily_prices GROUP BY code)"
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def create_app(db_path="hs300.db", top=10):
    app = Flask(__name__)

    @app.get("/")
    def index():
        from datetime import date as _date
        today = _date.today().isoformat()
        return _page(
            "每日机会", "picks",
            PAGE_BODY.replace("{{today}}", today),
            PAGE_SCRIPT,
        )

    @app.get("/plan")
    def plan_page():
        from datetime import date as _date
        today = _date.today().isoformat()
        return _page(
            "每日 Plan", "plan",
            PLAN_BODY.replace("{{today}}", today).replace("{{tier_filter}}", _TIER_FILTER_OPTIONS),
            PLAN_SCRIPT,
        )

    @app.get("/api/dates")
    def dates():
        conn = open_conn(db_path)
        ds = list_dates(conn)
        conn.close()
        return jsonify({"dates": ds})

    @app.get("/api/picks")
    def picks():
        date = request.args.get("date", "")
        conn = open_conn(db_path)
        if not date:
            ds = list_dates(conn)
            date = ds[0] if ds else ""
        groups = {}
        if date:
            lc = latest_close_map(conn)
            for row in picks_for_date(conn, date):
                c = lc.get(row["code"])
                row["ret_pct"] = round((c / row["buy"] - 1) * 100, 2) if (c and row["buy"]) else None
                groups.setdefault(row["kind"], []).append(row)
        conn.close()
        return jsonify({"date": date, "groups": groups})

    @app.post("/api/refresh")
    def refresh():
        body = request.get_json(silent=True) or {}
        do_sync = bool(body.get("sync", False))
        job_id = _start_job(db_path, top, do_sync)
        if job_id is None:
            return jsonify({"error": "已有任务进行中"}), 409
        return jsonify({"job_id": job_id}), 202

    @app.post("/api/plan/build")
    def plan_build():
        body = request.get_json(silent=True) or {}
        plan_date = (body.get("plan_date") or "").strip()
        if not plan_date:
            return jsonify({"error": "plan_date 必填"}), 400
        capital = body.get("capital")
        try:
            capital = int(capital)
        except (TypeError, ValueError):
            capital = DEFAULT_CAPITAL
        if capital not in CAPITAL_TIERS:
            capital = DEFAULT_CAPITAL
        job_id = _start_plan_job(db_path, plan_date, capital)
        if job_id is None:
            return jsonify({"error": "已有任务进行中"}), 409
        return jsonify({"job_id": job_id}), 202

    @app.get("/api/jobs/<job_id>")
    def job(job_id):
        with JOBS_LOCK:
            data = JOBS.get(job_id)
        if data is None:
            return jsonify({"error": "任务不存在"}), 404
        return jsonify(data)

    # ---- Plan (paper trading) ----
    from datetime import date as _date
    from db_repository import get_trade_plan_by_date as _get_plan

    @app.get("/api/plan/dates")
    def plan_dates():
        conn = open_conn(db_path)
        cur = conn.execute(
            "SELECT DISTINCT plan_date FROM trade_plan ORDER BY plan_date DESC"
        )
        ds = [r[0] for r in cur.fetchall()]
        conn.close()
        return jsonify({"dates": ds})

    @app.get("/api/plan/today")
    def plan_today():
        conn = open_conn(db_path)
        rows = _get_plan(conn, _date.today().isoformat())
        conn.close()
        return jsonify({"plan_date": _date.today().isoformat(), "rows": rows})

    @app.get("/api/plan/<plan_date>")
    def plan_by_date(plan_date):
        include_failed = request.args.get("include_failed", "0") == "1"
        conn = open_conn(db_path)
        rows = _get_plan(conn, plan_date, include_failed=include_failed)
        conn.close()
        return jsonify({"plan_date": plan_date, "rows": rows})

    @app.get("/api/holdings")
    def holdings():
        from db_repository import get_holdings_detail
        conn = open_conn(db_path)
        try:
            return jsonify(get_holdings_detail(conn))
        finally:
            conn.close()

    @app.get("/api/dashboard")
    def dashboard():
        from datetime import date as _date
        from db_repository import (
            get_last_refresh,
            get_today_plan_summary,
            get_open_positions_with_unrealized,
            get_recent_pnl,
            get_holdings_detail,
        )
        conn = open_conn(db_path)
        try:
            last = get_last_refresh(conn)
            today = get_today_plan_summary(conn, _date.today().isoformat())
            opens = get_open_positions_with_unrealized(conn)
            pnl = get_recent_pnl(conn, days=5)
            hd = get_holdings_detail(conn)
        finally:
            conn.close()
        if last:
            try:
                parsed = _dt_class.fromisoformat(last["updated_at"])
            except ValueError:
                parsed = _dt_class.strptime(last["updated_at"], "%Y-%m-%d %H:%M:%S")
            ago = (_dt_class.now() - parsed).total_seconds() / 3600
            last["ago_hours"] = round(ago, 1)
            last["freshness"] = "fresh" if ago < 24 else ("warm" if ago < 72 else "stale")
        return jsonify({
            "last_refresh": last,
            "today_plan": today,
            "open_positions": opens,
            "pnl_5d": pnl,
            "holdings_summary": hd["summary"],
        })

    @app.get("/api/stats")
    def stats():
        from db_repository import fetch_pick_outcomes
        from evolution.attribution import attribute
        conn = open_conn(db_path)
        try:
            rows = fetch_pick_outcomes(conn, judged_only=True)
        finally:
            conn.close()
        s = attribute(rows)
        strategies = [
            {"strategy": k, "n": v.n, "win_rate": round(v.win_rate * 100, 1),
             "expectancy": round(v.expectancy * 100, 2)}
            for k, v in sorted(s.items(), key=lambda kv: -kv[1].n)
        ]
        monthly = {}
        for r in rows:
            if r.get("win") is None:
                continue
            m = r["date"][:7]
            b = monthly.setdefault(m, {"n": 0, "wins": 0, "ret": 0.0})
            b["n"] += 1
            b["wins"] += int(r["win"])
            b["ret"] += float(r["outcome_pct"] or 0.0)
        monthly_list = [
            {"month": m, "n": b["n"], "win_rate": round(b["wins"] / b["n"] * 100, 1),
             "avg_ret": round(b["ret"] / b["n"] * 100, 2)}
            for m, b in sorted(monthly.items())
        ]
        return jsonify({"strategies": strategies, "monthly": monthly_list})

    return app


def main():
    import argparse
    parser = argparse.ArgumentParser(description="每日机会 Web 服务（Flask）")
    parser.add_argument("--db", type=str, default="hs300.db", help="SQLite 文件路径")
    parser.add_argument("--port", type=int, default=8000, help="监听端口")
    parser.add_argument("--top", type=int, default=10, help="榜单数量")
    args = parser.parse_args()
    app = create_app(db_path=args.db, top=args.top)
    print(f"http://127.0.0.1:{args.port}")
    app.run(host="127.0.0.1", port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
