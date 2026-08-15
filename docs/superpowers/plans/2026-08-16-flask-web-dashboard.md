# Flask Web 看板实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use supo-subagent-driven-development (recommended) or supo-executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 Flask 后端 + Bootstrap/jQuery（BootCDN）前端替换 stdlib 选股结果页，单页展示 `daily_picks` 两个榜单，并提供两个异步刷新按钮。

**Architecture:** Flask 应用（`app.py`）读 `hs300.db` 的 `daily_picks` 表提供 JSON API；HTML 单页通过 BootCDN 引入 Bootstrap 5 + jQuery，用 AJAX 拉取日期与榜单。刷新走后台线程执行 `pick_history.run_picks`，前端轮询 job 状态。保留 `web_server.py` 不动。

**Tech Stack:** Flask（新增依赖）、Bootstrap 5.3.3（BootCDN）、jQuery 3.7.1（BootCDN）、SQLite（复用现有 `hs300.db`）、pytest（新增 dev 依赖）。

## Global Constraints

- 前端类库一律 CDN 直引，不本地打包：Bootstrap 5.3.3 `https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.3/`，jQuery 3.7.1 `https://cdn.bootcdn.net/ajax/libs/jquery/3.7.1/jquery.min.js`
- `app.py` 复用 `web_server.py` 现有 `picks_for_date` 查询语义（`SELECT rank, kind, code, name, strategy, buy, stop, target, score FROM daily_picks WHERE date = ? ORDER BY kind, rank`）
- `web_server.py` 不改动；`stock_cli.py`/`pick_history.py`/`db_schema.py` 等既有逻辑不改动
- 同一时刻仅允许一个刷新任务；新任务在有 pending/running 任务时返回 409
- `run_picks` 的 `top` 来自启动参数 `--top`（默认 10）；`do_sync` 由请求 body 的 `sync` 决定

---

### Task 1: 新增 Flask 与 pytest 依赖

**Files:**
- Modify: `pyproject.toml`（依赖区）

**Interfaces:**
- Produces: 环境可 `import flask`、可运行 `pytest`；`uv.lock` 同步更新

- [ ] **Step 1: 添加 Flask 运行时依赖**

Run: `uv add flask`

- [ ] **Step 2: 添加 pytest 开发依赖**

Run: `uv add --dev pytest`

- [ ] **Step 3: 验证依赖可用**

```bash
.venv/bin/python -c "import flask; print(flask.__version__)"
.venv/bin/python -m pytest --version
```
Expected: 两行均正常输出，无 ModuleNotFoundError

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add flask and pytest dependencies"
```

---

### Task 2: Flask 数据 API（日期 + 榜单）

**Files:**
- Create: `app.py`
- Test: `app_test.py`

**Interfaces:**
- Produces:
  - `create_app(db_path="hs300.db", top=10) -> Flask`（应用工厂，供测试注入临时 DB）
  - `open_conn(db_path) -> sqlite3.Connection`（复用 `web_server.py` 语义）
  - `list_dates(conn) -> list[str]`（降序日期）
  - `picks_for_date(conn, date) -> list[dict]`（字段：rank/kind/code/name/strategy/buy/stop/target/score）
  - 路由：`GET /api/dates` → `{"dates": [...]}`；`GET /api/picks?date=` → `{"date": ..., "groups": {"均线": [...], "买入信号": [...]}}`

- [ ] **Step 1: 写失败测试**

`app_test.py`：

```python
import sqlite3
import tempfile
import os

import pytest

from db_schema import ensure_schema
from app import create_app


