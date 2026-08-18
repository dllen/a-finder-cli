from typing import List

from domain_models import Stock
from market_regime import RegimeType
from strategies.base import StrategySignal


def detect(stock: Stock, regime: RegimeType) -> List[StrategySignal]:
    if regime != RegimeType.BULL:
        return []
    prices = stock.prices
    volumes = stock.volumes
    if len(prices) < 61 or len(volumes) < 21:
        return []
    price = prices[-1]
    if price < max(prices[-61:-1]):
        return []
    avg_vol = sum(volumes[-21:-1]) / 20
    if avg_vol <= 0:
        return []
    vol_ratio = volumes[-1] / avg_vol
    if vol_ratio < 1.5:
        return []
    stop = price * 0.95
    tp = price + 2 * (price - stop)
    score = 50 + min(50, (vol_ratio - 1) * 50)
    return [StrategySignal(stock.code, "新高突破", price, stop, tp, score)]
