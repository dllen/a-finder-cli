import sqlite3
import tempfile
import os
import threading

import pytest

import app as app_module
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
