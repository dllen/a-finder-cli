"""归因：pick_outcomes → 每策略胜负统计。纯函数，输入为行 dict 列表。"""

from dataclasses import dataclass
from typing import Dict, List


@dataclass
class StrategyStats:
    strategy: str
    n: int
    wins: int
    win_rate: float
    expectancy: float  # 平均 outcome_pct（小数，0.05 = +5%）


def attribute(rows: List[Dict]) -> Dict[str, StrategyStats]:
    """按策略聚合已判定样本（win 为 None 的行不计入）。"""
    buckets: Dict[str, List[float]] = {}
    for r in rows:
        if r.get("win") is None or r.get("outcome_pct") is None:
            continue
        buckets.setdefault(r["strategy"], []).append(float(r["outcome_pct"]))
    out: Dict[str, StrategyStats] = {}
    for strategy, rets in buckets.items():
        n = len(rets)
        wins = sum(1 for x in rets if x > 0)
        out[strategy] = StrategyStats(
            strategy=strategy, n=n, wins=wins,
            win_rate=round(wins / n, 6), expectancy=round(sum(rets) / n, 6),
        )
    return out
