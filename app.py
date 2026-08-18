import sqlite3
import threading
import traceback
import uuid
from datetime import datetime as _dt_class

from flask import Flask, jsonify, request

from pick_history import run_picks


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

_APP_CSS = """
:root{
  --brand:#16263a;
  --brand-2:#1f3350;
  --accent:#2f6f9f;
  --bg:#f5f6f8;
  --surface:#ffffff;
  --border:#e3e6eb;
  --text:#1a2332;
  --muted:#66707c;
}
html{font-size:16px}
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
.app-nav .nav-link{color:#c6d0dc}
.app-nav .nav-link:hover{color:#fff}
.app-nav .nav-link.active{color:#fff;font-weight:600}
.app-footer{border-top:1px solid var(--border);color:var(--muted);font-size:.85rem;background:var(--surface)}
.page-title{font-weight:600}
.section-title{font-size:1.05rem;font-weight:600;margin-top:1.5rem}
.app-card{
  background:var(--surface);border:1px solid var(--border);border-radius:.5rem;
  overflow:hidden;
}
.app-card .app-table{margin-bottom:0}
.app-table{width:100%;border-collapse:collapse}
.app-table thead th{
  position:sticky;top:0;z-index:1;
  background:#fbfcfd;color:var(--muted);font-weight:600;font-size:.8rem;
  text-transform:uppercase;letter-spacing:.02em;
  border-bottom:1px solid var(--border);padding:.6rem .75rem;white-space:nowrap;
}
.app-table tbody td{padding:.65rem .75rem;vertical-align:middle;border-bottom:1px solid #eef1f4}
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
.legend-item{display:inline-flex;align-items:center;gap:.35rem;font-size:.85rem;color:var(--muted)}
.sw{display:inline-block;width:.9rem;height:.9rem;border-radius:.25rem;border:1px solid rgba(0,0,0,.06)}
.sw-hot{background:#fdecea}
.sw-warm{background:#fff4e0}
.sw-mild{background:#f7faf7}
.sw-neutral{background:#ffffff;border-color:var(--border)}
.rank-badge{
  display:inline-flex;min-width:1.7rem;height:1.7rem;align-items:center;justify-content:center;
  border-radius:.4rem;background:#eef2f6;color:var(--muted);font-size:.8rem;font-weight:600;
}
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
.form-label{font-weight:500}
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
        f'<a class="{picks_cls}" href="/">每日机会</a>'
        f'<a class="{plan_cls}" href="/plan">交易计划</a>'
        '</div></div></div></nav>'
    )


_SHELL = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<link rel="stylesheet" href="https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.3/css/bootstrap.min.css">
<style>__CSS__</style>
</head>
<body>
__NAV__
__BODY__
__FOOTER__
<script src="https://cdn.bootcdn.net/ajax/libs/jquery/3.7.1/jquery.min.js"></script>
<script src="https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.3/js/bootstrap.bundle.min.js"></script>
<script src="/static/common.js"></script>
<script src="/static/dashboard.js"></script>
<script>
__SCRIPT__
</script>
</body>
</html>"""


def _page(title: str, active: str, body: str, script: str) -> str:
    return (
        _SHELL
        .replace("__TITLE__", title)
        .replace("__CSS__", _APP_CSS)
        .replace("__NAV__", _nav(active))
        .replace("__BODY__", body)
        .replace("__FOOTER__", _footer())
        .replace("__SCRIPT__", script)
    )


# ---------------------------------------------------------------------------
# Page bodies + scripts
# ---------------------------------------------------------------------------

