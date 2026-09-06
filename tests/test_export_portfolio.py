import json
from pathlib import Path
import pytest
from db_repository import open_db


@pytest.fixture
def exported_site(tmp_path):
    db_path = str(tmp_path / "m.db")
    conn = open_db(db_path)
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

    site_dir = tmp_path / "site"
    from export_json import export
    export(db_path, str(site_dir))
    return db_path, site_dir


def test_summary_json_written(exported_site):
    _, site_dir = exported_site
    summary = json.loads((site_dir / "data" / "portfolio" / "summary.json").read_text())
    assert "tiers" in summary
    assert len(summary["tiers"]) == 10
    labels = {t["label"] for t in summary["tiers"]}
    assert "5W" in labels and "10W" in labels


def test_per_tier_json_written(exported_site):
    _, site_dir = exported_site
    pf_dir = site_dir / "data" / "portfolio"
    for label in ("5W", "10W", "50W"):
        assert (pf_dir / f"{label}.json").exists()


def test_per_tier_json_shape(exported_site):
    _, site_dir = exported_site
    data = json.loads((site_dir / "data" / "portfolio" / "10W.json").read_text())
    assert data["label"] == "10W"
    assert data["capital"] == 100000
    assert "pnl" in data


def test_portfolio_html_static_written(exported_site):
    _, site_dir = exported_site
    html = (site_dir / "portfolio.html").read_text()
    assert "持仓组合" in html
    # static HTML page should be generated (not absent)
    assert html.startswith("<!doctype html")
