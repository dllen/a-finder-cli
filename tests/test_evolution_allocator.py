import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from candidate_rules import DEFAULT_STRATEGY_RATIOS
from evolution.allocator import (EVOLVE_BUDGET, MAX_RATIO, MIN_RATIO, SUPPRESS_L1,
                                 Challenger, NoChange, next_config)
from evolution.attribution import StrategyStats, attribute


def _s(n, wr, exp):
    return StrategyStats("x", n, int(n * wr), wr, exp)


STATS_GOOD_A = {"A": _s(60, 0.60, 0.050), "B": _s(60, 0.50, 0.010)}
UNIVERSE = ["A", "B", "C", "D", "E"]
RATIOS0 = {**DEFAULT_STRATEGY_RATIOS, "A": 0.10, "B": 0.10, "C": 0.10}
ACTIVE0 = ["A", "B", "C"]


def test_attribute_counts_judged_only():
    rows = [
        {"strategy": "A", "win": 1, "outcome_pct": 0.1},
        {"strategy": "A", "win": 0, "outcome_pct": -0.05},
        {"strategy": "A", "win": None, "outcome_pct": None},
        {"strategy": "B", "win": 1, "outcome_pct": 0.02},
    ]
    stats = attribute(rows)
    assert stats["A"].n == 2 and stats["A"].win_rate == 0.5
    assert abs(stats["A"].expectancy - 0.025) < 1e-9
    assert "B" in stats


def test_dead_strategy_kicked_out():
    stats = dict(STATS_GOOD_A)
    stats["C"] = _s(40, 0.30, 0.02)  # 胜率过低
    stats["D"] = _s(40, 0.50, -0.01)  # expectancy 为负
    res = next_config(stats, RATIOS0, ACTIVE0, UNIVERSE)
    assert isinstance(res, Challenger)
    assert "C" not in res.active and "D" not in res.active
    assert res.ratios["C"] == 0.0 and res.ratios["D"] == 0.0


def test_quota_proportional_clamped_and_budgeted():
    stats = {"A": _s(60, 0.60, 0.200), "B": _s(60, 0.50, 0.010)}
    res = next_config(stats, RATIOS0, ACTIVE0, UNIVERSE)
    assert isinstance(res, Challenger)
    assert res.ratios["A"] == MAX_RATIO  # 高 expectancy 被帽住
    assert res.ratios["B"] >= MIN_RATIO
    sig_total = sum(res.ratios[k] for k in UNIVERSE)
    assert abs(sig_total - EVOLVE_BUDGET) < 0.02
    # 均线基线原样保留
    for k, v in DEFAULT_STRATEGY_RATIOS.items():
        assert res.ratios[k] == v


def test_trial_grace_for_small_sample():
    stats = dict(STATS_GOOD_A)
    stats["B"] = _s(10, 0.60, 0.02)  # 样本不足 → 宽限试跑
    res = next_config(stats, RATIOS0, ACTIVE0, UNIVERSE)
    assert "B" in res.active
    assert res.ratios["B"] <= EVOLVE_BUDGET / 5


def test_no_change_suppression_band():
    stats = {"A": _s(60, 0.55, 0.12), "B": _s(60, 0.52, 0.11), "C": _s(60, 0.50, 0.10)}
    # 当前配置已接近按比例分配 → L1 变化在带内
    near = {**DEFAULT_STRATEGY_RATIOS, "A": 0.104, "B": 0.10, "C": 0.096}
    res = next_config(stats, near, ["A", "B", "C"], UNIVERSE)
    assert isinstance(res, NoChange)


def test_dead_off_strategy_with_good_stats_revived():
    stats = {"A": _s(60, 0.60, 0.05), "B": _s(60, 0.5, 0.01), "E": _s(50, 0.70, 0.08)}
    res = next_config(stats, RATIOS0, ACTIVE0, UNIVERSE)
    assert "E" in res.active and res.ratios["E"] > 0
