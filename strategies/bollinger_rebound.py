from typing import List

from domain_models import Stock
from indicators import rsi
from market_regime import RegimeType
from strategies.base import StrategySignal, bollinger


def detect(stock: Stock, regime: RegimeType) -> List[StrategySignal]:
    if regime == RegimeType.BULL:
        return []
    prices = stock.prices
    if len(prices) < 22:
        return []
    mid, upper, lower = bollinger(prices)
    r = rsi(prices)
    if r is None or r >= 30:
        return []
    price = prices[-1]
    if prices[-2] >= lower or price < lower:
        return []
    stop = lower * 0.98
    tp = mid if mid > price else price + 2 * (price - stop)
    score = 50 + min(50, (price - lower) / lower * 1000)
    return [StrategySignal(stock.code, "布林超卖反弹", price, stop, tp, score)]
