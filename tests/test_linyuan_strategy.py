import pytest
from db_repository import FundamentalsHistoryRow
from strategies.linyuan_multi_factor import (
    LinYuanConfig, passes_linyuan_filter,
)

CFG = LinYuanConfig()


def _h(gm, roe, years):
    return [FundamentalsHistoryRow(
        code="X", year=y, gross_margin=gm, roe_excl=roe,
        report_date=f"{y}-12-31", synced_at="",
    ) for y in years]


def test_passes_with_5_consecutive_years():
    history = _h(0.50, 0.20, [2020, 2021, 2022, 2023, 2024])
    assert passes_linyuan_filter("医药生物", history, CFG) is True


def test_fails_when_only_4_years():
    history = _h(0.50, 0.20, [2020, 2021, 2022, 2023])
    assert passes_linyuan_filter("医药生物", history, CFG) is False


def test_fails_with_year_gap():
    # 缺 2022
    history = _h(0.50, 0.20, [2020, 2021, 2023, 2024])
    history.append(FundamentalsHistoryRow(
        code="X", year=2022, gross_margin=0, roe_excl=0,
        report_date="2022-12-31", synced_at="",
    ))
    assert passes_linyuan_filter("医药生物", history, CFG) is False


def test_fails_when_margin_below_threshold():
    history = _h(0.39, 0.20, [2020, 2021, 2022, 2023, 2024])
    assert passes_linyuan_filter("医药生物", history, CFG) is False


def test_fails_when_roe_below_threshold():
    history = _h(0.50, 0.14, [2020, 2021, 2022, 2023, 2024])
    assert passes_linyuan_filter("医药生物", history, CFG) is False


def test_fails_when_sector_not_in_whitelist():
    history = _h(0.50, 0.20, [2020, 2021, 2022, 2023, 2024])
    assert passes_linyuan_filter("房地产", history, CFG) is False


@pytest.mark.parametrize("sector", [
    "医药生物", "中药", "食品饮料", "机械设备", "电力设备", "汽车整车",
])
def test_all_whitelisted_sectors_pass(sector):
    history = _h(0.50, 0.20, [2020, 2021, 2022, 2023, 2024])
    assert passes_linyuan_filter(sector, history, CFG) is True


# ---------------------------------------------------------------------------
# LinYuanRunner tests
# ---------------------------------------------------------------------------

import sqlite3
from datetime import date as _date
from db_repository import open_db
from strategies.linyuan_multi_factor import LinYuanRunner, LinYuanConfig
from strategies.multi_factor_base import TargetPosition


@pytest.fixture()
def lin_db(tmp_path):
    db = str(tmp_path / "lin.db")
    conn = open_db(db)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS hs300_metadata ("
        "  code TEXT PRIMARY KEY, name TEXT, industry TEXT, region TEXT"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS fundamentals_history ("
        "  code TEXT, year INTEGER, gross_margin REAL, roe_excl REAL, "
        "  report_date TEXT, synced_at TEXT"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS hs300_constituents ("
        "  code TEXT PRIMARY KEY, name TEXT, exchange TEXT, updated_at TEXT"
        ")"
    )
    # 元数据
    conn.executemany(
        "INSERT INTO hs300_metadata (code, name, industry, region) VALUES (?, ?, ?, ?)",
        [
            ("600519", "贵州茅台", "食品饮料", "贵州"),
            ("000538", "云南白药", "中药", "云南"),
            ("600276", "恒瑞医药", "医药生物", "江苏"),
            ("000002", "万科A", "房地产", "深圳"),
        ],
    )
    # constituents 表也需要填充（LinYuanRunner 先查这个表）
    conn.executemany(
        "INSERT INTO hs300_constituents (code, name, exchange, updated_at) VALUES (?, ?, ?, ?)",
        [
            ("600519", "贵州茅台", "SH", "2025-01-01"),
            ("000538", "云南白药", "SZ", "2025-01-01"),
            ("600276", "恒瑞医药", "SH", "2025-01-01"),
            ("000002", "万科A", "SZ", "2025-01-01"),
        ],
    )
    # 5 年合格
    rows = []
    for code in ["600519", "000538", "600276"]:
        for y in [2020, 2021, 2022, 2023, 2024]:
            rows.append((code, y, 50.0, 20.0, f"{y}-12-31", ""))
    conn.executemany(
        "INSERT INTO fundamentals_history (code, year, gross_margin, roe_excl, report_date, synced_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    # 000002 只有 3 年
    rows2 = [(("000002", y, 50.0, 20.0, f"{y}-12-31", "")) for y in [2022, 2023, 2024]]
    conn.executemany(
        "INSERT INTO fundamentals_history (code, year, gross_margin, roe_excl, report_date, synced_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows2,
    )
    conn.commit()
    conn.close()
    return db


def test_runner_returns_only_passing_stocks(lin_db):
    runner = LinYuanRunner()
    result = runner.run(db_path=lin_db)
    codes = sorted(p.code for p in result.positions)
    assert codes == ["000538", "600276", "600519"]


def test_runner_assigns_equal_weights(lin_db):
    runner = LinYuanRunner()
    result = runner.run(db_path=lin_db)
    weights = {p.weight for p in result.positions}
    assert len(weights) == 1
    w = next(iter(weights))
    assert abs(w - 1 / 3) < 1e-6


def test_runner_top_n_caps_output(lin_db):
    runner = LinYuanRunner(top_n=2)
    result = runner.run(db_path=lin_db)
    assert len(result.positions) == 2


def test_runner_empty_when_no_history(tmp_path):
    db = str(tmp_path / "empty.db")
    open_db(db).close()
    runner = LinYuanRunner()
    result = runner.run(db_path=db)
    assert result.positions == []
