import argparse
import json
import sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse


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
        {
            "rank": r[0],
            "kind": r[1],
            "code": r[2],
            "name": r[3],
            "strategy": r[4],
            "buy": r[5],
            "stop": r[6],
            "target": r[7],
            "score": r[8],
        }
        for r in rows
    ]


PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>每日选股结果</title>
<style>
body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:1100px;margin:0 auto;padding:16px;background:#f7f8fa;color:#1f2328}
h1{font-size:20px}h2{font-size:16px;margin-top:24px}
table{border-collapse:collapse;width:100%;background:#fff;box-shadow:0 1px 2px rgba(0,0,0,.06)}
th,td{border:1px solid #e1e4e8;padding:6px 8px;text-align:right;font-size:13px}
th{background:#f6f8fa}td:nth-child(2),td:nth-child(3),td:nth-child(4),th:nth-child(2),th:nth-child(3),th:nth-child(4){text-align:left}
.up{color:#d73a49}.down{color:#22863a}
</style></head><body>
<h1>每日选股结果</h1>
<label>日期 <select id="d"></select></label>
<div id="board"></div>
<script>
const data=__DATA__;
const sel=document.getElementById('d');
for(const d of data.dates)sel.add(new Option(d,d));
function fmt(v){return v==null?'-':Number(v).toFixed(2)}
function render(date){
  const rows=data.rows[date]||[];
  const groups={};
  for(const r of rows)(groups[r.kind]=groups[r.kind]||[]).push(r);
  let html='';
  for(const kind of ['高胜率','均线','买入信号','信号策略','多因子']){
    const rs=groups[kind]||[];
    const scoreHead=kind==='高胜率'?'胜率%':'评分';
    html+='<h2>'+kind+' Top'+rs.length+'</h2>';
    html+='<table><thead><tr><th>排名</th><th>代码</th><th>名称</th><th>策略</th><th>买入</th><th>止损</th><th>目标</th><th>'+scoreHead+'</th></tr></thead><tbody>';
    for(const r of rs){
      html+='<tr><td>'+r.rank+'</td><td>'+r.code+'</td><td>'+r.name+'</td><td>'+r.strategy+'</td>'
        +'<td>'+fmt(r.buy)+'</td><td>'+fmt(r.stop)+'</td><td>'+fmt(r.target)+'</td><td>'+(r.score==null?'-':r.score)+'</td></tr>';
    }
    html+='</tbody></table>';
  }
  document.getElementById('board').innerHTML=html||'<p>该日期无数据</p>';
}
sel.onchange=()=>render(sel.value);
render(data.dates[0]||'');
</script></body></html>"""


def make_handler(db_path):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/":
                conn = open_conn(db_path)
                dates = list_dates(conn)
                rows = {}
                for d in dates:
                    rows[d] = picks_for_date(conn, d)
                conn.close()
                html = PAGE.replace("__DATA__", json.dumps({"dates": dates, "rows": rows}, ensure_ascii=False))
                body = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif parsed.path == "/api/picks":
                qs = parse_qs(parsed.query)
                date = qs.get("date", [""])[0]
                conn = open_conn(db_path)
                if date:
                    rows = picks_for_date(conn, date)
                else:
                    dates = list_dates(conn)
                    rows = picks_for_date(conn, dates[0]) if dates else []
                conn.close()
                body = json.dumps(rows, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args):
            pass

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="每日选股结果本地 HTTP 服务")
    parser.add_argument("--db", type=str, default="hs300.db", help="SQLite 文件路径")
    parser.add_argument("--port", type=int, default=8000, help="监听端口")
    args = parser.parse_args()
    server = HTTPServer(("127.0.0.1", args.port), make_handler(args.db))
    print(f"http://127.0.0.1:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