@pytest.fixture()
def client(tmp_path):
    db = str(tmp_path / "test.db")
    conn = sqlite3.connect(db)
    ensure_schema(conn)
    conn.execute(
        "INSERT INTO daily_picks (date, rank, kind, code, name, strategy, buy, stop, target, score) "
        "VALUES ('2026-08-15', 1, '均线', '600519', '贵州茅台', '突破', 1500.0, 1450.0, 1600.0, 9.5)"
    )
    conn.execute(
        "INSERT INTO daily_picks (date, rank, kind, code, name, strategy, buy, stop, target, score) "
        "VALUES ('2026-08-15', 1, '买入信号', '000001', '平安银行', '均线突破', 10.0, 9.5, 11.0, NULL)"
    )
    conn.commit()
    conn.close()
    app = create_app(db_path=db)
    app.config["TESTING"] = True
    return app.test_client()


def test_dates(client):
    resp = client.get("/api/dates")
    assert resp.status_code == 200
    assert resp.get_json() == {"dates": ["2026-08-15"]}


def test_picks(client):
    resp = client.get("/api/picks?date=2026-08-15")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["date"] == "2026-08-15"
    assert data["groups"]["均线"][0]["code"] == "600519"
    assert data["groups"]["买入信号"][0]["code"] == "000001"
    assert data["groups"]["买入信号"][0]["score"] is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest app_test.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app'`）

- [ ] **Step 3: 写最小实现（数据部分）**

`app.py`：

```python
import sqlite3

from flask import Flask, jsonify, request


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

    return app
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest app_test.py -v`
Expected: `test_dates` 与 `test_picks` PASS

- [ ] **Step 5: Commit**

```bash
git add app.py app_test.py
git commit -m "feat: add flask data api for daily picks"
```

---

### Task 3: 刷新任务（后台线程 + job 状态）

**Files:**
- Modify: `app.py`（追加 job 管理 + 路由）
- Test: `app_test.py`（追加测试）

**Interfaces:**
- Consumes: `create_app(db_path, top)`；`from pick_history import run_picks`（签名 `run_picks(db_path, top, do_sync)`，返回 `{"date": str, "ma": int, "buy": int}`）
- Produces:
  - `JOBS: dict[str, dict]`、`JOBS_LOCK: threading.Lock`（模块级）
  - `_start_job(db_path, top, do_sync) -> str | None`（返回 job_id；busy 时返回 None）
  - 路由：`POST /api/refresh`（body `{"sync": bool}`）→ 202 `{"job_id": ...}` 或 409 `{"error": "已有任务进行中"}`；`GET /api/jobs/<job_id>` → `{"status": ..., "message": ...}` 或 404

- [ ] **Step 1: 写失败测试**

`app_test.py` 追加：

```python
import json


def test_refresh_and_job(client):
    resp = client.post("/api/refresh", json={"sync": False})
    assert resp.status_code == 202
    job_id = resp.get_json()["job_id"]

    # busy 时重复提交应 409
    busy = client.post("/api/refresh", json={"sync": False})
    assert busy.status_code == 409

    import time
    status = None
    for _ in range(50):
        jr = client.get(f"/api/jobs/{job_id}")
        assert jr.status_code == 200
        status = jr.get_json()["status"]
        if status in ("done", "error"):
            break
        time.sleep(0.1)
    assert status == "done"


def test_job_not_found(client):
    resp = client.get("/api/jobs/nope")
    assert resp.status_code == 404
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest app_test.py::test_refresh_and_job app_test.py::test_job_not_found -v`
Expected: FAIL（404/405，路由未定义）

- [ ] **Step 3: 写最小实现（job 部分）**

`app.py` 追加（`import threading`、`import uuid`、`import traceback`，顶部加 `from pick_history import run_picks`）：

```python
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
```

并在 `create_app` 内追加：

```python
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
```

