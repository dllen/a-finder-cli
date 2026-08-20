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

    # === 新增: 行业分类 ===
    sector: str = ""
    sub_sector: str = ""

    # === 新增: 价值因子 ===
    ev_ebitda: float = 0.0

    # === 新增: 质量因子 ===
    cash_div_ratio: float = 0.0
    gross_margin_std: float = 0.0
    gross_margin: float = 0.0
    debt_ratio: float = 0.0

    # === 新增: 成长因子 ===
    revenue_cagr_3y: float = 0.0
    profit_cagr_3y: float = 0.0
    rd_expense_ratio: float = 0.0

    # === 新增: 动量/低波因子 ===
    volatility_120d: float = 0.0
    max_drawdown_1y: float = 0.0
    momentum_12m_1m: float = 0.0
    excess_momentum: float = 0.0

    # === 新增: 分红因子 ===
    dividend_yield: float = 0.0
    dividend_stability: float = 0.0
    dividend_payout_ratio: float = 0.0

    # === 新增: 历史数据 ===
    gross_margins_hist: List[float] = field(default_factory=list)
    prices_hist: List[float] = field(default_factory=list)  # 历史价格(用于计算动量)
