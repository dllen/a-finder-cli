from typing import List, Tuple

from signal_schema import Signal


def primary_signal(signals: List[Signal]) -> Tuple[str, str]:
    if not signals:
        return "无信号", ""
    buy = [s for s in signals if s["action"] == "买入"]
    sell = [s for s in signals if s["action"] == "卖出"]
    chosen = buy[0] if buy else sell[0]
    return chosen["action"], chosen["strategy"]
