import sqlite3
import tempfile
import os
import threading

import pytest

import app as app_module
from db_repository import open_db
from app import create_app


def _build_client(tmp_path, prices=(), outcomes=()):
    db = str(tmp_path / "test.db")
    conn = open_db(db)
    conn.execute(
        "INSERT INTO daily_picks (date, rank, kind, code, name, strategy, buy, stop, target, score) "
        "VALUES ('2026-08-15', 1, '均线', '600519', '贵州茅台', '突破', 1500.0, 1450.0, 1600.0, 9.5)"
    )
    conn.execute(
        "INSERT INTO daily_picks (date, rank, kind, code, name, strategy, buy, stop, target, score) "
        "VALUES ('2026-08-15', 1, '买入信号', '000001', '平安银行', '均线突破', 10.0, 9.5, 11.0, 8.0)"
    )
    for code, trade_date, close in prices:
        conn.execute(
            "INSERT OR REPLACE INTO daily_prices (code, trade_date, close) VALUES (?, ?, ?)",
            (code, trade_date, close),
        )
    for date, code, strategy, buy, outcome_pct, win in outcomes:
        conn.execute(
            "INSERT OR REPLACE INTO pick_outcomes "
            "(date, source, code, strategy, name, kind, score, buy, stop, target, "
            " exit_date, exit_price, outcome_pct, win, labeled_at) "
            "VALUES (?, 'replay', ?, ?, '', '', NULL, ?, NULL, NULL, NULL, NULL, ?, ?, 'now')",
            (date, code, strategy, buy, outcome_pct, win),
        )
    conn.commit()
    conn.close()
    app = create_app(db_path=db)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture()
def client(tmp_path):
    return _build_client(tmp_path)


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
    assert data["groups"]["买入信号"][0]["score"] is not None
    assert isinstance(data["groups"]["买入信号"][0]["score"], (int, float))


def test_refresh_and_job(client, monkeypatch):
    gate = threading.Event()

    def fake_run_picks(db_path, top, do_sync, progress=None):
        gate.wait()
        return {"date": "", "ma": 0, "buy": 0}

    monkeypatch.setattr(app_module, "run_picks", fake_run_picks)

    resp = client.post("/api/refresh", json={"sync": False})
    assert resp.status_code == 202
    job_id = resp.get_json()["job_id"]

    # 首个任务被 gate 阻塞，仍在运行，重复提交应 409
    busy = client.post("/api/refresh", json={"sync": False})
    assert busy.status_code == 409

    gate.set()

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


def test_picks_ret_pct(tmp_path):
    c = _build_client(
        tmp_path,
        prices=[("600519", "2026-08-20", 1650.0), ("000001", "2026-08-20", 9.0)],
    )
    data = c.get("/api/picks?date=2026-08-15").get_json()
    assert data["groups"]["均线"][0]["ret_pct"] == 10.0   # (1650/1500-1)*100
    assert data["groups"]["买入信号"][0]["ret_pct"] == -10.0  # (9/10-1)*100


def test_picks_ret_pct_null_without_prices(client):
    data = client.get("/api/picks?date=2026-08-15").get_json()
    assert data["groups"]["均线"][0]["ret_pct"] is None


def test_stats(tmp_path):
    c = _build_client(
        tmp_path,
        outcomes=[
            ("2026-07-01", "600000", "策略A", 10.0, 0.05, 1),
            ("2026-07-02", "600001", "策略A", 10.0, -0.02, 0),
            ("2026-08-01", "600002", "策略B", 10.0, 0.10, 1),
        ],
    )
    data = c.get("/api/stats").get_json()
    strategies = {s["strategy"]: s for s in data["strategies"]}
    assert strategies["策略A"]["n"] == 2
    assert strategies["策略A"]["win_rate"] == 50.0
    assert strategies["策略A"]["expectancy"] == 1.5  # (0.05-0.02)/2*100
    assert strategies["策略B"]["n"] == 1
    months = {m["month"]: m for m in data["monthly"]}
    assert months["2026-07"]["n"] == 2
    assert months["2026-07"]["win_rate"] == 50.0
    assert months["2026-08"]["n"] == 1


def test_stats_empty(client):
    data = client.get("/api/stats").get_json()
    assert data == {"strategies": [], "monthly": []}
