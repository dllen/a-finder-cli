import sqlite3
import threading
import traceback
import uuid

from flask import Flask, jsonify, request

from pick_history import run_picks


PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>每日选股结果</title>
<link rel="stylesheet" href="https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.3/css/bootstrap.min.css">
</head><body class="bg-light">
<div class="container py-4">
  <h1 class="mb-3">每日选股结果</h1>
  <div class="row g-2 align-items-center mb-3">
    <div class="col-auto"><label class="form-label mb-0">日期</label></div>
    <div class="col-auto"><select id="d" class="form-select"></select></div>
    <div class="col-auto"><button id="btn-recalc" class="btn btn-outline-primary">重算榜单</button></div>
    <div class="col-auto"><button id="btn-sync" class="btn btn-primary">同步行情并重算</button></div>
    <div class="col-auto"><a class="btn btn-outline-secondary" href="/plan">查看 Plan</a></div>
  </div>
  <div id="status" class="mb-3"></div>
  <div id="prog" class="mb-3"></div>
  <div id="log" class="mb-3"></div>
  <div id="board"></div>
</div>
<script src="https://cdn.bootcdn.net/ajax/libs/jquery/3.7.1/jquery.min.js"></script>
<script src="https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.3/js/bootstrap.bundle.min.js"></script>
<script>
function fmt(v){return v==null?'-':Number(v).toFixed(2)}
function setStatus(html, cls){$('#status').html(html?'<div class="alert '+cls+'">'+html+'</div>':'')}
function setProgress(pct){
  if (pct==null) {$('#prog').empty(); return;}
  var color = pct>=100 ? 'bg-success' : 'bg-primary';
  $('#prog').html('<div class="progress" style="height:1.4rem">'+
    '<div class="progress-bar progress-bar-striped progress-bar-animated '+color+'" style="width:'+pct+'%">'+pct+'%</div></div>');
}
function setLog(lines){
  if (!lines || !lines.length) {$('#log').empty(); return;}
  var items = lines.map(function(l){return '<div class="border-bottom py-1 text-muted" style="font-size:.85rem">'+l+'</div>'});
  $('#log').html('<div class="card"><div class="card-body" style="max-height:14rem;overflow-y:auto">'+items.join('')+'</div></div>');
}
function render(date){
  $.getJSON('/api/picks', {date: date}, function(data){
    var groups = data.groups || {};
    var html = '';
    ['均线','买入信号'].forEach(function(kind){
      var rs = groups[kind] || [];
      html += '<h2>'+kind+' Top'+rs.length+'</h2>';
      html += '<table class="table table-striped table-hover align-middle"><thead><tr>'+
        '<th>排名</th><th>代码</th><th>名称</th><th>策略</th><th>买入</th><th>止损</th><th>目标</th><th>评分</th>'+
        '</tr></thead><tbody>';
      rs.forEach(function(r){
        html += '<tr><td>'+r.rank+'</td><td>'+r.code+'</td><td>'+r.name+'</td><td>'+r.strategy+'</td>'+
          '<td>'+fmt(r.buy)+'</td><td>'+fmt(r.stop)+'</td><td>'+fmt(r.target)+'</td><td>'+
          (r.score==null?'-':r.score)+'</td></tr>';
      });
      html += '</tbody></table>';
    });
    $('#board').html(html || '<p>该日期无数据</p>');
  });
}
function loadDates(){
  $.getJSON('/api/dates', function(data){
    var sel = $('#d').empty();
    (data.dates || []).forEach(function(d){sel.append(new Option(d, d))});
    if (data.dates && data.dates.length) render(data.dates[0]);
    else setStatus('无数据，请先同步行情','alert-warning');
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
  $('#d').on('change', function(){render($(this).val())});
  $('#btn-recalc').on('click', function(){refresh(false)});
  $('#btn-sync').on('click', function(){refresh(true)});
  loadDates();
});
</script></body></html>"""


PLAN_PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>每日 Plan</title>
<link rel="stylesheet" href="https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.3/css/bootstrap.min.css">
</head><body class="bg-light">
<div class="container py-4">
  <div class="d-flex align-items-center mb-3">
    <a class="btn btn-link p-0 me-3" href="/">&larr; 返回榜单</a>
    <h1 class="mb-0">每日 Plan（paper）</h1>
  </div>
  <div class="row g-2 align-items-center mb-3">
    <div class="col-auto"><label class="form-label mb-0">日期</label></div>
    <div class="col-auto"><select id="d" class="form-select"></select></div>
    <div class="col-auto">
      <div class="form-check"><input id="include-failed" type="checkbox" class="form-check-input">
        <label for="include-failed" class="form-check-label">含 failed</label></div>
    </div>
  </div>
  <div id="status" class="mb-3"></div>
  <div id="board"></div>
</div>
<script src="https://cdn.bootcdn.net/ajax/libs/jquery/3.7.1/jquery.min.js"></script>
<script>
function fmt(v){return v==null?'-':Number(v).toFixed(2)}
function setStatus(html, cls){$('#status').html(html?'<div class="alert '+cls+'">'+html+'</div>':'')}
function render(date){
  var includeFailed = $('#include-failed').is(':checked') ? '1' : '0';
  $.getJSON('/api/plan/'+date, {include_failed: includeFailed}, function(data){
    var rows = data.rows || [];
    var html = '<p class="text-muted">plan_date=' + data.plan_date + '，共 ' + rows.length + ' 行</p>';
    html += '<table class="table table-striped table-hover align-middle"><thead><tr>' +
      '<th>代码</th><th>方向</th><th>计划价</th><th>仓位%</th><th>止损</th><th>止盈</th><th>RR</th><th>状态</th><th>理由</th>' +
      '</tr></thead><tbody>';
    rows.forEach(function(r){
      html += '<tr><td>'+r.code+'</td><td>'+r.action+'</td>' +
        '<td>'+fmt(r.plan_price)+'</td>' +
        '<td>'+(r.size_pct==null?'-':(r.size_pct*100).toFixed(1))+'</td>' +
        '<td>'+fmt(r.stop_price)+'</td>' +
        '<td>'+fmt(r.tp_price)+'</td>' +
        '<td>'+fmt(r.rr_ratio)+'</td>' +
        '<td><span class="badge '+(r.status==="ok"?"bg-success":"bg-secondary")+'">'+r.status+'</span>' +
          (r.reason? ' <small class="text-muted">'+r.reason+'</small>':'')+'</td>' +
        '<td><small class="text-muted" style="font-family:monospace">'+(r.rationale_json||'-')+'</small></td></tr>';
    });
    html += '</tbody></table>';
    $('#board').html(html || '<p>该日期无 plan</p>');
  }).fail(function(){ setStatus('加载失败','alert-danger'); });
}
function loadDates(){
  $.getJSON('/api/plan/dates', function(data){
    var sel = $('#d').empty();
    var dates = data.dates || [];
    dates.forEach(function(d){ sel.append(new Option(d, d)); });
    if (dates.length) render(dates[0]);
    else { setStatus('暂无 plan，请先生成', 'alert-warning'); $('#board').empty(); }
  });
}
$(function(){
  $('#d').on('change', function(){ render($(this).val()); });
  $('#include-failed').on('change', function(){ var d = $('#d').val(); if (d) render(d); });
  loadDates();
});
</script></body></html>"""


JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


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
    return sqlite3.connect(db_path)


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
        return PAGE

    @app.get("/plan")
    def plan_page():
        return PLAN_PAGE

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

    return app


def main():
    import argparse
    parser = argparse.ArgumentParser(description="每日选股结果 Web 服务（Flask）")
    parser.add_argument("--db", type=str, default="hs300.db", help="SQLite 文件路径")
    parser.add_argument("--port", type=int, default=8000, help="监听端口")
    parser.add_argument("--top", type=int, default=10, help="榜单数量")
    args = parser.parse_args()
    app = create_app(db_path=args.db, top=args.top)
    print(f"http://127.0.0.1:{args.port}")
    app.run(host="127.0.0.1", port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
