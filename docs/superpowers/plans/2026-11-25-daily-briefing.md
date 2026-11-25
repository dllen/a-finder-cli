# 每日简报（时间流）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a /briefing page that renders a daily market briefing as a timeline, generated automatically after each sync with LLM narrative via the DSH plugin.

**Architecture:** Python script extracts structured data → briefing-input.json; DSH plugin delivers prompt to agent after sync → agent writes briefing-YYYY-MM-DD.json; frontend reads either file (LLM version preferred, data-only fallback). Static export copies both to site/data/.

**Tech Stack:** Python 3.10+ (stdlib only), Vanilla ES6 modules, Jinja2 templates, Hallmark Console dark theme CSS, DSH Schedule service (new_once target).

**Spec:** `docs/superpowers/specs/2026-11-25-daily-briefing-design.md`

---

## File Structure

| File | Responsibility |
|---|---|
| `scripts/generate_briefing_input.py` | Extract structured summary from islands/picks/cb → data/briefing-input.json |
| `plugins/dsh-a-finder-sync/lib/briefing.js` | Build prompt, validate output schema, deliver via Schedule |
| `templates/briefing.html` | Page template extending base.html |
| `static/js/briefing.js` | Read JSON, render timeline DOM, date filter integration |
| `static/css/briefing.css` | Timeline styles (Console dark theme) |
| `tests/test_briefing.py` | All Python-side tests |
| `a-finder.sh` | Add step [5/5] calling generate_briefing_input.py |
| `app.py` | /briefing route + /api/briefing/* endpoints |
| `export_json.py` | Copy briefing JSON + render briefing.html |
| `templates/components/nav.html` | Add 每日简报 link |
| `static/js/api.js` | fetchBriefing / fetchBriefingDates / fetchBriefingInput |
| `plugins/dsh-a-finder-sync/lib/index.js` | Trigger briefing after performSync |
| `plugins/dsh-a-finder-sync/package.json` | Version bump 0.4.0 → 0.5.0 |

---

### Task 1: generate_briefing_input.py — Data Extraction

**Files:**
- Create: `scripts/generate_briefing_input.py`
- Create: `tests/test_briefing.py`

- [ ] **Step 1: Write failing tests for extraction logic**

```python
# tests/test_briefing.py
"""每日简报：数据提取、Flask 路由、静态导出、插件契约的回归测试。"""
from __future__ import annotations
import json
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_script():
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import generate_briefing_input as mod
        return mod
    finally:
        sys.path.pop(0)


class TestBriefingInputExtraction:
    def test_extracts_intraday_peak(self, tmp_path):
        mod = _load_script()
        islands = {
            "initData": {
                "date": "2026-11-24",
                "times": ["09:31", "14:57", "15:00"],
                "bars": [33.17, 24.84, 3.88],
            },
            "cmpData": {"latest": {"date": "2026-11-24", "amount": 856.21, "netflow": -118.7}},
            "dailyData": {"dates": ["2026-11-24"], "amount": [856.21], "netflow": [-118.7]},
            "broadData": {
                "dates": ["2026-11-24"],
                "cats": ["沪深300", "中证1000"],
                "series": {"沪深300": [-36.13], "中证1000": [46.96]},
                "total": [-115.86],
            },
        }
        result = mod.extract_intraday(islands)
        assert result["peak_minute"]["time"] == "14:57"
        assert result["peak_minute"]["amount_yi"] == 24.84
        assert result["total_amount_yi"] == 856.21
        assert result["total_netflow_yi"] == -118.7

    def test_extracts_sectors_top3(self, tmp_path):
        mod = _load_script()
        islands = {
            "broadData": {
                "dates": ["2026-11-24"],
                "cats": ["沪深300", "中证1000", "科创50", "创业板指"],
                "series": {"沪深300": [-36.13], "中证1000": [46.96], "科创50": [-12.89], "创业板指": [-11.77]},
                "total": [-115.86],
            },
        }
        result = mod.extract_sectors(islands)
        assert result["top_inflow"][0]["name"] == "中证1000"
        assert result["top_outflow"][0]["name"] == "沪深300"
        assert len(result["top_inflow"]) <= 3
        assert len(result["top_outflow"]) <= 3

    def test_extracts_picks_from_json(self, tmp_path):
        mod = _load_script()
        picks_file = tmp_path / "picks-2026-11-24.json"
        picks_file.write_text(json.dumps({
            "date": "2026-11-24",
            "groups": {
                "买入信号": [{"rank": 1, "code": "002179", "name": "中航光电", "strategy": "均线突破", "day_pct": 1.83}],
                "高胜率": [{"rank": 1, "code": "601689", "name": "拓普集团", "strategy": "量价齐升（6次）", "day_pct": 2.63}],
            },
        }), encoding="utf-8")
        result = mod.extract_picks(picks_file)
        assert result["buy_signals"][0]["code"] == "002179"
        assert result["high_winrate"][0]["code"] == "601689"
        assert result["count_by_group"]["买入信号"] == 1

    def test_missing_picks_returns_empty(self, tmp_path):
        mod = _load_script()
        result = mod.extract_picks(tmp_path / "nonexistent.json")
        assert result["buy_signals"] == []
        assert result["count_by_group"] == {}

    def test_recent_events_from_daily_data(self):
        mod = _load_script()
        islands = {
            "dailyData": {
                "dates": ["2026-11-18", "2026-11-19", "2026-11-20", "2026-11-23", "2026-11-24"],
                "netflow": [50.0, -20.0, 903.87, 160.47, -118.7],
            },
            "cmpData": {"support": [{"date": "2026-11-20", "amount": 1545.07, "netflow": 903.87}]},
        }
        events = mod.extract_recent_events(islands, n=5)
        assert len(events) >= 1
        assert any(e["type"] == "放量日" for e in events)

    def test_full_build_produces_valid_schema(self, tmp_path):
        mod = _load_script()
        # Minimal valid inputs
        etf_dir = tmp_path / "etf"
        etf_dir.mkdir()
        (etf_dir / "islands.json").write_text(json.dumps({
            "initData": {"date": "2026-11-24", "times": ["14:57"], "bars": [24.84]},
            "cmpData": {"latest": {"date": "2026-11-24", "amount": 856.21, "netflow": -118.7}, "support": []},
            "dailyData": {"dates": ["2026-11-24"], "amount": [856.21], "netflow": [-118.7]},
            "broadData": {"dates": ["2026-11-24"], "cats": ["沪深300"], "series": {"沪深300": [-36.13]}, "total": [-36.13]},
        }), encoding="utf-8")
        site_data = tmp_path / "site" / "data"
        site_data.mkdir(parents=True)
        (site_data / "picks-2026-11-24.json").write_text(json.dumps({"date": "2026-11-24", "groups": {}}), encoding="utf-8")

        result = mod.build_briefing_input(etf_dir, site_data, db_path=None)
        assert result["date"] == "2026-11-24"
        assert result["is_trading_day"] is True
        assert "intraday" in result
        assert "sectors" in result
        assert "picks" in result
        assert "recent_events" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/shichaopeng/Work/self-dir/projects/a-finder-cli && ./.venv/bin/python -m pytest tests/test_briefing.py -q`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Implement generate_briefing_input.py**

```python
#!/usr/bin/env python3
"""从 ETF islands / picks / cb 数据提取每日简报的结构化摘要。

产出 data/briefing-input.json，供 DSH 插件投递给 agent 生成 LLM 叙事，
也供前端在无 LLM 时渲染纯数据兜底版。

用法：
    python3 scripts/generate_briefing_input.py [--db hs300.db] [--out data]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def extract_intraday(islands: dict) -> dict:
    init = islands.get("initData") or {}
    times = init.get("times") or []
    bars = init.get("bars") or []
    cmp_latest = (islands.get("cmpData") or {}).get("latest") or {}

    peak_time, peak_val = "", 0.0
    for t, b in zip(times, bars):
        if b > peak_val:
            peak_val = b
            peak_time = t

    # Hourly aggregation from minute bars
    hourly = {}
    for t, b in zip(times, bars):
        hour_key = t[:2] + ":00-" + str(int(t[:2]) + 1).zfill(2) + ":00"
        hourly[hour_key] = hourly.get(hour_key, 0.0) + b
    hourly_flow = [{"hour": k, "amount_yi": round(v, 2)} for k, v in sorted(hourly.items())]

    return {
        "peak_minute": {"time": peak_time, "amount_yi": round(peak_val, 2)},
        "hourly_flow": hourly_flow,
        "total_amount_yi": cmp_latest.get("amount", 0),
        "total_netflow_yi": cmp_latest.get("netflow", 0),
    }


def extract_sectors(islands: dict) -> dict:
    bd = islands.get("broadData") or {}
    dates = bd.get("dates") or []
    if not dates:
        return {"top_inflow": [], "top_outflow": []}
    last = len(dates) - 1
    cats = bd.get("cats") or []
    series = bd.get("series") or {}
    items = []
    for c in cats:
        vals = series.get(c) or []
        v = vals[last] if last < len(vals) else 0
        items.append({"name": c, "netflow_yi": round(v, 2)})
    inflow = sorted([x for x in items if x["netflow_yi"] > 0], key=lambda x: -x["netflow_yi"])[:3]
    outflow = sorted([x for x in items if x["netflow_yi"] < 0], key=lambda x: x["netflow_yi"])[:3]
    return {"top_inflow": inflow, "top_outflow": outflow}


def extract_picks(picks_path: Path) -> dict:
    if not picks_path.exists():
        return {"buy_signals": [], "high_winrate": [], "count_by_group": {}}
    try:
        data = json.loads(picks_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"buy_signals": [], "high_winrate": [], "count_by_group": {}}
    groups = data.get("groups") or {}
    buy = groups.get("买入信号") or []
    high = groups.get("高胜率") or []
    counts = {k: len(v) for k, v in groups.items()}
    fields = ("code", "name", "strategy", "day_pct")
    return {
        "buy_signals": [{k: r.get(k) for k in fields} for r in buy[:5]],
        "high_winrate": [{k: r.get(k) for k in fields} for r in high[:3]],
        "count_by_group": counts,
    }


def extract_cb(site_data: Path, date: str) -> dict:
    alerts_path = site_data / "cb-alerts.json"
    picks_path = site_data / f"cb-picks-{date}.json"
    alerts_count = 0
    if alerts_path.exists():
        try:
            alerts = json.loads(alerts_path.read_text(encoding="utf-8"))
            alerts_count = len(alerts.get("alerts") or [])
        except (json.JSONDecodeError, OSError):
            pass
    top_picks = []
    if picks_path.exists():
        try:
            data = json.loads(picks_path.read_text(encoding="utf-8"))
            rows = data.get("picks") or []
            top_picks = [{"code": r.get("code"), "name": r.get("name"), "score": r.get("score")} for r in rows[:3]]
        except (json.JSONDecodeError, OSError):
            pass
    return {"alerts_count": alerts_count, "top_picks": top_picks}


def extract_recent_events(islands: dict, n: int = 5) -> list:
    dd = islands.get("dailyData") or {}
    dates = dd.get("dates") or []
    netflows = dd.get("netflow") or []
    support_dates = {s["date"] for s in (islands.get("cmpData") or {}).get("support") or []}
    events = []
    for i in range(max(0, len(dates) - n), len(dates)):
        d = dates[i]
        nf = netflows[i] if i < len(netflows) else 0
        if d in support_dates:
            events.append({"date": d, "type": "放量日", "detail": f"净流入 {nf:.1f} 亿"})
        elif nf < -100:
            events.append({"date": d, "type": "大幅净赎回", "detail": f"净流出 {abs(nf):.1f} 亿"})
        elif nf > 100:
            events.append({"date": d, "type": "大幅净申购", "detail": f"净流入 {nf:.1f} 亿"})
    return events[-4:]


def build_briefing_input(etf_dir: Path, site_data: Path, db_path: str | None = None) -> dict:
    islands_path = etf_dir / "islands.json"
    if not islands_path.exists():
        raise FileNotFoundError(f"{islands_path} not found")
    islands = json.loads(islands_path.read_text(encoding="utf-8"))

    cmp_latest = (islands.get("cmpData") or {}).get("latest") or {}
    date = cmp_latest.get("date") or ""
    if not date:
        dd = islands.get("dailyData") or {}
        dates = dd.get("dates") or []
        date = dates[-1] if dates else ""

    # Determine trading day from DB if available
    is_trading_day = True
    if db_path:
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            row = conn.execute("SELECT MAX(trade_date) FROM daily_prices").fetchone()
            conn.close()
            if row and row[0]:
                is_trading_day = (date == row[0])
        except Exception:
            pass

    picks_path = site_data / f"picks-{date}.json"
    return {
        "date": date,
        "is_trading_day": is_trading_day,
        "intraday": extract_intraday(islands),
        "sectors": extract_sectors(islands),
        "picks": extract_picks(picks_path),
        "cb": extract_cb(site_data, date),
        "recent_events": extract_recent_events(islands),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="生成每日简报输入数据")
    ap.add_argument("--db", default="hs300.db")
    ap.add_argument("--out", default="data")
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent
    etf_dir = root / "data" / "etf"
    site_data = root / "site" / "data"
    out_dir = root / args.out

    try:
        result = build_briefing_input(etf_dir, site_data, db_path=str(root / args.db))
    except FileNotFoundError as e:
        print(f"[briefing] 跳过：{e}")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "briefing-input.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"[briefing] {out_path} ({result['date']}, trading={result['is_trading_day']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_briefing.py -q`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/generate_briefing_input.py tests/test_briefing.py
git commit -m "feat(briefing): 数据提取脚本 generate_briefing_input.py + 测试"
```

---

### Task 2: Flask Routes + API + Nav

**Files:**
- Modify: `app.py` (add routes before final `return app`)
- Modify: `templates/components/nav.html`
- Modify: `static/js/api.js`
- Create: `templates/briefing.html`
- Test: `tests/test_briefing.py` (append)

- [ ] **Step 1: Write failing route tests**

Append to `tests/test_briefing.py`:

```python
class TestBriefingRoutes:
    @pytest.fixture()
    def client(self):
        from app import create_app
        app = create_app(str(ROOT / "hs300.db"), 10)
        app.config.update(TESTING=True)
        return app.test_client()

    def test_briefing_page_renders(self, client):
        r = client.get("/briefing")
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        assert "每日简报" in body
        assert 'id="briefing-timeline"' in body

    def test_briefing_api_input(self, client):
        r = client.get("/api/briefing/input")
        # May be 404 if no data yet — that's valid
        assert r.status_code in (200, 404)

    def test_briefing_api_dates(self, client):
        r = client.get("/api/briefing/dates")
        assert r.status_code == 200
        assert "dates" in r.get_json()

    def test_nav_has_briefing_entry(self, client):
        body = client.get("/briefing").get_data(as_text=True)
        assert 'data-nav="briefing"' in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_briefing.py::TestBriefingRoutes -q`
Expected: FAIL (404 on /briefing)

- [ ] **Step 3: Add routes to app.py**

Insert before the final `return app` in `create_app()`:

```python
    # ------------------------------------------------------------------
    # 每日简报（2026-11-25 新增）
    # ------------------------------------------------------------------

    BRIEFING_DATA_DIR = Path(__file__).parent / "data"
    BRIEFING_SITE_DIR = Path(__file__).parent / "site" / "data"

    @app.get("/briefing")
    def briefing_page():
        from datetime import date as _date
        return render_template("briefing.html",
            today=_date.today().isoformat(),
            app_mode="api", data_prefix="",
            year=_date.today().year)

    @app.get("/api/briefing/input")
    def briefing_input():
        path = BRIEFING_DATA_DIR / "briefing-input.json"
        if not path.exists():
            return jsonify({"error": "简报数据未生成"}), 404
        return jsonify(json.loads(path.read_text(encoding="utf-8")))

    @app.get("/api/briefing/dates")
    def briefing_dates():
        dates = []
        for f in sorted(BRIEFING_SITE_DIR.glob("briefing-*.json"), reverse=True):
            d = f.stem[len("briefing-"):]
            if len(d) == 10:
                dates.append(d)
        # Also include input date if no LLM version yet
        inp = BRIEFING_DATA_DIR / "briefing-input.json"
        if inp.exists():
            try:
                data = json.loads(inp.read_text(encoding="utf-8"))
                d = data.get("date", "")
                if d and d not in dates:
                    dates.insert(0, d)
            except (json.JSONDecodeError, OSError):
                pass
        return jsonify({"dates": dates})

    @app.get("/api/briefing/<date>")
    def briefing_for_date(date):
        # Prefer LLM version, fall back to input
        llm_path = BRIEFING_SITE_DIR / f"briefing-{date}.json"
        if llm_path.exists():
            return jsonify(json.loads(llm_path.read_text(encoding="utf-8")))
        inp = BRIEFING_DATA_DIR / "briefing-input.json"
        if inp.exists():
            data = json.loads(inp.read_text(encoding="utf-8"))
            if data.get("date") == date:
                data["_fallback"] = True
                return jsonify(data)
        return jsonify({"error": "该日期无简报"}), 404
```

- [ ] **Step 4: Create templates/briefing.html**

```html
{% extends "base.html" %}
{% set active_page = "briefing" %}
{% block title %}每日简报{% endblock %}
{% block description %}每日市场简报：盘中资金节奏、近期事件、选股亮点与转债预警，时间流呈现。{% endblock %}
{% block body %}
<main class="container briefing-page" id="main">
<header class="briefing-head">
<h1 class="briefing-head__title">每日简报</h1>
<div class="briefing-head__meta">
<span class="etf-tag" id="briefingDate">--</span>
<span class="etf-tag" id="briefingVersion">--</span>
</div>
</header>
{% include 'components/date_filter.html' %}
<div id="briefing-timeline" class="briefing-timeline"></div>
<div id="briefing-events" class="briefing-events"></div>
<div id="briefing-picks" class="briefing-picks"></div>
<div id="briefing-cb" class="briefing-cb"></div>
<p class="etf-source">数据来源：Choice、东方财富、交易所两融披露。客观数据仅供参考，不构成投资建议。</p>
</main>
{% endblock %}
{% block scripts %}
<script type="module" src="{{ url_for('static', filename='js/briefing.js') }}"></script>
{% endblock %}
```

- [ ] **Step 5: Add nav entry**

In `templates/components/nav.html`, after the ETF link:
```html
<a class="nav-link" data-nav="briefing" href="/briefing" {% if active_page == 'briefing' %}aria-current="page"{% endif %}>每日简报</a>
```

- [ ] **Step 6: Add api.js fetchers**

Append to `static/js/api.js` before the static-mode nav fix block:
```javascript
export function fetchBriefingInput() {
  return isStatic()
    ? getJSON(dsPath('data/briefing-input.json'))
    : getJSON('/api/briefing/input');
}

export function fetchBriefingDates() {
  return isStatic()
    ? getJSON(dsPath('data/briefing-dates.json'))
    : getJSON('/api/briefing/dates');
}

export function fetchBriefing(date) {
  return isStatic()
    ? getJSON(dsPath('data/briefing-' + date + '.json'))
    : getJSON('/api/briefing/' + date);
}
```

Also add to the static-mode nav fix:
```javascript
    const briefingLink = document.querySelector('a[data-nav="briefing"]');
    if (briefingLink) briefingLink.href = 'briefing.html';
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_briefing.py -q`
Expected: all passed

- [ ] **Step 8: Commit**

```bash
git add app.py templates/briefing.html templates/components/nav.html static/js/api.js tests/test_briefing.py
git commit -m "feat(briefing): Flask 路由 + 模板 + 导航 + API fetchers"
```

---

### Task 3: Frontend Timeline Rendering

**Files:**
- Create: `static/js/briefing.js`
- Create: `static/css/briefing.css`
- Modify: `templates/base.html` (link CSS)

- [ ] **Step 1: Create briefing.css**

Timeline styles using Console dark theme tokens. Left vertical line + dot nodes, right text. Cards for picks. Responsive single-column on mobile.

- [ ] **Step 2: Create briefing.js**

Vanilla ES6 module that:
1. Imports fetchBriefing/fetchBriefingDates/fetchBriefingInput from api.js
2. On load: fetch dates → populate date-filter → fetch latest briefing
3. If response has `_fallback: true` or no narrative fields → render data-only version with rule-based short sentences
4. Otherwise render LLM narrative version
5. Timeline DOM: `<div class="tl-node"><span class="tl-dot"></span><div class="tl-content"><time>HH:MM</time><p>narrative</p></div></div>`
6. Events section: reverse-chronological list
7. Picks cards: code/name/strategy/day_pct with up/down coloring
8. CB badge: alert count or hidden

- [ ] **Step 3: Link CSS in base.html**

After etf-dashboard.css link:
```html
<link rel="stylesheet" href="{{ url_for('static', filename='css/briefing.css') }}">
```

- [ ] **Step 4: Verify JS syntax**

Run: `node --check static/js/briefing.js`
Expected: exit 0

- [ ] **Step 5: Headless browser verification**

Start Flask, open /briefing in headless Chrome, confirm DOM has timeline nodes and no console errors.

- [ ] **Step 6: Commit**

```bash
git add static/js/briefing.js static/css/briefing.css templates/base.html
git commit -m "feat(briefing): 前端时间流渲染 + 样式"
```

---

### Task 4: Static Export Integration

**Files:**
- Modify: `export_json.py`
- Modify: `a-finder.sh`
- Test: `tests/test_briefing.py` (append)

- [ ] **Step 1: Write export tests**

```python
class TestBriefingExport:
    def test_export_copies_briefing_input(self, tmp_path, monkeypatch):
        import export_json as ex
        # Create fake briefing-input.json
        src = tmp_path / "src"
        src.mkdir()
        (src / "briefing-input.json").write_text('{"date":"2026-11-24"}', encoding="utf-8")
        monkeypatch.setattr(ex, "BRIEFING_SOURCE_DIR", src)
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        ok = ex._export_briefing_data(data_dir)
        assert ok is True
        assert (data_dir / "briefing-input.json").exists()

    def test_export_no_data_skips(self, tmp_path, monkeypatch):
        import export_json as ex
        monkeypatch.setattr(ex, "BRIEFING_SOURCE_DIR", tmp_path / "nonexistent")
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        assert ex._export_briefing_data(data_dir) is False

    def test_briefing_html_renderable(self):
        from export_json import _render_static_page
        html = _render_static_page("briefing.html", today="2026-11-24", year=2026)
        assert "每日简报" in html
        assert 'id="briefing-timeline"' in html
```

- [ ] **Step 2: Add _export_briefing_data to export_json.py**

Similar pattern to `_export_etf_data`: copy briefing-input.json + any briefing-*.json from site/data/, generate briefing-dates.json, render briefing.html.

- [ ] **Step 3: Add step [5/5] to a-finder.sh cmd_site**

After the export step, before the monotonicity guard:
```bash
  echo "[5/5] 生成简报输入数据..."
  run_py "$ROOT_DIR/scripts/generate_briefing_input.py" --db "$db" \
    || echo "WARN: 简报输入生成失败，继续（简报页沿用上一份）" >&2
```

Renumber existing steps [1/4]→[1/5], [2/4]→[2/5], [3/4]→[3/5], [4/4]→[4/5].

- [ ] **Step 4: Update test_site_cb_sync.py step numbering test**

Change assertions from [1/4]..[4/4] to [1/5]..[5/5].

- [ ] **Step 5: Run full test suite**

Run: `./.venv/bin/python -m pytest tests/ -q`
Expected: all passed

- [ ] **Step 6: Commit**

```bash
git add export_json.py a-finder.sh tests/test_briefing.py tests/test_site_cb_sync.py
git commit -m "feat(briefing): 静态导出 + 流水线第 5 步"
```

---

### Task 5: Plugin Briefing Module

**Files:**
- Create: `plugins/dsh-a-finder-sync/lib/briefing.js`
- Modify: `plugins/dsh-a-finder-sync/lib/index.js`
- Modify: `plugins/dsh-a-finder-sync/package.json`
- Test: `tests/test_plugin_cb_sync.py` (append)

- [ ] **Step 1: Write plugin contract tests**

```python
def test_briefing_prompt_contains_data(project):
    """prompt 包含结构化数据和生成指令。"""
    briefing_js = PLUGIN_LIB / "briefing.js"
    source = """
const mod = await import('__BRIEFING_JS__')
const input = { date: '2026-11-24', is_trading_day: true, intraday: { peak_minute: { time: '14:57', amount_yi: 24.84 } } }
const prompt = mod.buildBriefingPrompt(input, '/path/to/project')
process.stdout.write(JSON.stringify({
  hasDate: prompt.includes('2026-11-24'),
  hasPeak: prompt.includes('14:57'),
  hasInstruction: prompt.includes('时间') || prompt.includes('节点'),
  hasJsonSchema: prompt.includes('JSON'),
}))
""".replace("__BRIEFING_JS__", briefing_js.as_uri())
    got = _run_node(source, project)
    assert got["hasDate"] is True
    assert got["hasPeak"] is True
    assert got["hasInstruction"] is True
    assert got["hasJsonSchema"] is True


def test_briefing_skips_non_trading_day(project):
    briefing_js = PLUGIN_LIB / "briefing.js"
    source = """
const mod = await import('__BRIEFING_JS__')
const input = { date: '2026-11-24', is_trading_day: false }
const should = mod.shouldGenerateBriefing(input, '2026-11-24')
process.stdout.write(JSON.stringify({ should }))
""".replace("__BRIEFING_JS__", briefing_js.as_uri())
    got = _run_node(source, project)
    assert got["should"] is False


def test_briefing_validates_output_schema(project):
    briefing_js = PLUGIN_LIB / "briefing.js"
    source = """
const mod = await import('__BRIEFING_JS__')
const good = { date: '2026-11-24', intraday_nodes: [{time:'09:30',text:'开盘'}], events: [], picks_highlight: [] }
const bad = { date: '2026-11-24' }
const wrongDate = { date: '2026-11-20', intraday_nodes: [], events: [], picks_highlight: [] }
process.stdout.write(JSON.stringify({
  good: mod.validateBriefingOutput(good, '2026-11-24'),
  bad: mod.validateBriefingOutput(bad, '2026-11-24'),
  wrongDate: mod.validateBriefingOutput(wrongDate, '2026-11-24'),
}))
""".replace("__BRIEFING_JS__", briefing_js.as_uri())
    got = _run_node(source, project)
    assert got["good"] is True
    assert got["bad"] is False
    assert got["wrongDate"] is False
```

- [ ] **Step 2: Implement briefing.js**

Exports: `buildBriefingPrompt(input, projectDir)`, `shouldGenerateBriefing(input, dbLatest)`, `validateBriefingOutput(output, expectedDate)`, `deliverBriefingTask(service, input, projectDir)`.

- [ ] **Step 3: Wire into index.js performSync**

After the successful sync return, check briefing-input.json and call deliverBriefingTask if conditions met. Use Schedule service.create with timing `{ kind: 'at', at: new Date().toISOString() }` for immediate one-shot delivery.

- [ ] **Step 4: Bump version to 0.5.0**

- [ ] **Step 5: Run tests**

Run: `./.venv/bin/python -m pytest tests/test_plugin_cb_sync.py tests/test_briefing.py -q`
Expected: all passed

- [ ] **Step 6: Commit**

```bash
git add plugins/dsh-a-finder-sync/lib/briefing.js plugins/dsh-a-finder-sync/lib/index.js plugins/dsh-a-finder-sync/package.json tests/test_plugin_cb_sync.py
git commit -m "feat(plugin): 简报生成模块 + performSync 后自动投递"
```

---

### Task 6: Documentation + Final Verification

**Files:**
- Modify: `CLAUDE.md`
- Modify: `README.md`
- Modify: `plugins/dsh-a-finder-sync/README.md`

- [ ] **Step 1: Add briefing section to CLAUDE.md and README.md**

Document the data flow, fallback behavior, and plugin trigger mechanism.

- [ ] **Step 2: Run full test suite**

Run: `./.venv/bin/python -m pytest tests/ -q`
Expected: all passed (912+ new tests)

- [ ] **Step 3: End-to-end verification**

1. Run `python3 scripts/generate_briefing_input.py` → confirm data/briefing-input.json produced
2. Start Flask → open /briefing → confirm timeline renders (data fallback version)
3. Static export → confirm briefing.html + briefing-input.json in site/
4. Headless Chrome → no console errors, timeline nodes visible

- [ ] **Step 4: Commit + push**

```bash
git add CLAUDE.md README.md plugins/dsh-a-finder-sync/README.md
git commit -m "docs: 每日简报功能文档"
git push origin main
```
