import os
import tempfile
import pytest
from db_repository import open_db, insert_open_position, insert_trade_event, close_open_position


@pytest.fixture
def fresh_db_client(tmp_path, monkeypatch):
    path = str(tmp_path / "m.db")
    conn = open_db(path)
    conn.execute(
        """INSERT INTO daily_picks (date, rank, kind, code, name, strategy,
             buy, stop, target, score)
           VALUES ('2026-09-07', 1, 'test', '600519', 'M', 'test', 100, 80, 140, 2.0)"""
    )
    conn.execute(
        """INSERT INTO daily_prices (code, trade_date, open, close, high, low,
                                    volume, amount) VALUES ('600519','2026-09-07',100,110,110,100,0,0)"""
    )
    conn.execute(
        """INSERT INTO hs300_metadata (code, name, industry, region)
           VALUES ('600519', 'M', '酒', '贵州')"""
    )
    conn.commit()
    conn.close()

    from app import create_app
    app = create_app(db_path=path)
    app.config["TESTING"] = True
    client = app.test_client()
    return client, path


def test_portfolio_page_renders(fresh_db_client):
    client, _ = fresh_db_client
    resp = client.get("/portfolio")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "持仓组合" in body


def test_api_portfolio_summary_returns_all_tiers(fresh_db_client):
    client, _ = fresh_db_client
    resp = client.get("/api/portfolio/summary?date=2026-09-07")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["date"] == "2026-09-07"
    assert "tiers" in data
    tier_labels = {t["label"] for t in data["tiers"]}
    assert "5W" in tier_labels and "10W" in tier_labels and "50W" in tier_labels


def test_api_portfolio_summary_empty_data(fresh_db_client):
    """还没有任何 plan 写过 → 仍返回全 10 档（capital=100000 默认，pnl=0）。"""
    client, _ = fresh_db_client
    resp = client.get("/api/portfolio/summary")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["tiers"]) == 10


def test_api_portfolio_detail_returns_label_data(fresh_db_client):
    client, _ = fresh_db_client
    resp = client.get("/api/portfolio/10W?date=2026-09-07")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["label"] == "10W"
    assert data["capital"] == 100000
    assert "pnl" in data
    assert "holdings" in data
    assert "plan_rows" in data


def test_api_portfolio_detail_unknown_label_404(fresh_db_client):
    client, _ = fresh_db_client
    resp = client.get("/api/portfolio/999W")
    assert resp.status_code == 404
