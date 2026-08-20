import pytest
from strategies.multi_factor_base import MultiFactorConfig, MultiFactorBase, FactorConfig, FactorDirection
from domain_models import Stock
from indicators import z_score_normalize


def test_z_score_normalization():
    values = [10, 20, 30, 40, 50]
    result = z_score_normalize(values, higher_is_better=True)
    assert len(result) == 5
    assert min(result) >= 0
    assert max(result) <= 100


def test_multi_factor_scoring():
    config = MultiFactorConfig(
        name="test",
        factors=[
            FactorConfig("pe", 0.5, FactorDirection.LOWER_IS_BETTER, lambda s: s.pe),
            FactorConfig("roe", 0.5, FactorDirection.HIGHER_IS_BETTER, lambda s: s.roe),
        ]
    )
    stocks = [
        Stock(code="1", name="A", pe=10, pb=1, peg=1, revenue_growth=0.1, profit_growth=0.1, roe=0.10, cashflow=0.1, prices=[10]*120, volumes=[100]*120),
        Stock(code="2", name="B", pe=10, pb=2, peg=2, revenue_growth=0.2, profit_growth=0.2, roe=0.20, cashflow=0.2, prices=[20]*120, volumes=[100]*120),
    ]
    strategy = MultiFactorBase(config)
    result = strategy.select(None, stocks)
    assert len(result.positions) == 2
    assert result.positions[0].score > result.positions[1].score


def test_empty_candidates():
    config = MultiFactorConfig(name="test", factors=[], top_n=10)
    strategy = MultiFactorBase(config)
    result = strategy.select(None, [])
    assert len(result.positions) == 0
