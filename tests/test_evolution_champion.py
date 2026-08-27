import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evolution import champion


def _row(date, strategy, code, score, win, pct):
    return {"date": date, "strategy": strategy, "code": code, "score": score,
            "win": win, "outcome_pct": pct}


def test_board_metrics_selects_with_quota_and_scores():
    # 两天、每天 3 个候选，top=2，A:B 配额 1:1 → 每天各取分数最高的一条
    rows = [
        _row("d1", "A", "000001", 90, 1, 0.10),
        _row("d1", "A", "000002", 80, 0, -0.05),
        _row("d1", "B", "000003", 70, 1, 0.02),
        _row("d2", "A", "000004", 95, 0, -0.03),
        _row("d2", "B", "000005", 60, 1, 0.04),
    ]
    ratios = {"A": 0.5, "B": 0.5}
    m = champion.board_metrics(rows, ratios, top=2)
    # d1 选 A(90 胜) + B(70 胜)；d2 选 A(95 负) + B(60 胜)
    assert m["n"] == 4
    assert abs(m["win_rate"] - 0.75) < 1e-9
    assert abs(m["expectancy"] - (0.10 + 0.02 - 0.03 + 0.04) / 4) < 1e-9  # d1 的 A80 被配额挤出


def test_board_metrics_skips_unjudged():
    rows = [_row("d1", "A", "1", 9, None, None), _row("d1", "A", "2", 8, 1, 0.01)]
    m = champion.board_metrics(rows, {"A": 1.0}, top=2)
    assert m["n"] == 1


def test_decide_gate_thresholds():
    champ = {"n": 200, "win_rate": 0.5, "expectancy": 0.02}
    # 样本不足 → 拒绝
    ok, _ = champion.decide(champ, {"n": 50, "win_rate": 0.9, "expectancy": 0.2})
    assert not ok
    # 胜率 +1pp → 晋升
    ok, _ = champion.decide(champ, {"n": 200, "win_rate": 0.51, "expectancy": 0.01})
    assert ok
    # 持平带内看 expectancy
    ok, _ = champion.decide(champ, {"n": 200, "win_rate": 0.503, "expectancy": 0.03})
    assert ok
    ok, _ = champion.decide(champ, {"n": 200, "win_rate": 0.503, "expectancy": 0.015})
    assert not ok
    # 更差 → 拒绝
    ok, _ = champion.decide(champ, {"n": 200, "win_rate": 0.45, "expectancy": 0.05})
    assert not ok


def test_should_rollback():
    champ = {"metrics": {"win_rate": 0.55}}
    assert not champion.should_rollback(champ, {"n": 10, "win_rate": 0.1})   # 样本不足
    assert not champion.should_rollback(champ, {"n": 30, "win_rate": 0.51})  # 跌幅 <5pp
    assert champion.should_rollback(champ, {"n": 30, "win_rate": 0.49})
    assert not champion.should_rollback({"metrics": None}, {"n": 30, "win_rate": 0.0})


def test_live_window_stats_filters_by_date():
    rows = [_row("2026-08-01", "A", "1", 0, 1, 0.1), _row("2026-08-10", "A", "2", 0, 0, -0.1)]
    m = champion.live_window_stats(rows, "2026-08-05")
    assert m["n"] == 1 and m["win_rate"] == 0.0
