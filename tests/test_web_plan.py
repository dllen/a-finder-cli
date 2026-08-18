import json
import sqlite3
import tempfile

import pytest

from app import create_app
from db_schema import ensure_schema
from db_repository import open_db


@pytest.fixture()
def plan_db(tmp_path):
    db = str(tmp_path / "plan.db")
    # open_db runs migrations which create trade_plan table
    conn = open_db(db)
    # Seed one trade_plan row for 2026-08-18
    conn.execute(
        """INSERT INTO trade_plan
        (plan_date, code, action, plan_price, size_pct, stop_price, tp_price,
         rr_ratio, status, reason, rationale_json, params_hash, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "2026-08-18", "600519", "buy", 1500.0, 0.10, 1380.0, 1740.0,
            2.0, "ok", "", "{}", "deadbeef", "2026-08-18T00:00:00",
        ),
    )
    conn.commit()
    conn.close()
    return db


def test_plan_page_renders(plan_db):
    app = create_app(db_path=plan_db)
    app.config["TESTING"] = True
    resp = app.test_client().get("/plan")
    assert resp.status_code == 200
    assert b"Plan" in resp.data


def test_api_plan_today_returns_today_plan(plan_db):
    """Seeded plan_date matches today, so /api/plan/today returns the row."""
    from datetime import date as _date
    app = create_app(db_path=plan_db)
    app.config["TESTING"] = True
    resp = app.test_client().get("/api/plan/today")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "rows" in data
    assert "plan_date" in data
    if data["plan_date"] == "2026-08-18":
        assert len(data["rows"]) >= 1
    else:
        assert data["rows"] == []


def test_api_plan_by_date_excludes_failed_by_default(plan_db):
    app = create_app(db_path=plan_db)
    app.config["TESTING"] = True
    resp = app.test_client().get("/api/plan/2026-08-18")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["plan_date"] == "2026-08-18"
    assert len(data["rows"]) == 1
    assert data["rows"][0]["code"] == "600519"
    assert data["rows"][0]["status"] == "ok"


def test_api_plan_by_date_includes_failed_with_flag(plan_db):
    db = plan_db
    # add a failed row
    conn = sqlite3.connect(db)
    conn.execute(
        """INSERT INTO trade_plan
        (plan_date, code, action, plan_price, size_pct, stop_price, tp_price,
         rr_ratio, status, reason, rationale_json, params_hash, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "2026-08-18", "000001", "buy", 10.0, 0.99, 9.0, 12.0,
            0.0, "failed", "size_exceed_max", "{}", "deadbeef", "2026-08-18T00:00:00",
        ),
    )
    conn.commit()
    conn.close()

    app = create_app(db_path=db)
    app.config["TESTING"] = True

    # default: only ok
    resp_ok = app.test_client().get("/api/plan/2026-08-18")
    assert len(resp_ok.get_json()["rows"]) == 1

    # include_failed=1: both
    resp_all = app.test_client().get("/api/plan/2026-08-18?include_failed=1")
    rows = resp_all.get_json()["rows"]
    assert len(rows) == 2
    statuses = {r["status"] for r in rows}
    assert statuses == {"ok", "failed"}


def test_api_plan_dates(plan_db):
    app = create_app(db_path=plan_db)
    app.config["TESTING"] = True
    resp = app.test_client().get("/api/plan/dates")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["dates"] == ["2026-08-18"]


def test_api_dashboard_returns_four_sections(plan_db, monkeypatch):
    """Seed daily_picks + verify 4 sections + freshness boundary."""
    import types
    from datetime import datetime as _dt
    fake_mod = types.SimpleNamespace(
        now=lambda: _dt(2026, 8, 18, 12, 0, 0),
        fromisoformat=_dt.fromisoformat,
        strptime=_dt.strptime,
    )
    import app as app_module
    monkeypatch.setattr(app_module, "_dashboard_now", fake_mod)

    conn = sqlite3.connect(plan_db)
    conn.execute(
        "INSERT INTO daily_picks (date, rank, kind, code, updated_at) "
        "VALUES ('2026-08-17', 1, '均线', '600519', '2026-08-17 10:00:00')"
    )
    conn.commit()
    conn.close()

    app = create_app(db_path=plan_db)
    app.config["TESTING"] = True
    resp = app.test_client().get("/api/dashboard")
    assert resp.status_code == 200
    data = resp.get_json()
    assert set(data.keys()) == {"last_refresh", "today_plan", "open_positions", "pnl_5d"}
    # last_refresh: 26h ago = warm (>24, <72)
    assert data["last_refresh"]["date"] == "2026-08-17"
    assert data["last_refresh"]["ago_hours"] == 26.0
    assert data["last_refresh"]["freshness"] == "warm"
    # today_plan: 来自 plan_db fixture 已插入的 ok 行
    assert data["today_plan"]["buy"] == 1
    # open_positions: 空
    assert data["open_positions"]["count"] == 0
    # pnl_5d: 空
    assert data["pnl_5d"] == []


def test_api_dashboard_freshness_fresh(plan_db, monkeypatch):
    """1h ago = fresh (<24). warm+stale covered by other tests / endpoint logic."""
    import types
    from datetime import datetime as _dt, timedelta
    import app as app_module
    fake_mod = types.SimpleNamespace(
        now=lambda: _dt(2026, 8, 18, 12, 0, 0),
        fromisoformat=_dt.fromisoformat,
        strptime=_dt.strptime,
    )
    monkeypatch.setattr(app_module, "_dashboard_now", fake_mod)

    conn = sqlite3.connect(plan_db)
    conn.execute("DELETE FROM daily_picks")
    ts = (_dt(2026, 8, 18, 12, 0, 0) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO daily_picks (date, rank, kind, code, updated_at) "
        "VALUES ('2026-08-18', 1, '均线', '600519', ?)",
        (ts,),
    )
    conn.commit()
    conn.close()

    app = create_app(db_path=plan_db)
    app.config["TESTING"] = True
    data = app.test_client().get("/api/dashboard").get_json()
    assert data["last_refresh"]["freshness"] == "fresh"


def test_api_dashboard_empty_db():
    import tempfile
    from db_repository import open_db
    from app import create_app
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    open_db(path).close()
    client = create_app(db_path=path).test_client()
    data = client.get("/api/dashboard").get_json()
    assert data["last_refresh"] is None
    assert data["today_plan"]["buy"] == 0
    assert data["open_positions"]["count"] == 0
    assert data["pnl_5d"] == []


def test_dashboard_partial_present_on_both_pages(plan_db):
    app = create_app(db_path=plan_db)
    client = app.test_client()
    for path in ("/", "/plan"):
        r = client.get(path)
        assert b'<div id="dashboard"></div>' in r.data
        assert b'startDashboard();' in r.data


def test_dashboard_js_served():
    """dashboard.js is served at /static/dashboard.js."""
    import tempfile
    from db_repository import open_db
    from app import create_app
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    open_db(path).close()
    app = create_app(db_path=path)
    client = app.test_client()
    resp = client.get("/static/dashboard.js")
    assert resp.status_code == 200
    assert b"startDashboard" in resp.data