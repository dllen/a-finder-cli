import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evolution.labeling import _Series, judge


def _series(closes, lows=None):
    lows = lows or closes
    dates = [f"2026-01-{i + 1:02d}" for i in range(len(closes))]
    return _Series(code="x", dates=dates, closes=[float(c) for c in closes],
                   lows=[float(l) for l in lows])


C12 = [10.0] * 12


def test_judge_target_hit_is_win():
    closes = [10.0, 10.5, 11.0, 11.5, 12.0, 13.0] + [13.0] * 6
    s = _series(closes)
    r = judge(10.0, 9.0, 12.0, s, 0)
    assert r["exit_price"] == 12.0 and abs(r["outcome_pct"] - 0.2) < 1e-9
    assert r["exit_date"] == "2026-01-05"


def test_judge_stop_hit_is_loss():
    lows = [10.0, 10.0, 9.8, 9.4] + [9.4] * 8
    s = _series(C12, lows)
    r = judge(10.0, 9.5, 12.0, s, 0)
    assert r["exit_price"] == 9.5 and abs(r["outcome_pct"] + 0.05) < 1e-9
    assert r["exit_date"] == "2026-01-04"


def test_judge_same_day_double_touch_counts_loss():
    closes = [10.0, 10.0, 10.0, 12.0] + [12.0] * 8
    lows = [10.0, 10.0, 10.0, 9.0] + [9.0] * 8
    s = _series(closes, lows)
    r = judge(10.0, 9.5, 12.0, s, 0)  # 同日双触 → 保守记负
    assert abs(r["outcome_pct"] + 0.05) < 1e-9


def test_judge_timeout_uses_market_close():
    closes = [10.0] + [10.5] * 11
    s = _series(closes)
    r = judge(10.0, 9.0, 12.0, s, 0)
    assert r["exit_date"] == "2026-01-11"
    assert abs(r["outcome_pct"] - 0.05) < 1e-9


def test_judge_insufficient_future_returns_none():
    s = _series([10.0] * 10)
    assert judge(10.0, 9.0, 12.0, s, 0) is None  # 0+10 越界
    s12 = _series([10.5] * 12)
    assert judge(10.0, 9.0, 12.0, s12, 1) is not None


def test_judge_bad_levels_returns_none():
    s = _series(C12)
    assert judge(10.0, 10.5, 12.0, s, 0) is None  # stop >= entry
