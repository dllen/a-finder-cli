"""归因：pick_outcomes → 每策略胜负统计。纯函数，输入为行 dict 列表。"""

from dataclasses import dataclass
from typing import Dict, List


@dataclass
class StrategyStats:
    strategy: str
    n: int
    win_rate: float
    expectancy: float  # 平均 outcome_pct（小数，0.05 = +5%）


def attribute(rows: List[Dict]) -> Dict[str, StrategyStats]:
    """按策略聚合已判定样本（win 为 None 的行不计入）。"""
    buckets: Dict[str, List[Dict]] = {}
    for r in rows:
        if r.get("win") is None or r.get("outcome_pct") is None:
            continue
        buckets.setdefault(r["strategy"], []).append(r)
    out: Dict[str, StrategyStats] = {}
    for strategy, rs in buckets.items():
        n = len(rs)
        rets = [float(r["outcome_pct"]) for r in rs]
        wins = sum(int(r["win"]) for r in rs)
        out[strategy] = StrategyStats(
            strategy=strategy, n=n,
            win_rate=round(wins / n, 6), expectancy=round(sum(rets) / n, 6),
        )
    return out
