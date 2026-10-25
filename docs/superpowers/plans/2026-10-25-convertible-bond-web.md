# 可转债 Web 页面 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新建可转债 Web 页面（/cb），展示转债榜单、持仓、强赎预警三个板块，Flask API 与静态站（gh-pages）双模式可用。

**Architecture:** 复用 `static/js/api.js` 已有的 API/静态双模式抽象（`isStatic()` + `dsPath()`），新增 4 个 `fetchCb*` 函数即可同时服务两种部署。后端新增 5 个 `/api/cb/*` Flask 路由只读 `cb_*` 表（预警接口例外，实时抓取仅针对持仓的少数几只）。前端新建 `static/js/cb.js` + `templates/cb.html`。

**Tech Stack:** Flask, Jinja2, Vanilla ES6 modules, Bootstrap 5.3.3 (本地 vendored), SQLite

**Spec:** `docs/superpowers/specs/2026-10-25-convertible-bond-web-design.md`

---

### Task 1: api.js 增加转债数据源函数

**Files:**
- Modify: `static/js/api.js` (append at end)
- Test: `tests/test_cb_web.py` (create — first test file for this feature)

- [ ] **Step 1: Write the failing test**

Create `tests/test_cb_web.py`:

```python
"""可转债 Web 页面：API 路由、模板、静态导出。"""
import json
import os
import tempfile

import pytest


@pytest.fixture
def cb_db():
    """带转债数据的临时库。"""
    from db_repository import open_db
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = open_db(path)
    conn.execute(
        "INSERT INTO cb_picks (trade_date, rank, code, name, price, total_score, "
        "a_score, b_score, c_score, size_pct, shares, stop_price, tp_price, reason) "
        "VALUES ('2026-10-25', 1, '113046', '金田转债', 101.17, 83.1, 79, 85, 87, "
        "0.15, 140, 93.08, 121.4, '债底保护充分')"
    )
    conn.execute(
        "INSERT INTO cb_positions (code, name, stock_code, stock_name, entry_date, "
        "entry_price, shares, stop_price, tp_price) "
        "VALUES ('113059', '转债A', '601000', '浦发银行', '2026-10-20', 108.5, 50, "
        "99.82, 130.2)"
    )
    conn.commit()
    conn.close()
    yield path
    os.unlink(path)


@pytest.fixture
def client(cb_db):
    from app import create_app
    app = create_app(db_path=cb_db)
    app.config["TESTING"] = True
    return app.test_client()


class TestCbApiJs:

    def test_api_js_has_cb_fetchers(self):
        """static/js/api.js 需提供 4 个转债取数函数（API + 静态双模式）。"""
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "static", "js", "api.js",
        )
        src = open(path, encoding="utf-8").read()
        for fn in ("fetchCbPicks", "fetchCbPositions", "fetchCbAlerts", "fetchCbDates"):
            assert f"export function {fn}" in src, f"缺少 {fn}"
        # 静态模式分支
        assert "cb-picks-" in src
        assert "cb-positions.json" in src
        assert "cb-alerts.json" in src
        assert "cb-dates.json" in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_cb_web.py -v`
Expected: FAIL — `assert 'export function fetchCbPicks' in src`

- [ ] **Step 3: Implement the fetchers**

Append to `static/js/api.js`:

```javascript
// ---------------------------------------------------------------------------
// 可转债（Convertible Bond）
// ---------------------------------------------------------------------------

export function fetchCbPicks(date) {
  return isStatic()
    ? getJSON(dsPath('data/cb-picks-' + date + '.json'))
    : getJSON('/api/cb/picks', { date });
}

export function fetchCbDates() {
  return isStatic()
    ? getJSON(dsPath('data/cb-dates.json'))
    : getJSON('/api/cb/dates');
}

export function fetchCbPositions() {
  return isStatic()
    ? getJSON(dsPath('data/cb-positions.json'))
    : getJSON('/api/cb/positions');
}

export function fetchCbAlerts() {
  return isStatic()
    ? getJSON(dsPath('data/cb-alerts.json'))
    : getJSON('/api/cb/alerts');
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_cb_web.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add static/js/api.js tests/test_cb_web.py
git commit -m "feat(cb): api.js 增加可转债取数函数（API + 静态双模式）"
```

---

### Task 2: Flask /api/cb/* 路由

**Files:**
- Modify: `app.py` (add routes inside `create_app`)
- Test: `tests/test_cb_web.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cb_web.py`:

