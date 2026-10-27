"""本地兜底判定脚本（scripts/needs_fallback.py）的测试。"""

import importlib.util
import json
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "needs_fallback",
    Path(__file__).resolve().parent.parent / "scripts" / "needs_fallback.py",
)
nf = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(nf)


def _write(tmp_path, dates):
    p = tmp_path / "dates.json"
    p.write_text(json.dumps({"dates": dates}), encoding="utf-8")
    return str(p)


def test_up_to_date_needs_no_fallback(tmp_path):
    path = _write(tmp_path, ["2026-10-27", "2026-10-26"])
    assert nf.main(["--dates-json", path, "--today", "2026-10-27"]) == 0


def test_stale_needs_fallback(tmp_path):
    path = _write(tmp_path, ["2026-10-23", "2026-10-22"])
    assert nf.main(["--dates-json", path, "--today", "2026-10-27"]) == 1


def test_empty_dates_needs_fallback(tmp_path):
    path = _write(tmp_path, [])
    assert nf.main(["--dates-json", path, "--today", "2026-10-27"]) == 1


def test_missing_file_returns_error_code(tmp_path):
    assert nf.main(["--dates-json", str(tmp_path / "nope.json")]) == 2


def test_weekend_expects_last_trading_day(tmp_path):
    """周日跑 → 期望值是上周五，线上周五数据即算最新。"""
    path = _write(tmp_path, ["2026-10-23"])
    # 2026-10-25 是周日
    assert nf.main(["--dates-json", path, "--today", "2026-10-25"]) == 0


def test_decide_messages():
    need, reason = nf.decide("2026-10-23", "2026-10-27")
    assert need and "落后" in reason
    need, reason = nf.decide("2026-10-27", "2026-10-27")
    assert not need and "最新" in reason
    need, reason = nf.decide(None, "2026-10-27")
    assert need and "为空" in reason
