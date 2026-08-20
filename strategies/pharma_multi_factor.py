from dataclasses import dataclass
from typing import List
from datetime import date
from domain_models import Stock
from strategies.multi_factor_base import (
    MultiFactorConfig, MultiFactorBase, FactorConfig,
    FactorDirection, SelectionResult
)

# 因子配置
PHARMA_FACTORS = [
    # 价值(30%): PE行业分位, PB行业分位, EV/EBITDA
    FactorConfig('pe_rank', 0.10, FactorDirection.LOWER_IS_BETTER, lambda s: s.pe),
    FactorConfig('pb_rank', 0.10, FactorDirection.LOWER_IS_BETTER, lambda s: s.pb),
    FactorConfig('ev_ebitda', 0.10, FactorDirection.LOWER_IS_BETTER, lambda s: s.ev_ebitda),
    # 质量(30%): ROE, 现金流/净利润, 毛利率稳定性
    FactorConfig('roe', 0.12, FactorDirection.HIGHER_IS_BETTER, lambda s: s.roe),
    FactorConfig('cash_div_ratio', 0.10, FactorDirection.HIGHER_IS_BETTER, lambda s: s.cash_div_ratio),
    FactorConfig('gross_margin_std', 0.08, FactorDirection.LOWER_IS_BETTER, lambda s: s.gross_margin_std),
    # 成长(20%): 营收CAGR, 利润CAGR, 研发费用率
    FactorConfig('revenue_cagr', 0.08, FactorDirection.HIGHER_IS_BETTER, lambda s: s.revenue_cagr_3y),
    FactorConfig('profit_cagr', 0.07, FactorDirection.HIGHER_IS_BETTER, lambda s: s.profit_cagr_3y),
    FactorConfig('rd_expense', 0.05, FactorDirection.HIGHER_IS_BETTER, lambda s: s.rd_expense_ratio),
    # 动量(10%): 12m-1m, 超额动量
    FactorConfig('momentum', 0.06, FactorDirection.HIGHER_IS_BETTER, lambda s: s.momentum_12m_1m),
    FactorConfig('excess_momentum', 0.04, FactorDirection.HIGHER_IS_BETTER, lambda s: s.excess_momentum),
    # 低波(10%): 波动率, 最大回撤
    FactorConfig('volatility', 0.05, FactorDirection.LOWER_IS_BETTER, lambda s: s.volatility_120d),
    FactorConfig('max_dd', 0.05, FactorDirection.LOWER_IS_BETTER, lambda s: s.max_drawdown_1y),
]


@dataclass
class PharmaMultiFactorConfig(MultiFactorConfig):
    name: str = "医药多因子价值+质量"
    sector: str = "医药生物"
    top_n: int = 25
    max_weight: float = 0.05
    rebalance_freq: str = "monthly"


class PharmaMultiFactorStrategy(MultiFactorBase):
    def __init__(self, config: PharmaMultiFactorConfig = None):
        config = config or PharmaMultiFactorConfig(factors=PHARMA_FACTORS)
        super().__init__(config)

    def _filter_candidates(self, stocks: List[Stock]) -> List[Stock]:
        # 过滤: 行业 + 1年数据(365天)
        return [s for s in stocks
                if s.sector == self.config.sector and s.prices and len(s.prices) >= 365]

    def select(self, date: date, candidates: List[Stock]) -> SelectionResult:
        result = super().select(date, candidates)
        result.rebalance_reason = f"月度调仓, 候选{len(candidates)}只, 持仓{len(result.positions)}只"
        return result