```python
class TestCbApi:

    def test_api_cb_picks_returns_rows(self, client):
        resp = client.get("/api/cb/picks?date=2026-10-25")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["date"] == "2026-10-25"
        assert len(data["rows"]) == 1
        row = data["rows"][0]
        assert row["code"] == "113046"
        assert row["name"] == "金田转债"
        assert row["total_score"] == 83.1
        assert row["a_score"] == 79
        assert row["b_score"] == 85
        assert row["c_score"] == 87
        assert row["shares"] == 140
        assert row["reason"] == "债底保护充分"

    def test_api_cb_picks_defaults_to_latest_date(self, client):
        resp = client.get("/api/cb/picks")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["date"] == "2026-10-25"
        assert len(data["rows"]) == 1

    def test_api_cb_picks_empty_db(self):
        from db_repository import open_db
        from app import create_app
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        open_db(path).close()
        app = create_app(db_path=path)
        resp = app.test_client().get("/api/cb/picks")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["rows"] == []
        os.unlink(path)

    def test_api_cb_positions_returns_rows(self, client):
        resp = client.get("/api/cb/positions")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["rows"]) == 1
        row = data["rows"][0]
        assert row["code"] == "113059"
        assert row["shares"] == 50
        assert row["entry_price"] == 108.5
        assert row["stock_name"] == "浦发银行"

    def test_api_cb_dates(self, client):
        resp = client.get("/api/cb/dates")
        assert resp.status_code == 200
        assert resp.get_json()["dates"] == ["2026-10-25"]

    def test_api_cb_alerts_no_positions_no_network(self, client, monkeypatch):
        """无持仓 → 空数组，且不触发任何网络抓取。"""
        import cb_data_provider

        def boom(*a, **kw):
            raise AssertionError("无持仓时不应抓网络")

        monkeypatch.setattr(cb_data_provider, "fetch_universe_snapshot", boom)
        monkeypatch.setattr(cb_data_provider, "fetch_redeem_status_map", boom)
        resp = client.get("/api/cb/alerts")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["alerts"] == []
        assert "generated_at" in data
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_cb_web.py::TestCbApi -v`
Expected: FAIL — 404 on `/api/cb/picks`

- [ ] **Step 3: Implement the routes**

