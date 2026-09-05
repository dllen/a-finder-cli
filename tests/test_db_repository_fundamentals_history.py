import pytest
from datetime import datetime
from db_repository import (
    open_db,
    upsert_fundamentals_history,
    get_fundamentals_history_by_code,
    FundamentalsHistoryRow,
)


@pytest.fixture()
def db(tmp_path):
    p = str(tmp_path / "h.db")
    open_db(p).close()
    import sqlite3
    return sqlite3.connect(p)


def _row(code, year, gm=42.0, roe=18.0):
    return FundamentalsHistoryRow(
        code=code, year=year, gross_margin=gm, roe_excl=roe,
        revenue=None, net_profit_excl=None,
        report_date=f"{year}-12-31", synced_at=datetime.now().isoformat(),
    )


def test_upsert_inserts_and_updates(db):
    conn = db
    upsert_fundamentals_history(conn, [_row("600519", 2024, gm=42.0)])
    rows = get_fundamentals_history_by_code(conn, "600519")
    assert len(rows) == 1
    assert rows[0].gross_margin == pytest.approx(42.0)

    # update same (code, year)
    upsert_fundamentals_history(conn, [_row("600519", 2024, gm=43.5)])
    rows = get_fundamentals_history_by_code(conn, "600519")
    assert len(rows) == 1
    assert rows[0].gross_margin == pytest.approx(43.5)


def test_get_returns_sorted_by_year_desc(db):
    upsert_fundamentals_history(db, [
        _row("600519", 2022),
        _row("600519", 2024),
        _row("600519", 2023),
    ])
    rows = get_fundamentals_history_by_code(db, "600519")
    assert [r.year for r in rows] == [2024, 2023, 2022]


def test_get_filters_by_code(db):
    upsert_fundamentals_history(db, [
        _row("600519", 2024),
        _row("000001", 2024),
    ])
    rows = get_fundamentals_history_by_code(db, "600519")
    assert len(rows) == 1
    assert rows[0].code == "600519"
