from typing import List

from domain_models import Stock
from market_regime import RegimeType
from strategies.base import StrategySignal


def detect(stock: Stock, regime: RegimeType) -> List[StrategySignal]:
    if regime != RegimeType.BULL:
        return []
    prices = stock.prices
    volumes = stock.volumes
    if len(prices) < 25 or len(volumes) < 25:
        return []
    box = prices[-25:-1]
    box_high = max(box)
    box_low = min(box)
    price = prices[-1]
    if box_low <= 0 or (box_high - box_low) / box_low > 0.15:
        return []
    if price <= box_high:
        return []
    avg_vol = sum(volumes[-25:-1]) / 24
    if avg_vol <= 0:
        return []
    vol_ratio = volumes[-1] / avg_vol
    if vol_ratio < 1.5:
        return []
    stop = box_high * 0.97
    tp = price + 2 * (price - stop)
    score = 50 + min(50, (vol_ratio - 1) * 50)
    return [StrategySignal(stock.code, "箱体突破", price, stop, tp, score)]