PAGE_BODY = """<main class="container py-4">
  <div class="d-flex align-items-baseline justify-content-between mb-3">
    <h1 class="h3 mb-0 page-title">每日机会</h1>
    <span class="text-muted small" id="page-date">日期：{{today}}</span>
  </div>

  <div id="dashboard"></div>

  <div class="row g-2 align-items-center mb-3">
    <div class="col-auto"><label class="form-label mb-0" for="d">日期</label></div>
    <div class="col-auto"><input id="d" type="date" class="form-control" value="{{today}}"></div>
    <div class="col-auto"><button id="btn-recalc" class="btn btn-outline-primary">重算榜单</button></div>
    <div class="col-auto"><button id="btn-sync" class="btn btn-primary">同步行情并重算</button></div>
  </div>
  <div id="status" class="mb-3"></div>
  <div id="prog" class="mb-3"></div>
  <div id="log" class="mb-3"></div>
  <div id="board"></div>
</main>"""

PAGE_SCRIPT = """var PICKS_STATE = { data: null, sort: { kind: null, key: null, dir: -1 } };

var PICKS_COLS = [
  { key: 'code',     label: '代码', type: 'str',  cls: '' },
  { key: 'name',     label: '名称', type: 'str',  cls: '' },
  { key: 'strategy', label: '策略', type: 'str',  cls: '' },
  { key: 'buy',      label: '买入', type: 'num',  cls: 'num' },
  { key: 'stop',     label: '止损', type: 'num',  cls: 'num' },
  { key: 'target',   label: '目标', type: 'num',  cls: 'num' },
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
  var anyGroup = false;
  ['均线','买入信号'].forEach(function(kind){
    var rs = groups[kind] || [];
    if (!rs.length) return;
    var hasScore = rs.some(function(r){ return r.score != null; });
    if (!hasScore) return;  // 评分列为空 → 完全隐藏该组
    anyGroup = true;
    var s = PICKS_STATE.sort;
    var rows = sortRows(rs, s.kind === kind ? s.key : null, s.dir);
    var maxScore = rows.reduce(function(m,r){ return r.score!=null && r.score>m ? r.score : m; }, 0);
    html += '<h2 class="section-title">'+kind+' <span class="badge bg-light text-muted">Top '+rows.length+'</span></h2>';
    html += '<div class="table-responsive app-card"><table class="app-table"><thead><tr><th>排名</th>';
    PICKS_COLS.forEach(function(c){
      var active = s.kind === kind && s.key === c.key;
      var arrow = active ? (s.dir === -1 ? ' \u25BC' : ' \u25B2') : '';
      html += '<th class="'+c.cls+' sortable'+(active?' sorted':'')+'" data-sort="'+c.key+'" data-kind="'+kind+'">'+c.label+arrow+'</th>';
    });
    html += '</tr></thead><tbody>';
    rows.forEach(function(r, i){
      html += '<tr class="'+scoreBand(r.score, maxScore)+'"><td><span class="rank-badge">'+(i+1)+'</span></td>'+
        '<td class="code">'+r.code+'</td><td>'+r.name+'</td><td>'+r.strategy+'</td>'+
        '<td class="num">'+fmt(r.buy)+'</td><td class="num">'+fmt(r.stop)+'</td><td class="num">'+fmt(r.target)+'</td><td class="num"><span class="score-chip">'+
        (r.score==null?'—':r.score)+'</span></td></tr>';
    });
    html += '</tbody></table></div>';
  });
  if (anyGroup) {
    html = '<div class="legend d-flex flex-wrap align-items-center gap-3 mb-2">' +
      '<span class="text-muted small">评分分档：</span>' +
      '<span class="legend-item"><i class="sw sw-hot"></i>高 (≥85%)</span>' +
      '<span class="legend-item"><i class="sw sw-warm"></i>中 (≥70%)</span>' +
      '<span class="legend-item"><i class="sw sw-mild"></i>低 (≥50%)</span>' +
      '<span class="legend-item"><i class="sw sw-neutral"></i>其余</span>' +
      '<span class="text-muted small ms-auto">点击表头排序</span>' +
      '</div>' + html;
  }
  $('#board').html(html || '<div class="empty-state">该日期暂无选股数据</div>');
}

function render(date){
  showBoardLoading();
  $.getJSON('/api/picks', {date: date}, function(data){
    whenBoardReady(function(){
      PICKS_STATE.data = data;
      PICKS_STATE.sort = { kind: null, key: null, dir: -1 };
      drawBoard();
    });
  });
}
function loadDates(){
  var d = $('#d').val();
  if (d) render(d);
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
  $('#btn-recalc').on('click', function(){ refresh(false); });
  $('#btn-sync').on('click', function(){ refresh(true); });
  $('#board').on('click', 'th.sortable', function(){
    var key = $(this).data('sort');
    var kind = $(this).data('kind');
    var s = PICKS_STATE.sort;
    if (s.kind === kind && s.key === key) {
      s.dir = -s.dir;
    } else {
      s.kind = kind; s.key = key; s.dir = -1;
    }
    drawBoard();
  });
  startDashboard();
  loadDates();
});"""

