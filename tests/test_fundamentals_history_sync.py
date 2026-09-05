import sqlite3
from unittest.mock import patch
from db_repository import open_db, get_fundamentals_history_by_code
from sync_service import sync_fundamentals_history


def test_sync_writes_rows_to_db(tmp_path):
    db = str(tmp_path / "s.db")
    conn = open_db(db)
    conn.execute("INSERT INTO hs300_constituents (code, name) VALUES ('600519', '贵州茅台')")
    conn.commit()
    conn.close()

    fake_rows = []  # populated by mock
    from db_repository import FundamentalsHistoryRow
    from datetime import datetime
    for y in [2020, 2021, 2022, 2023, 2024]:
        fake_rows.append(FundamentalsHistoryRow(
            code="600519", year=y, gross_margin=45.0, roe_excl=18.0,
            report_date=f"{y}-12-31", synced_at=datetime.now().isoformat(),
        ))

    with patch("sync_service.fetch_fundamentals_history_akshare", return_value=fake_rows):
        result = sync_fundamentals_history(db, concurrency=1, rate_limit=1000.0)

    assert result["symbols"] == 1
    assert result["rows"] == 5
    rows = get_fundamentals_history_by_code(sqlite3.connect(db), "600519")
    assert len(rows) == 5


def test_sync_skips_when_no_codes(tmp_path):
    db = str(tmp_path / "empty.db")
    open_db(db).close()
    from sync_service import FetchError
    import pytest
    with pytest.raises(FetchError):
        sync_fundamentals_history(db)
