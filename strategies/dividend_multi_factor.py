from dataclasses import dataclass, field
from typing import List
from domain_models import Stock
from strategies.multi_factor_base import (
    MultiFactorConfig, MultiFactorBase, FactorConfig,
    FactorDirection
)

# 因子配置
DIVIDEND_FACTORS = [
    # 股息(40%): 股息率, 分红稳定性
    FactorConfig('dividend_yield', 0.25, FactorDirection.HIGHER_IS_BETTER, lambda s: s.dividend_yield),
    FactorConfig('dividend_stability', 0.15, FactorDirection.HIGHER_IS_BETTER, lambda s: s.dividend_stability),
    # 低波(25%): 波动率, 最大回撤
    FactorConfig('volatility', 0.15, FactorDirection.LOWER_IS_BETTER, lambda s: s.volatility_120d),
    FactorConfig('max_dd', 0.10, FactorDirection.LOWER_IS_BETTER, lambda s: s.max_drawdown_1y),
    # 估值(20%): PE, PB, 股息率溢价
    FactorConfig('pe', 0.10, FactorDirection.LOWER_IS_BETTER, lambda s: s.pe),
    FactorConfig('pb', 0.05, FactorDirection.LOWER_IS_BETTER, lambda s: s.pb),
    FactorConfig('cash_div_ratio', 0.05, FactorDirection.HIGHER_IS_BETTER, lambda s: s.cash_div_ratio),
    # 质量(15%): ROE, 资产负债率, 现金流
    FactorConfig('roe', 0.07, FactorDirection.HIGHER_IS_BETTER, lambda s: s.roe),
    FactorConfig('debt_ratio', 0.04, FactorDirection.LOWER_IS_BETTER, lambda s: s.debt_ratio),
    FactorConfig('cashflow', 0.04, FactorDirection.HIGHER_IS_BETTER, lambda s: s.cashflow),
]


@dataclass
class DividendMultiFactorConfig(MultiFactorConfig):
    name: str = "高股息+低波防御"
    sectors: List[str] = field(default_factory=lambda: [
        "银行", "保险", "公用事业", "中药", "可选消费"
    ])
    top_n: int = 40
    max_weight: float = 0.04
    rebalance_freq: str = "quarterly"


class DividendMultiFactorStrategy(MultiFactorBase):
    def __init__(self, config: DividendMultiFactorConfig = None):
        config = config or DividendMultiFactorConfig(factors=DIVIDEND_FACTORS)
        super().__init__(config)

    def _filter_candidates(self, stocks: List[Stock]) -> List[Stock]:
        filtered = []
        for s in stocks:
            # 过滤: 股息率为0/负
            if s.dividend_yield <= 0:
                continue
            # 过滤: 资产负债率过高
            if s.debt_ratio > 0.9:
                continue
            # 可扩展: 3年净利润为负、分红率>100%
            filtered.append(s)
        return filtered
