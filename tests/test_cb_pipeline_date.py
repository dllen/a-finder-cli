"""转债榜单日期来源的回归测试。

2026-10-27 修复：cb_pipeline 用 `dt.date.today()` 当交易日，于是
- 周末/节假日跑会写出一个不存在的交易日（库里出现 2026-10-25 = 周日）
- 不同日期跑的榜单挂到不同日期，转债页与股票页对不上

现在改为「库里最后一个交易日」，并允许 CLI 用 --date 指定。
"""

import importlib.util
from pathlib import Path

import pytest

from db_repository import open_db

_SPEC = importlib.util.spec_from_file_location(
    "cb_pipeline",
    Path(__file__).resolve().parent.parent / "cb_pipeline.py",
)
cbp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cbp)


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "cb.db")
    conn = open_db(path)
    for day in ("2026-10-23", "2026-10-26", "2026-10-27"):
        conn.execute(
            "INSERT INTO daily_prices (code, trade_date, open, close, high, low,"
            " volume, amount) VALUES (?,?,1,1,1,1,1,1)",
            ("600519", day),
        )
    conn.commit()
    yield path
    conn.close()


def test_latest_trade_date_reads_db(db):
    assert cbp._latest_trade_date(db) == "2026-10-27"


def test_latest_trade_date_missing_db_returns_none(tmp_path):
    assert cbp._latest_trade_date(str(tmp_path / "nope.db")) is None


def test_latest_trade_date_empty_table_returns_none(tmp_path):
    path = str(tmp_path / "empty.db")
    conn = open_db(path)
    conn.close()
    assert cbp._latest_trade_date(path) is None


def test_cli_accepts_date_flag():
    """--date 必须能被 argparse 接受（此前 cb-picks 没有这个参数）。"""
    from cli_layer import build_parser

    args = build_parser().parse_args(
        ["cb-picks", "--top", "20", "--db", "x.db", "--date", "2026-10-26"])
    assert args.date == "2026-10-26"


def test_cli_date_defaults_to_none():
    from cli_layer import build_parser

    args = build_parser().parse_args(["cb-picks", "--db", "x.db"])
    assert args.date is None
