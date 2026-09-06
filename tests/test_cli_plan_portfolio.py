import argparse
import os
import tempfile
import pytest


def test_plan_build_parser_accepts_portfolio():
    from cli_layer import build_parser
    p = build_parser()
    args = p.parse_args(["plan", "build", "--portfolio", "10W", "--capital", "100000"])
    assert args.command == "plan"
    assert args.action == "build"
    assert args.portfolio == "10W"
    assert args.capital == 100000


def test_plan_build_parser_default_portfolio():
    from cli_layer import build_parser
    args = build_parser().parse_args(["plan", "build"])
    assert args.portfolio == "default"


def test_plan_build_all_parser_accepts_strategy():
    from cli_layer import build_parser
    args = build_parser().parse_args(["plan", "build-all", "--strategy", "linyuan"])
    assert args.command == "plan"
    assert args.action == "build-all"
    assert args.strategy == "linyuan"


def test_plan_build_all_parser_accepts_tiers():
    from cli_layer import build_parser
    args = build_parser().parse_args(["plan", "build-all", "--tiers", "50000,100000,200000"])
    assert args.tiers == "50000,100000,200000"


def test_plan_build_all_parser_accepts_backfill_and_since():
    from cli_layer import build_parser
    args = build_parser().parse_args(
        ["plan", "build-all", "--backfill", "--since", "2025-01-01"]
    )
    assert args.backfill is True
    assert args.since == "2025-01-01"


def test_plan_build_runs_with_portfolio(tmp_path, capsys):
    """End-to-end: plan build --portfolio writes trade_plan rows with portfolio."""
    import sqlite3
    from db_repository import open_db
    path = str(tmp_path / "m.db")
    conn = open_db(path)
    conn.execute(
        """INSERT INTO daily_picks (date, rank, kind, code, name, strategy,
             buy, stop, target, score)
           VALUES ('2026-09-07', 1, 'test', '600519', 'M', 'test', 100, 80, 140, 2.0)"""
    )
    conn.execute(
        """INSERT INTO daily_prices (code, trade_date, open, close, high, low,
                                    volume, amount) VALUES ('600519','2026-09-07',100,100,100,100,0,0)"""
    )
    conn.commit()
    conn.close()

    from cli_layer import build_parser, run_cli
    parser = build_parser()
    args = parser.parse_args([
        "plan", "build", "--db", path,
        "--date", "2026-09-07", "--portfolio", "10W",
        "--capital", "100000",
    ])
    run_cli(args, stocks=[], scores={})

    conn = open_db(path)
    try:
        rows = conn.execute(
            "SELECT DISTINCT portfolio FROM trade_plan"
        ).fetchall()
    finally:
        conn.close()
    assert rows == [("10W",)]


def test_plan_build_all_runs_full_tiers(tmp_path, capsys):
    """End-to-end: plan build-all runs all tiers and writes trade_plan rows."""
    import sqlite3
    from db_repository import open_db
    path = str(tmp_path / "m.db")
    conn = open_db(path)
    conn.execute(
        """INSERT INTO daily_picks (date, rank, kind, code, name, strategy,
             buy, stop, target, score)
           VALUES ('2026-09-07', 1, 'test', '600519', 'M', 'test', 100, 80, 140, 2.0)"""
    )
    conn.execute(
        """INSERT INTO daily_prices (code, trade_date, open, close, high, low,
                                    volume, amount) VALUES ('600519','2026-09-07',100,100,100,100,0,0)"""
    )
    conn.commit()
    conn.close()

    from cli_layer import build_parser, run_cli
    parser = build_parser()
    args = parser.parse_args([
        "plan", "build-all", "--db", path,
        "--date", "2026-09-07", "--tiers", "50000,100000",
    ])
    run_cli(args, stocks=[], scores={})

    conn = open_db(path)
    try:
        rows = conn.execute(
            "SELECT DISTINCT portfolio FROM trade_plan ORDER BY portfolio"
        ).fetchall()
    finally:
        conn.close()
    assert rows == [("10W",), ("5W",)]