注意：`test_refresh_and_job` 里 `run_picks` 会真实读 `tmp_path/test.db`（无元数据、无行情），`build_market_from_db` 返回空 → `run_picks` 返回 `{"date": "", "ma": 0, "buy": 0}`，job 正常置 `done`，无需真实网络。

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest app_test.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add app.py app_test.py
git commit -m "feat: add async refresh jobs with polling"
```

---

### Task 4: HTML 单页 + 命令行入口

**Files:**
- Modify: `app.py`（追加 `PAGE` 常量、`main()`、`if __name__ == "__main__"`）
- Modify: `run_web.sh`

**Interfaces:**
- Consumes: `create_app(db_path, top)`；`/api/dates`、`/api/picks`、`/api/refresh`、`/api/jobs/<id>` 路由
- Produces: `GET /` 返回自包含 HTML；`python app.py --db --port --top` 启动服务

- [ ] **Step 1: 写 HTML 模板**

`app.py` 顶部加 `PAGE`（jQuery 3.7.1 + Bootstrap 5.3.3 BootCDN，单页：标题、日期下拉、两个按钮、状态条、两个榜单表格）：

```python
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
  $.post('/api/refresh', JSON.stringify({sync: sync}), function(data){
    poll(data.job_id);
  }, 'json').fail(function(xhr){
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
  });
}
$(function(){
  $('#d').on('change', function(){render($(this).val())});
  $('#btn-recalc').on('click', function(){refresh(false)});
  $('#btn-sync').on('click', function(){refresh(true)});
  loadDates();
});
</script></body></html>"""
```

并在 `create_app` 内追加：

```python
    @app.get("/")
    def index():
        return PAGE
```

- [ ] **Step 2: 写命令行入口**

`app.py` 末尾追加：

```python
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
```

- [ ] **Step 3: 更新 run_web.sh**

将调用行改为（保留 `DB`/`PORT`，新增 `TOP`）：

```bash
TOP="${TOP:-10}"
echo "==> 启动每日选股结果服务 http://127.0.0.1:$PORT"
"$PY" "$ROOT_DIR/app.py" --db "$DB" --port "$PORT" --top "$TOP"
```

- [ ] **Step 4: 验证首页返回 HTML**

Run: `.venv/bin/python -c "from app import create_app; c=create_app().test_client(); r=c.get('/'); print(r.status_code, 'bootstrap' in r.get_data(as_text=True))"`
Expected: `200 True`

- [ ] **Step 5: 运行全量测试**

Run: `.venv/bin/python -m pytest app_test.py -v`
Expected: 全部 PASS

- [ ] **Step 6: Commit**

```bash
git add app.py run_web.sh
git commit -m "feat: add bootstrap/jquery single-page dashboard and cli entry"
```

---

### Task 5: 文档更新

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: `run_web.sh`（支持 `DB`/`PORT`/`TOP` 环境变量）

- [ ] **Step 1: 在 README 补充 Web 服务说明**

在「一键管理」之前插入：

```markdown
## Web 看板

Flask 后端 + Bootstrap/jQuery（CDN）单页看板，只读 `daily_picks` 预计算结果：

```bash
bash run_web.sh                    # 默认 http://127.0.0.1:8000
DB=hs300.db PORT=8080 TOP=20 bash run_web.sh
```

- 「重算榜单」：不联网，仅重算 `daily_picks`（走 `pick_history.run_picks` 的 `do_sync=False`）
- 「同步行情并重算」：先增量同步沪深300行情再重算榜单（较慢，依赖网络）
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: document web dashboard usage"
```

---

## Self-Review

- **Spec coverage:** 数据 API（Task 2）、刷新任务 + 409（Task 3）、HTML 单页 + 两个按钮 + 轮询（Task 4）、CLI/run_web.sh（Task 4）、依赖（Task 1）、文档（Task 5）、保留 web_server.py 不动（Global Constraints 明确不改）。全部覆盖。
- **Placeholder scan:** 无 TBD/TODO；每个代码步骤含完整可运行代码块。
- **Type consistency:** `create_app(db_path, top)`、`run_picks(db_path, top, do_sync)`、`_start_job`、`JOBS`/`JOBS_LOCK` 命名在各任务一致；测试与实现字段名一致（rank/kind/code/name/strategy/buy/stop/target/score）。