PLAN_BODY = """<main class="container py-4">
  <div class="d-flex align-items-baseline justify-content-between mb-3">
    <h1 class="h3 mb-0 page-title">交易计划 <span class="badge bg-secondary align-middle">paper</span></h1>
  </div>

  <div id="dashboard"></div>

  <div class="row g-2 align-items-center mb-3">
    <div class="col-auto"><label class="form-label mb-0" for="d">日期</label></div>
    <div class="col-auto"><input id="d" type="date" class="form-control" value="{{today}}"></div>
    <div class="col-auto"><button id="btn-build" class="btn btn-primary">生成 plan</button></div>
    <div class="col-auto">
      <div class="form-check"><input id="include-failed" type="checkbox" class="form-check-input">
        <label for="include-failed" class="form-check-label">含 failed</label></div>
    </div>
  </div>
  <div id="status" class="mb-3"></div>
  <div id="prog" class="mb-3"></div>
  <div id="log" class="mb-3"></div>
  <div id="board"></div>
</main>"""

PLAN_SCRIPT = """var ACTION_META = {
  buy:  {label: '买入', cls: 'bg-success'},
  hold: {label: '持有', cls: 'bg-secondary'},
  exit: {label: '退出', cls: 'bg-warning text-dark'}
};
function actionMeta(a){ return ACTION_META[a] || {label:a, cls:'bg-light text-dark'}; }
function statusBadge(s){ return s==='ok' ? 'bg-success' : 'bg-danger'; }
function row(r){
  var meta = actionMeta(r.action);
  var sizePct = r.size_pct==null ? '—' : (r.size_pct*100).toFixed(1) + '%';
  var rationale;
  try {
    var obj = JSON.parse(r.rationale_json || '{}');
    var keys = Object.keys(obj);
    rationale = keys.length
      ? '<details><summary class="text-muted">'+keys.length+' 字段</summary>' +
        '<pre class="mb-0 small">' + JSON.stringify(obj, null, 2) + '</pre></details>'
      : '<span class="text-muted">—</span>';
  } catch(e) {
    rationale = '<small class="text-muted font-monospace">'+(r.rationale_json||'—')+'</small>';
  }
  return '<tr><td class="code">'+r.code+'</td>' +
    '<td>'+(r.name||'<span class="text-muted">—</span>')+'</td>' +
    '<td><span class="badge '+meta.cls+'">'+meta.label+'</span></td>' +
    '<td class="num">'+fmt(r.plan_price)+'</td>' +
    '<td class="num">'+sizePct+'</td>' +
    '<td class="num">'+fmt(r.stop_price)+'</td>' +
    '<td class="num">'+fmt(r.tp_price)+'</td>' +
    '<td class="num">'+fmt(r.rr_ratio)+'</td>' +
    '<td><span class="badge '+statusBadge(r.status)+'">'+r.status+'</span>' +
      (r.reason? ' <small class="text-muted">'+r.reason+'</small>':'')+'</td>' +
    '<td>'+rationale+'</td></tr>';
}
function render(date){
  showBoardLoading();
  var includeFailed = $('#include-failed').is(':checked') ? '1' : '0';
  $.getJSON('/api/plan/'+date, {include_failed: includeFailed}, function(data){
    whenBoardReady(function(){ drawPlan(data); });
  }).fail(function(){ setStatus('加载失败','alert-danger'); });
}
function drawPlan(data){
    var rows = data.rows || [];
    if (!rows.length) { $('#board').html('<div class="empty-state">该日期暂无 plan</div>'); return; }
    var groups = {buy:[], hold:[], exit:[]};
    var buySize = 0, failed = 0;
    rows.forEach(function(r){
      (groups[r.action] || (groups[r.action]=[])).push(r);
      if (r.action === 'buy' && r.status === 'ok' && r.size_pct != null) buySize += r.size_pct;
      if (r.status === 'failed') failed++;
    });
    var counts = Object.entries(groups).filter(function(e){return e[1].length}).map(function(e){return actionMeta(e[0]).label+' '+e[1].length;}).join(' · ');
    var summary = '<div class="card mb-3"><div class="card-body py-2">' +
      '<span class="me-3"><strong>'+data.plan_date+'</strong></span>' +
      '<span class="text-muted me-3">'+rows.length+' 行</span>' +
      '<span class="text-muted me-3">'+counts+'</span>' +
      '<span class="text-muted me-3">买入合计仓位 '+ (buySize*100).toFixed(1) +'%</span>' +
      '<span class="text-muted">失败 '+failed+'</span>' +
      '</div></div>';
    var html = summary;
    ['buy','hold','exit'].forEach(function(a){
      var rs = groups[a] || [];
      if (!rs.length) return;
      var meta = actionMeta(a);
      html += '<h2 class="section-title"><span class="badge '+meta.cls+'">'+meta.label+'</span> <small class="text-muted">'+rs.length+' 只</small></h2>';
      html += '<div class="table-responsive app-card"><table class="app-table"><thead><tr>' +
        '<th>代码</th><th>名称</th><th>方向</th><th class="num">计划价</th><th class="num">仓位</th><th class="num">止损</th><th class="num">止盈</th><th class="num">RR</th><th>状态</th><th>理由</th>' +
        '</tr></thead><tbody>' + rs.map(row).join('') + '</tbody></table></div>';
    });
    $('#board').html(html);
}
function loadDates(){ render($('#d').val()); }
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
    data: JSON.stringify({plan_date: date}),
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
    } else {
      setStatus('任务失败：'+data.message, 'alert-danger');
      $('#btn-build').prop('disabled', false);
    }
  }).fail(function(){
    setStatus('查询任务状态失败','alert-danger');
    $('#btn-build').prop('disabled', false);
  });
}
$(function(){
  $('#d').on('change', function(){ render($(this).val()); });
  $('#include-failed').on('change', function(){ var d = $('#d').val(); if (d) render(d); });
  $('#btn-build').on('click', buildPlan);
  startDashboard();
  render($('#d').val());
});"""


JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def _start_plan_job(db_path, plan_date):
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
            msg = f"日期 {result.get('date') or '-'}：均线 {result.get('ma') or 0} 条 / 买入信号 {result.get('buy') or 0} 条"
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
            PLAN_BODY.replace("{{today}}", today),
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
            for row in picks_for_date(conn, date):
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
        job_id = _start_plan_job(db_path, plan_date)
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

    @app.get("/api/dashboard")
    def dashboard():
        from datetime import date as _date
        from db_repository import (
            get_last_refresh,
            get_today_plan_summary,
            get_open_positions_with_unrealized,
            get_recent_pnl,
        )
        conn = open_conn(db_path)
        try:
            last = get_last_refresh(conn)
            today = get_today_plan_summary(conn, _date.today().isoformat())
            opens = get_open_positions_with_unrealized(conn)
            pnl = get_recent_pnl(conn, days=5)
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
        })

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
