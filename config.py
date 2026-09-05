from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SyncConfig:
    mode: str
    limit: Optional[int]
    sleep_seconds: float = 0.05


# Daily execution plan defaults (used by plan_builder sanity gate).
RR_TARGET = 2.0        # take-profit / stop-loss ratio
MAX_SINGLE = 0.15      # max single-position weight
MAX_TOTAL = 0.95       # max total portfolio weight
SLIPPAGE = 0.001       # paper-trade fill slippage (0.1%)
STOP_ATR_MULT = 2.0    # ATR multiple for stop

# Initial-capital tiers for the daily plan (元). Default active capital = 10W.
# 5W → 50W every 5W（10 档），CLI/页面/plan_builder 共用同一份。
CAPITAL_TIERS = [50000, 100000, 150000, 200000, 250000, 300000, 350000, 400000, 450000, 500000]
DEFAULT_CAPITAL = 100000
