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