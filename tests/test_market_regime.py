import pytest
from market_regime import MarketRegime, detect_regime, RegimeType


def test_bull_market_detection():
    """牛市：均线多头排列 + 指数上涨"""
    # 生成上涨趋势数据
    prices = [100 + i * 0.5 for i in range(60)]
    macro = {"pe_percentile": 0.4, "m2_yoy": 0.12}
    result = detect_regime(prices, macro)
    assert result.regime == RegimeType.BULL


def test_bear_market_detection():
    """熊市：均线空头排列 + 指数下跌"""
    prices = [100 - i * 0.5 for i in range(60)]
    macro = {"pe_percentile": 0.8, "m2_yoy": 0.08}
    result = detect_regime(prices, macro)
    assert result.regime == RegimeType.BEAR


def test_sideways_market_detection():
    """震荡市：波动小 + 方向不明"""
    import math
    prices = [100 + math.sin(i * 0.3) * 3 for i in range(60)]
    macro = {"pe_percentile": 0.5, "m2_yoy": 0.10}
    result = detect_regime(prices, macro)
    assert result.regime == RegimeType.SIDEWAYS
