from typing import List

from domain_models import Stock
from indicators import macd, moving_average, rsi
from signal_schema import Signal


def detect_signals(stock: Stock) -> List[Signal]:
    prices = stock.prices
    volumes = stock.volumes
    signals = []
    if len(prices) < 61:
        return signals
    ma20 = moving_average(prices, 20)
    ma60 = moving_average(prices, 60)
    prev_ma20 = sum(prices[-21:-1]) / 20
    prev_ma60 = sum(prices[-61:-1]) / 60
    if ma20 > ma60 and prev_ma20 <= prev_ma60:
        signals.append({"action": "买入", "strategy": "均线突破"})
    if ma20 < ma60 and prev_ma20 >= prev_ma60:
        signals.append({"action": "卖出", "strategy": "均线跌破"})
    high_60 = max(prices[-60:])
    avg_volume_20 = sum(volumes[-20:]) / 20
    if prices[-1] >= high_60 and volumes[-1] > avg_volume_20 * 1.5:
        signals.append({"action": "买入", "strategy": "动量突破"})
    if ma20 > ma60 and abs(prices[-1] - ma20) / ma20 <= 0.02:
        signals.append({"action": "买入", "strategy": "回调买入"})
    macd_line, signal_line = macd(prices)
    if len(macd_line) >= 2 and len(signal_line) >= 2:
        if macd_line[-1] > signal_line[-1] and macd_line[-2] <= signal_line[-2]:
            signals.append({"action": "买入", "strategy": "MACD金叉"})
        if macd_line[-1] < signal_line[-1] and macd_line[-2] >= signal_line[-2]:
            signals.append({"action": "卖出", "strategy": "MACD死叉"})
    rsi_value = rsi(prices)
    if rsi_value is not None and rsi_value < 30:
        signals.append({"action": "买入", "strategy": "RSI超卖"})
    return signals
