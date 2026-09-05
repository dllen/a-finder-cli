import argparse
from cli_layer import build_parser


def test_linyuan_picks_parser():
    parser = build_parser()
    sub_actions = next(
        a for a in parser._actions
        if hasattr(a, "choices") and isinstance(a.choices, dict) and "linyuan-picks" in a.choices
    )
    linyuan = sub_actions.choices["linyuan-picks"]
    flags = {a.dest for a in linyuan._actions}
    assert "top" in flags
    assert "db" in flags
    assert "dry_run" in flags


def test_sync_fundamentals_history_parser():
    parser = build_parser()
    sub_actions = next(
        a for a in parser._actions
        if hasattr(a, "choices") and isinstance(a.choices, dict)
        and "sync-fundamentals-history" in a.choices
    )
    cmd = sub_actions.choices["sync-fundamentals-history"]
    flags = {a.dest for a in cmd._actions}
    assert {"db", "concurrency", "rate", "retries", "backoff"} <= flags


def test_sync_industry_parser():
    parser = build_parser()
    sub_actions = next(
        a for a in parser._actions
        if hasattr(a, "choices") and isinstance(a.choices, dict)
        and "sync-industry" in a.choices
    )
    cmd = sub_actions.choices["sync-industry"]
    flags = {a.dest for a in cmd._actions}
    assert {"db", "concurrency", "rate", "retries", "backoff"} <= flags
