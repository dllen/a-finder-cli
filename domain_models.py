from dataclasses import dataclass
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
