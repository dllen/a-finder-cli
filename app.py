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
  </div>
  <div id="status" class="mb-3"></div>
  <div id="board"></div>
</div>
<script src="https://cdn.bootcdn.net/ajax/libs/jquery/3.7.1/jquery.min.js"></script>
<script src="https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.3/js/bootstrap.bundle.min.js"></script>
<script>
function fmt(v){return v==null?'-':Number(v).toFixed(2)}
function setStatus(html, cls){$('#status').html(html?'<div class="alert '+cls+'">'+html+'</div>':'')}
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


JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def _start_job(db_path, top, do_sync):
    with JOBS_LOCK:
        if any(j["status"] in ("pending", "running") for j in JOBS.values()):
            return None
        job_id = uuid.uuid4().hex
        JOBS[job_id] = {"status": "pending", "message": ""}

    def work():
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "running"
        try:
            result = run_picks(db_path, top, do_sync)
            msg = f"日期 {result.get('date') or '-'}：均线 {result.get('ma') or 0} 条 / 买入信号 {result.get('buy') or 0} 条"
            with JOBS_LOCK:
                JOBS[job_id] = {"status": "done", "message": msg}
        except Exception as e:  # noqa: BLE001
            with JOBS_LOCK:
                JOBS[job_id] = {"status": "error", "message": traceback.format_exc(limit=3)}

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