In `app.py`, inside `create_app`, add after the `/api/dashboard` route (after line 311's `return jsonify({...})` block):

```python
    # ------------------------------------------------------------------
    # 可转债（Convertible Bond）
    # ------------------------------------------------------------------

    def _cb_latest_pick_date(conn) -> str:
        row = conn.execute(
            "SELECT MAX(trade_date) FROM cb_picks"
        ).fetchone()
        return row[0] if row and row[0] else ""

    @app.get("/api/cb/dates")
    def cb_dates():
        conn = open_conn(db_path)
        try:
            rows = conn.execute(
                "SELECT DISTINCT trade_date FROM cb_picks ORDER BY trade_date DESC"
            ).fetchall()
        finally:
            conn.close()
        return jsonify({"dates": [r[0] for r in rows]})

    @app.get("/api/cb/picks")
    def cb_picks():
        from cb_repository import get_cb_picks
        conn = open_conn(db_path)
        try:
            date = request.args.get("date", "") or _cb_latest_pick_date(conn)
            rows = get_cb_picks(conn, date) if date else []
        finally:
            conn.close()
        return jsonify({"date": date, "rows": rows})

    @app.get("/api/cb/positions")
    def cb_positions():
        from cb_repository import get_cb_positions
        conn = open_conn(db_path)
        try:
            rows = get_cb_positions(conn)
        finally:
            conn.close()
        return jsonify({"rows": rows})

    @app.get("/api/cb/alerts")
    def cb_alerts():
        """持仓退出预警。实时抓取行情（仅针对持仓的少数几只）。

        无持仓时直接返回空数组，不触发任何网络请求。
        """
        from cb_repository import get_cb_positions
        from strategies.convertible import evaluate_alerts

        conn = open_conn(db_path)
        try:
            positions = get_cb_positions(conn)
        finally:
            conn.close()

        generated_at = _dt_class.now().strftime("%Y-%m-%dT%H:%M:%S")
        if not positions:
            return jsonify({"generated_at": generated_at, "alerts": []})

        from cb_data_provider import fetch_redeem_status_map, fetch_universe_snapshot
        universe = {r["code"]: r for r in fetch_universe_snapshot()}
        redeem_map = fetch_redeem_status_map()

        quotes = {}
        for code in {p["code"] for p in positions}:
            u = universe.get(code) or {}
            rd = redeem_map.get(code) or {}
            cp = u.get("convert_price")
            sp = u.get("stock_price")
            trig = rd.get("redeem_price")
            redeem_dist = None
            if trig and sp and sp > 0:
                redeem_dist = round((trig / sp - 1.0) * 100.0, 4)
            quotes[code] = {
                "price": u.get("price"),
                "redeem_status": rd.get("redeem_status") or "",
                "redeem_dist": redeem_dist,
                "premium_rate": u.get("premium_rate"),
                "stock_price": sp,
                "convert_price": cp,
            }

        alerts = evaluate_alerts(positions, quotes)
        return jsonify({"generated_at": generated_at, "alerts": alerts})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_cb_web.py::TestCbApi -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_cb_web.py
git commit -m "feat(cb): 新增 5 个 /api/cb/* 路由（榜单/持仓/预警/日期）"
```

---

### Task 3: cb.html 模板 + 导航 + dashboard 摘要

**Files:**
- Create: `templates/cb.html`
- Modify: `templates/components/nav.html`
- Modify: `templates/components/dashboard_cards.html`
- Test: `tests/test_cb_web.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cb_web.py`:

```python
class TestCbTemplates:

    def test_cb_page_renders(self, client):
        resp = client.get("/cb")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8")
        assert "可转债" in body
        assert "转债榜单" in body
        assert "持仓" in body
        assert "预警" in body

    def test_cb_page_no_jquery_no_cdn(self, client):
        body = client.get("/cb").data.decode("utf-8").lower()
        assert "jquery" not in body
        assert "bootcdn" not in body
        assert "cdn.jsdelivr" not in body

    def test_cb_page_loads_es6_module(self, client):
        body = client.get("/cb").data.decode("utf-8")
        assert 'type="module"' in body
        assert "js/cb.js" in body

    def test_nav_has_cb_link(self, client):
        body = client.get("/").data.decode("utf-8")
        assert 'data-nav="cb"' in body
        assert "可转债" in body

    def test_dashboard_includes_cb_summary(self, client):
        resp = client.get("/api/dashboard")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "cb" in data
        cb = data["cb"]
        assert cb["picks_count"] == 1
        assert cb["positions_count"] == 1
        assert cb["latest_date"] == "2026-10-25"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_cb_web.py::TestCbTemplates -v`
Expected: FAIL — 404 on `/cb`

- [ ] **Step 3: Create templates/cb.html**

Create `templates/cb.html`:

```html
{% extends "base.html" %}
{% set active_page = "cb" %}
{% block title %}可转债{% endblock %}

{% block body %}
<main class="container py-4">
  <div class="d-flex align-items-center mb-3">
    <h2 class="mb-0">可转债</h2>
    <span class="text-muted fs-6 ms-2">[独立转债仓]</span>
    <div class="ms-auto d-flex align-items-center gap-2">
      <select id="cb-date" class="form-select form-select-sm" style="width:auto" aria-label="榜单日期"></select>
      <button id="cb-refresh" class="btn btn-sm btn-outline-primary">刷新</button>
    </div>
  </div>

  <div class="row row-cols-1 row-cols-md-3 g-2 mb-3" id="cb-summary"></div>

  <h5 class="mt-4" id="cb-alert-title">持仓与预警</h5>
  <div id="cb-alerts"></div>
  <div id="cb-positions" class="mt-3"></div>

  <h5 class="mt-4">转债榜单</h5>
  <div id="cb-board"></div>
</main>
{% endblock %}

{% block scripts %}
<script type="module" src="{{ url_for('static', filename='js/cb.js') }}"></script>
{% endblock %}
```

- [ ] **Step 4: Add nav link**

In `templates/components/nav.html`, add this line after the `plan` link:

```html
<a class="nav-link {% if active_page == 'cb' %}active{% endif %}" data-nav="cb" href="/cb">可转债</a>
```

The nav's `navbar-nav` block becomes:

```html
<div class="navbar-nav ms-auto">
<a class="nav-link {% if active_page == 'picks' %}active{% endif %}" data-nav="picks" href="/">每日机会</a>
<a class="nav-link {% if active_page == 'portfolio' %}active{% endif %}" data-nav="portfolio" href="/portfolio">持仓组合</a>
<a class="nav-link {% if active_page == 'plan' %}active{% endif %}" data-nav="plan" href="/plan">交易计划</a>
<a class="nav-link {% if active_page == 'cb' %}active{% endif %}" data-nav="cb" href="/cb">可转债</a>
</div>
```

- [ ] **Step 5: Add dashboard cb summary**

In `templates/components/dashboard_cards.html`, add a cb card placeholder. The file currently contains:

```html
<div id="dashboard"></div>
```

Change it to:

```html
<div id="dashboard"></div>
<div id="cb-summary-strip" class="mb-3"></div>
```

- [ ] **Step 6: Add /cb route and dashboard cb field to app.py**

In `app.py`, inside `create_app`, add the page route near the other page routes (after the `/portfolio` route):

```python
    @app.get("/cb")
    def cb_page():
        from datetime import date as _date
        return render_template("cb.html",
            today=_date.today().isoformat(),
            app_mode="api", data_prefix="",
            year=_date.today().year)
```

In the `/api/dashboard` route, add cb summary. Replace the `return jsonify({...})` block with:

```python
        from cb_repository import get_cb_positions
        conn2 = open_conn(db_path)
        try:
            cb_date = _cb_latest_pick_date(conn2)
            cb_picks_count = conn2.execute(
                "SELECT COUNT(*) FROM cb_picks WHERE trade_date=?", (cb_date,)
            ).fetchone()[0] if cb_date else 0
            cb_positions_count = len(get_cb_positions(conn2))
        finally:
            conn2.close()
        return jsonify({
            "last_refresh": last,
            "today_plan": today,
            "open_positions": opens,
            "pnl_5d": pnl,
            "holdings_summary": hd["summary"],
            "cb": {
                "picks_count": cb_picks_count,
                "positions_count": cb_positions_count,
                "latest_date": cb_date,
            },
        })
```

Note: `_cb_latest_pick_date` must be defined BEFORE the `/api/dashboard` route for the above to work. Move the helper function to just after the `def create_app(...)` opening line, before the first route.

- [ ] **Step 7: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_cb_web.py -v`
Expected: PASS (all tests)

- [ ] **Step 8: Commit**

```bash
git add templates/cb.html templates/components/nav.html templates/components/dashboard_cards.html app.py tests/test_cb_web.py
git commit -m "feat(cb): 新增 /cb 页面模板 + 导航入口 + dashboard 转债摘要"
```

---

### Task 4: static/js/cb.js 页面逻辑

**Files:**
- Create: `static/js/cb.js`
- Test: `tests/test_cb_web.py` (append — static asset checks)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cb_web.py`:

```python
class TestCbJs:

    def _js(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "static", "js", "cb.js",
        )
        return open(path, encoding="utf-8").read()

    def test_cb_js_exists_and_is_module(self):
        src = self._js()
        assert "import" in src
        assert "export function" in src

    def test_cb_js_imports_cb_fetchers(self):
        src = self._js()
        for fn in ("fetchCbPicks", "fetchCbPositions", "fetchCbAlerts", "fetchCbDates"):
            assert fn in src, f"未使用 {fn}"

    def test_cb_js_has_no_jquery(self):
        src = self._js()
        assert "$(" not in src
        assert "$.getJSON" not in src
        assert ".html(" not in src

    def test_cb_js_renders_three_sections(self):
        src = self._js()
        assert "cb-summary" in src
        assert "cb-alerts" in src
        assert "cb-positions" in src
        assert "cb-board" in src
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_cb_web.py::TestCbJs -v`
Expected: FAIL — `FileNotFoundError`

- [ ] **Step 3: Create static/js/cb.js**

Create `static/js/cb.js`:

```javascript
// 可转债页面：榜单 + 持仓 + 强赎预警

import { fetchCbPicks, fetchCbPositions, fetchCbAlerts, fetchCbDates } from './api.js';
import { $, fmt, esc, setStatus } from './dom.js';
import { scoreBand } from './table.js';

const ALERT_CLS = {
  emergency: 'bg-danger',
  important: 'bg-warning text-dark',
  watch: 'bg-info text-dark',
  normal: 'bg-secondary',
};

function pct(v) { return v == null ? '—' : (v * 100).toFixed(1) + '%'; }

// ---------------------------------------------------------------------------
// 摘要卡
// ---------------------------------------------------------------------------

function renderSummary(picksCount, positionsCount, alertsCount, latestDate) {
  const el = $('cb-summary');
  if (!el) return;
  const card = (title, value, sub) => `
    <div class="col">
      <div class="card shadow-sm h-100">
        <div class="card-body py-2 px-3">
          <div class="text-muted small mb-1">${title}</div>
          <div class="fs-4 fw-bold">${value}</div>
          <div class="text-muted small">${sub}</div>
        </div>
      </div>
    </div>`;
  el.innerHTML =
    card('转债榜单', picksCount, latestDate ? `最新 ${latestDate}` : '暂无数据') +
    card('持仓', positionsCount, positionsCount ? '只' : '空仓') +
    card('预警', alertsCount, alertsCount ? '需处理' : '无');
}

// ---------------------------------------------------------------------------
// 预警
// ---------------------------------------------------------------------------

function renderAlerts(alerts, generatedAt) {
  const el = $('cb-alerts');
  if (!el) return;
  if (!alerts || !alerts.length) {
    el.innerHTML = '<div class="empty-state">无预警：持仓全部正常</div>';
    return;
  }
  let html = '<div class="app-card mb-2">';
  alerts.forEach(a => {
    const cls = ALERT_CLS[a.level] || 'bg-secondary';
    html += `<div class="border-bottom py-2 d-flex align-items-start gap-2">` +
      `<span class="badge ${cls}">${esc(a.level_label || a.level)}</span>` +
      `<div class="flex-grow-1">` +
        `<div class="fw-semibold">${esc(a.code)} ${esc(a.name)}` +
        `<span class="text-muted ms-2">${a.shares != null ? a.shares + ' 张' : ''}</span></div>` +
        `<div class="small">${esc(a.message)}</div>` +
        `<div class="small text-muted">现价 ${fmt(a.price)}</div>` +
      `</div></div>`;
  });
  html += '</div>';
  if (generatedAt) {
    html += `<div class="text-muted small mb-3">快照时间 ${esc(generatedAt)}</div>`;
  }
  el.innerHTML = html;
}

// ---------------------------------------------------------------------------
// 持仓
// ---------------------------------------------------------------------------

function renderPositions(rows) {
  const el = $('cb-positions');
  if (!el) return;
  if (!rows || !rows.length) {
    el.innerHTML = '<div class="empty-state">暂无转债持仓</div>';
    return;
  }
  let html = '<div class="table-responsive app-card d-none d-md-block mb-3"><table class="app-table"><thead><tr>' +
    '<th>代码</th><th>名称</th><th>正股</th><th>建仓日</th><th class="num">建仓价</th>' +
    '<th class="num">张数</th><th class="num">止损</th><th class="num">止盈</th><th>备注</th>' +
    '</tr></thead><tbody>';
  rows.forEach(r => {
    html += `<tr><td class="code">${esc(r.code)}</td><td>${esc(r.name || '—')}</td>` +
      `<td>${esc(r.stock_name || r.stock_code || '—')}</td>` +
      `<td>${esc(r.entry_date || '—')}</td>` +
      `<td class="num">${fmt(r.entry_price)}</td>` +
      `<td class="num">${r.shares}</td>` +
      `<td class="num">${fmt(r.stop_price)}</td>` +
      `<td class="num">${fmt(r.tp_price)}</td>` +
      `<td>${esc(r.note || '')}</td></tr>`;
  });
  html += '</tbody></table></div>';

  html += '<div class="app-card d-md-none mb-3">';
  rows.forEach(r => {
    html += `<div class="plan-card"><div class="card-header-row">` +
      `<span class="code fw-semibold">${esc(r.code)}</span>` +
      `<span class="card-title-text">${esc(r.name || '')}</span>` +
      `<span class="num">${r.shares} 张</span></div>` +
      `<div class="kv-grid">` +
        `<span class="k">正股</span><span class="v wrap">${esc(r.stock_name || r.stock_code || '—')}</span>` +
        `<span class="k">建仓</span><span class="v num">${esc(r.entry_date || '—')} @ ${fmt(r.entry_price)}</span>` +
        `<span class="k">止损</span><span class="v num">${fmt(r.stop_price)}</span>` +
        `<span class="k">止盈</span><span class="v num">${fmt(r.tp_price)}</span>` +
      `</div></div>`;
  });
  html += '</div>';
  el.innerHTML = html;
}

// ---------------------------------------------------------------------------
// 榜单
// ---------------------------------------------------------------------------

function renderPicks(date, rows) {
  const el = $('cb-board');
  if (!el) return;
  if (!rows || !rows.length) {
    el.innerHTML = '<div class="empty-state">该日期暂无转债榜单</div>';
    return;
  }
  const maxScore = rows.reduce((m, r) =>
    r.total_score != null && r.total_score > m ? r.total_score : m, 0);

  let html = '<div class="table-responsive app-card d-none d-md-block mb-3"><table class="app-table"><thead><tr>' +
    '<th>排名</th><th>代码</th><th>名称</th><th class="num">现价</th><th class="num">总评分</th>' +
    '<th class="num">债底</th><th class="num">股性</th><th class="num">条款</th>' +
    '<th class="num">仓位</th><th class="num">张数</th><th class="num">止损</th><th class="num">止盈</th>' +
    '<th>理由</th></tr></thead><tbody>';
  rows.forEach((r, i) => {
    const band = scoreBand(r.total_score, maxScore);
    html += `<tr class="${band}">` +
      `<td><span class="rank-badge">${i + 1}</span></td>` +
      `<td class="code">${esc(r.code)}</td><td>${esc(r.name)}</td>` +
      `<td class="num">${fmt(r.price)}</td>` +
      `<td class="num"><span class="score-chip">${r.total_score == null ? '—' : r.total_score}</span></td>` +
      `<td class="num">${r.a_score == null ? '—' : r.a_score}</td>` +
      `<td class="num">${r.b_score == null ? '—' : r.b_score}</td>` +
      `<td class="num">${r.c_score == null ? '—' : r.c_score}</td>` +
      `<td class="num">${pct(r.size_pct)}</td>` +
      `<td class="num">${r.shares}</td>` +
      `<td class="num">${fmt(r.stop_price)}</td>` +
      `<td class="num">${fmt(r.tp_price)}</td>` +
      `<td class="small">${esc(r.reason || '')}` +
      `${r.risk_note ? ` <span class="text-warning">${esc(r.risk_note)}</span>` : ''}</td>` +
      `</tr>`;
  });
  html += '</tbody></table></div>';

  html += '<div class="app-card d-md-none mb-3">';
  rows.forEach((r, i) => {
    const band = scoreBand(r.total_score, maxScore).replace('row-', 'band-');
    html += `<div class="plan-card ${band}"><div class="card-header-row">` +
      `<span class="rank-badge">${i + 1}</span>` +
      `<span class="code fw-semibold">${esc(r.code)}</span>` +
      `<span class="card-title-text">${esc(r.name)}</span>` +
      `<span class="score-chip">${r.total_score == null ? '—' : r.total_score}</span></div>` +
      `<div class="kv-grid">` +
        `<span class="k">现价</span><span class="v num">${fmt(r.price)}</span>` +
        `<span class="k">评分</span><span class="v num">债底 ${r.a_score ?? '—'} / 股性 ${r.b_score ?? '—'} / 条款 ${r.c_score ?? '—'}</span>` +
        `<span class="k">仓位</span><span class="v num">${pct(r.size_pct)} · ${r.shares} 张</span>` +
        `<span class="k">止损</span><span class="v num">${fmt(r.stop_price)}</span>` +
        `<span class="k">止盈</span><span class="v num">${fmt(r.tp_price)}</span>` +
      `</div>` +
      (r.reason ? `<div class="small text-muted mt-1">${esc(r.reason)}</div>` : '') +
      (r.risk_note ? `<div class="small text-warning mt-1">${esc(r.risk_note)}</div>` : '') +
      `</div>`;
  });
  html += '</div>';
  el.innerHTML = html;
}

// ---------------------------------------------------------------------------
// 加载
// ---------------------------------------------------------------------------

function fillDateOptions(dates) {
  const sel = $('cb-date');
  if (!sel) return;
  sel.innerHTML = '';
  (dates || []).forEach(d => sel.appendChild(new Option(d, d)));
  if (dates && dates.length) sel.value = dates[0];
}

async function loadPicks(date) {
  const data = await fetchCbPicks(date);
  renderPicks(data.date, data.rows || []);
  return data;
}

async function loadAll() {
  setStatus('加载中…', 'alert-info');
  try {
    const [datesRes, posRes, alertsRes] = await Promise.all([
      fetchCbDates().catch(() => ({ dates: [] })),
      fetchCbPositions().catch(() => ({ rows: [] })),
      fetchCbAlerts().catch(() => ({ alerts: [], generated_at: '' })),
    ]);
    fillDateOptions(datesRes.dates || []);
    const positions = posRes.rows || [];
    const alerts = alertsRes.alerts || [];

    const date = ($('cb-date') && $('cb-date').value) || (datesRes.dates || [])[0] || '';
    const picks = date ? await loadPicks(date) : { date: '', rows: [] };

    renderSummary((picks.rows || []).length, positions.length, alerts.length, picks.date);
    renderAlerts(alerts, alertsRes.generated_at);
    renderPositions(positions);
    setStatus('', '');
  } catch (e) {
    setStatus('加载失败：' + (e && e.message ? e.message : e), 'alert-danger');
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const sel = $('cb-date');
  if (sel) sel.addEventListener('change', () => loadPicks(sel.value));
  const btn = $('cb-refresh');
  if (btn) btn.addEventListener('click', loadAll);
  loadAll();
});
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_cb_web.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Verify JS syntax**

Run: `node --check static/js/cb.js`
Expected: no output (syntax valid)

- [ ] **Step 6: Commit**

```bash
git add static/js/cb.js tests/test_cb_web.py
git commit -m "feat(cb): static/js/cb.js 页面逻辑（摘要/预警/持仓/榜单）"
```

---

### Task 5: export_json.py 转债静态导出

**Files:**
- Modify: `export_json.py`
- Test: `tests/test_cb_web.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cb_web.py`:

```python
class TestCbExport:

    def test_export_includes_cb_files(self, cb_db, tmp_path):
        from export_json import export
        out = str(tmp_path / "site")
        export(cb_db, out)
        data_dir = os.path.join(out, "data")
        assert os.path.exists(os.path.join(data_dir, "cb-dates.json"))
        assert os.path.exists(os.path.join(data_dir, "cb-picks-2026-10-25.json"))
        assert os.path.exists(os.path.join(data_dir, "cb-positions.json"))
        assert os.path.exists(os.path.join(data_dir, "cb-alerts.json"))

        with open(os.path.join(data_dir, "cb-picks-2026-10-25.json"), encoding="utf-8") as f:
            picks = json.load(f)
        assert picks["date"] == "2026-10-25"
        assert picks["rows"][0]["code"] == "113046"

        with open(os.path.join(data_dir, "cb-positions.json"), encoding="utf-8") as f:
            pos = json.load(f)
        assert pos["rows"][0]["code"] == "113059"

    def test_export_writes_cb_html(self, cb_db, tmp_path):
        from export_json import export
        out = str(tmp_path / "site")
        export(cb_db, out)
        html_path = os.path.join(out, "cb.html")
        assert os.path.exists(html_path)
        body = open(html_path, encoding="utf-8").read()
        assert "可转债" in body
        assert "js/cb.js" in body
        assert 'window.APP_MODE="static"' in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_cb_web.py::TestCbExport -v`
Expected: FAIL — `cb-dates.json` not found

- [ ] **Step 3: Implement the export**

In `export_json.py`, add a helper function after `_dashboard_payload`:

```python
def _export_cb_data(conn, data_dir: Path) -> None:
    """导出可转债数据：榜单（按日期）、持仓、预警快照。"""
    from cb_repository import get_cb_positions, get_cb_picks

    cb_dates = [
        r[0] for r in conn.execute(
            "SELECT DISTINCT trade_date FROM cb_picks ORDER BY trade_date DESC"
        ).fetchall()
    ]
    json.dump({"dates": cb_dates}, (data_dir / "cb-dates.json").open("w"), ensure_ascii=False)
    for d in cb_dates:
        rows = get_cb_picks(conn, d)
        json.dump(
            {"date": d, "rows": rows},
            (data_dir / f"cb-picks-{d}.json").open("w"),
            ensure_ascii=False, default=str,
        )

    positions = get_cb_positions(conn)
    json.dump(
        {"rows": positions},
        (data_dir / "cb-positions.json").open("w"),
        ensure_ascii=False, default=str,
    )

    # 预警快照：导出时点抓一次行情（无持仓则不触网）
    generated_at = _dt_class.now().strftime("%Y-%m-%dT%H:%M:%S")
    alerts: list = []
    if positions:
        try:
            from cb_data_provider import fetch_redeem_status_map, fetch_universe_snapshot
            from strategies.convertible import evaluate_alerts

            universe = {r["code"]: r for r in fetch_universe_snapshot()}
            redeem_map = fetch_redeem_status_map()
            quotes = {}
            for code in {p["code"] for p in positions}:
                u = universe.get(code) or {}
                rd = redeem_map.get(code) or {}
                cp = u.get("convert_price")
                sp = u.get("stock_price")
                trig = rd.get("redeem_price")
                redeem_dist = None
                if trig and sp and sp > 0:
                    redeem_dist = round((trig / sp - 1.0) * 100.0, 4)
                quotes[code] = {
                    "price": u.get("price"),
                    "redeem_status": rd.get("redeem_status") or "",
                    "redeem_dist": redeem_dist,
                    "premium_rate": u.get("premium_rate"),
                    "stock_price": sp,
                    "convert_price": cp,
                }
            alerts = evaluate_alerts(positions, quotes)
        except Exception:
            alerts = []
    json.dump(
        {"generated_at": generated_at, "alerts": alerts},
        (data_dir / "cb-alerts.json").open("w"),
        ensure_ascii=False, default=str,
    )
```

In the `export()` function, inside the `with conn:` block (after the `json.dump(get_holdings_detail(conn), ...)` line), add:

```python
        _export_cb_data(conn, data_dir)
```

Then, after the `plan.html` write block, add the `cb.html` write block:

```python
    (out / "cb.html").write_text(
        _render_static_page("cb.html",
            today=_dt_class.now().strftime("%Y-%m-%d"),
            year=_dt_class.now().year),
        encoding="utf-8",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_cb_web.py::TestCbExport -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run full suite**

Run: `python3 -m pytest tests/ -q`
Expected: PASS (baseline + new tests, 0 regressions)

- [ ] **Step 6: Commit**

```bash
git add export_json.py tests/test_cb_web.py
git commit -m "feat(cb): export_json 导出转债数据（榜单/持仓/预警快照）+ cb.html"
```

---

### Task 6: 生成转债榜单数据 + 端到端验证

**Files:** None (verification only)

- [ ] **Step 1: Generate the cb picks into the DB**

```bash
cd /Users/shichaopeng/Work/self-dir/projects/a-finder-cli
python3 stock_cli.py cb-picks --top 20 --db hs300.db --concurrency 2 --rate 4 2>&1 | tail -5
```

Expected: a ranked list of convertible bonds written to `cb_picks`.

- [ ] **Step 2: Verify the DB has data**

```bash
python3 -c "
import sqlite3
c = sqlite3.connect('hs300.db')
for t in ['cb_universe','cb_factors','cb_picks','cb_positions']:
    n = c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
    print(f'{t}: {n}')
print('latest:', c.execute('SELECT MAX(trade_date) FROM cb_picks').fetchone()[0])
c.close()
"
```

Expected: `cb_factors` > 0, `cb_picks` = 20, latest date = today

- [ ] **Step 3: Verify the Flask page serves real data**

```bash
python3 -c "
from app import create_app
app = create_app()
c = app.test_client()
d = c.get('/api/cb/picks').get_json()
print('picks date:', d['date'], 'rows:', len(d['rows']))
p = c.get('/api/cb/positions').get_json()
print('positions:', len(p['rows']))
a = c.get('/api/cb/alerts').get_json()
print('alerts:', len(a['alerts']))
h = c.get('/api/dashboard').get_json()
print('dashboard cb:', h['cb'])
assert c.get('/cb').status_code == 200
print('/cb OK')
"
```

- [ ] **Step 4: Regenerate the static site**

```bash
python3 export_json.py --db hs300.db --out site 2>&1 | tail -2
ls site/data/cb-*.json site/cb.html
```

Expected: `cb-dates.json`, `cb-picks-<date>.json`, `cb-positions.json`, `cb-alerts.json`, `cb.html`

- [ ] **Step 5: Run the full test suite one final time**

```bash
python3 -m pytest tests/ -q
```

Expected: PASS, 0 regressions

- [ ] **Step 6: Commit any remaining changes**

```bash
git status --short
git add -A
git commit -m "chore(cb): 生成转债榜单数据" 2>&1 | tail -1 || true
```

---

## Self-Review

**Spec coverage:**
- §五 API 路由 (5 个) → Task 2
- §六 前端页面设计 → Task 3 (模板) + Task 4 (JS)
- §七 静态导出 (4 个 JSON + cb.html) → Task 5
- §四 4.4 dashboard 摘要 → Task 3 Step 6
- §二 目标 5 (导航入口) → Task 3 Step 4
- §九 测试策略 (12 类) → Tasks 1-5
- Task 6 端到端验证

**Placeholder scan:** No TBD/TODO/FIXME. All code blocks contain complete, runnable code.

**Type consistency:**
- `fetchCbPicks(date)` / `fetchCbDates()` / `fetchCbPositions()` / `fetchCbAlerts()` — same names in Task 1 (api.js), Task 4 (cb.js import), Task 5 (export paths)
- `get_cb_picks(conn, date)` / `get_cb_positions(conn)` — match cb_repository.py signatures
- `evaluate_alerts(positions, quotes)` — matches strategies/convertible.py
- Response shapes: `{date, rows}`, `{rows}`, `{generated_at, alerts}`, `{dates}` — consistent across Task 2, 4, 5
- `scoreBand(score, max)` — imported from table.js, exists there
- `_cb_latest_pick_date(conn)` — defined in Task 2, used in Task 3 Step 6
