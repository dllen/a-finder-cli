from typing import List

from domain_models import Stock
from market_regime import RegimeType
from strategies.base import StrategySignal, kdj


def detect(stock: Stock, regime: RegimeType) -> List[StrategySignal]:
    if regime == RegimeType.BULL:
        return []
    prices = stock.prices
    if len(prices) < 20:
        return []
    ks, ds, js = kdj(prices)
    if ks[-2] > ds[-2] or ks[-1] <= ds[-1] or ks[-1] >= 20:
        return []
    price = prices[-1]
    stop = price * 0.97
    tp = price + 2 * (price - stop)
    score = 50 + min(50, (20 - ks[-1]) * 5)
    return [StrategySignal(stock.code, "KDJ低位金叉", price, stop, tp, score)]
