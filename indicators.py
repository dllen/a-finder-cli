import math
from typing import List, Optional, Tuple


def normalize(values: List[float], higher_is_better: bool) -> List[float]:
    low = min(values)
    high = max(values)
    if math.isclose(high, low):
        return [50.0 for _ in values]
    result = []
    for value in values:
        if higher_is_better:
            score = (value - low) / (high - low) * 100
        else:
            score = (high - value) / (high - low) * 100
        result.append(score)
    return result


def moving_average(prices: List[float], window: int) -> float:
    return sum(prices[-window:]) / window


def moving_average_slice(prices: List[float], window: int, end_index: Optional[int] = None) -> float:
    if end_index is None:
        end_index = len(prices)
    return sum(prices[end_index - window : end_index]) / window


def ema(series: List[float], period: int) -> List[float]:
    if not series:
        return []
    k = 2 / (period + 1)
    values = [series[0]]
    for price in series[1:]:
        values.append(price * k + values[-1] * (1 - k))
    return values


def macd(prices: List[float]) -> Tuple[List[float], List[float]]:
    ema12 = ema(prices, 12)
    ema26 = ema(prices, 26)
    macd_line = [a - b for a, b in zip(ema12, ema26)]
    signal_line = ema(macd_line, 9)
    return macd_line, signal_line


def rsi(prices: List[float], period: int = 14) -> Optional[float]:
    if len(prices) <= period:
        return None
    gains = []
    losses = []
    for i in range(1, period + 1):
        change = prices[i] - prices[i - 1]
        gains.append(max(change, 0))
        losses.append(abs(min(change, 0)))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    for i in range(period + 1, len(prices)):
        change = prices[i] - prices[i - 1]
        gain = max(change, 0)
        loss = abs(min(change, 0))
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if math.isclose(avg_loss, 0):
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))
