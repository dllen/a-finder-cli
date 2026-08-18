from typing import List

from domain_models import Stock
from market_regime import RegimeType
from strategies.base import StrategySignal


def detect(stock: Stock, regime: RegimeType) -> List[StrategySignal]:
    prices = stock.prices
    volumes = stock.volumes
    if len(prices) < 21 or len(volumes) < 21:
        return []
    if len(stock.pct_change) == len(prices):
        pct = stock.pct_change[-1]
    else:
        pct = (prices[-1] / prices[-2] - 1) * 100
    if pct <= 0:
        return []
    avg_vol = sum(volumes[-21:-1]) / 20
    if avg_vol <= 0:
        return []
    vol_ratio = volumes[-1] / avg_vol
    if vol_ratio < 1.2:
        return []
    price = prices[-1]
    stop = price * 0.95
    tp = price + 2 * (price - stop)
    score = 40 + min(60, pct * 10) + min(20, (vol_ratio - 1) * 20)
    if regime == RegimeType.BEAR:
        score *= 0.8
    return [StrategySignal(stock.code, "量价齐升", price, stop, tp, score)]
