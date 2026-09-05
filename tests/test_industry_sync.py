import sqlite3
from unittest.mock import patch

from db_repository import get_metadata_by_code, open_db

from sync_service import sync_industry


def test_sync_writes_industry_to_metadata(tmp_path):
    db = str(tmp_path / "i.db")
    conn = open_db(db)
    conn.execute("INSERT INTO hs300_constituents (code, name) VALUES ('600519', '贵州茅台')")
    conn.execute(
        "INSERT INTO hs300_metadata (code, name, industry, region) VALUES ('600519', '贵州茅台', '', '')"
    )
    conn.commit()
    conn.close()

    with patch("sync_service.fetch_industry_akshare", return_value="食品饮料"):
        result = sync_industry(db, concurrency=1, rate_limit=1000.0)

    assert result["symbols"] == 1
    assert result["rows"] == 1
    meta = get_metadata_by_code(sqlite3.connect(db), "600519")
    assert meta.industry == "食品饮料"


def test_sync_skips_empty_industry(tmp_path):
    db = str(tmp_path / "i2.db")
    conn = open_db(db)
    conn.execute("INSERT INTO hs300_constituents (code, name) VALUES ('600519', 'X')")
    conn.execute(
        "INSERT INTO hs300_metadata (code, name, industry, region) VALUES ('600519', 'X', 'OLD', '')"
    )
    conn.commit()
    conn.close()

    with patch("sync_service.fetch_industry_akshare", return_value=""):
        result = sync_industry(db, concurrency=1, rate_limit=1000.0)

    # 空行业不覆盖原值
    assert result["rows"] == 0
    meta = get_metadata_by_code(sqlite3.connect(db), "600519")
    assert meta.industry == "OLD"
