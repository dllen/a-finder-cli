from dataclasses import dataclass, field
from typing import List


@dataclass
class Stock:
    code: str
    name: str
    pe: float
    pb: float
    peg: float
    revenue_growth: float
    profit_growth: float
    roe: float
    cashflow: float
    prices: List[float]
    volumes: List[int]
    turnover: List[float] = field(default_factory=list)
    amount: List[float] = field(default_factory=list)
    pct_change: List[float] = field(default_factory=list)
