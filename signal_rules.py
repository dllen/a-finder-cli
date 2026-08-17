from dataclasses import dataclass
from enum import Enum
from typing import List

from domain_models import Stock
from indicators import macd, moving_average, rsi
from signal_schema import Signal


class ConfidenceLevel(Enum):
    STRONG = "strong"
    MEDIUM = "medium"
    WEAK = "weak"
    NONE = "none"


@dataclass
class SignalScore:
    total: float              # 总分 0-100
    indicator_count: int      # 同时满足的指标数
    spatial_score: float      # 空间距离得分 0-40
    historical_winrate: float # 历史胜率得分 0-30
    momentum_score: float     # 动量得分 0-30
    confidence: ConfidenceLevel


# 历史胜率表（简化版，实际应该从回测数据计算）
HISTORICAL_WINRATES = {
    "均线突破": 0.58,
    "动量突破": 0.52,
    "回调买入": 0.62,
    "MACD金叉": 0.55,
    "RSI超卖": 0.48,
}


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


def score_signal_strength(stock: Stock, signals: dict) -> SignalScore:
    """
    计算信号强度综合评分

    Args:
        stock: 股票对象
        signals: detect_signals 的输出，格式 {"买入": [...], "卖出": [...]}

    Returns:
        SignalScore 对象
    """
    buy_signals = signals.get("买入", [])
    sell_signals = signals.get("卖出", [])

    if not buy_signals and not sell_signals:
        return SignalScore(
            total=0, indicator_count=0, spatial_score=0,
            historical_winrate=0, momentum_score=0, confidence=ConfidenceLevel.NONE
        )

    # 1. 指标数量分 (0-50)
    indicator_count = len(buy_signals) + len(sell_signals)
    indicator_score = min(50, indicator_count * 10)

    # 2. 空间距离分 (0-40)
    prices = stock.prices
    if len(prices) >= 20:
        ma10 = sum(prices[-10:]) / 10
        ma30 = sum(prices[-30:]) / 30
        ma60 = sum(prices[-60:]) / 60
        price = prices[-1]

        # 计算价格与均线的距离
        distances = [
            abs(price / ma10 - 1),
            abs(price / ma30 - 1),
            abs(price / ma60 - 1),
        ]
        avg_distance = sum(distances) / len(distances)

        # 距离越小分越高（回踩精准）
        spatial_score = max(0, 40 - avg_distance * 400)
    else:
        spatial_score = 0

    # 3. 历史胜率分 (0-30)
    all_signals = buy_signals + sell_signals
    winrates = [HISTORICAL_WINRATES.get(s, 0.5) for s in all_signals]
    avg_winrate = sum(winrates) / len(winrates)
    historical_score = avg_winrate * 30

    # 4. 动量分 (0-30)
    if len(prices) >= 20:
        momentum_5 = prices[-1] / prices[-5] - 1
        momentum_20 = prices[-1] / prices[-20] - 1

        # 正向动量加分，负向动量减分
        momentum_score = (momentum_5 * 10 + momentum_20 * 20) * 30
        momentum_score = max(0, min(30, momentum_score + 15))
    else:
        momentum_score = 15  # 默认中等

    total = indicator_score + spatial_score + historical_score + momentum_score
    total = max(0, min(100, total))

    # 信号强度分级
    if total >= 75:
        confidence = ConfidenceLevel.STRONG
    elif total >= 50:
        confidence = ConfidenceLevel.MEDIUM
    elif total >= 25:
        confidence = ConfidenceLevel.WEAK
    else:
        confidence = ConfidenceLevel.NONE

    return SignalScore(
        total=total,
        indicator_count=indicator_count,
        spatial_score=spatial_score,
        historical_winrate=historical_score,
        momentum_score=momentum_score,
        confidence=confidence,
    )


def calculate_position(signal_score: SignalScore, regime) -> dict:
    """
    根据信号强度和市场状态计算仓位

    Args:
        signal_score: 信号评分
        regime: MarketRegime 对象

    Returns:
        dict: {position_size, stop_loss_pct, trailing_stop_pct, time_exit_days}
    """
    from market_regime import RegimeType

    # 基础仓位
    if regime.regime == RegimeType.BULL:
        base_position = 0.15
    elif regime.regime == RegimeType.BEAR:
        base_position = 0.08
    else:
        base_position = 0.10

    # 信号强度调整
    signal_multiplier = signal_score.total / 75
    signal_multiplier = min(1.5, signal_multiplier)  # 上限1.5倍

    position_size = base_position * signal_multiplier
    position_size = min(0.20, position_size)  # 最大20%

    # 止损设置
    if regime.regime == RegimeType.BULL:
        stop_loss = -0.08
        trailing_stop = 0.05
        time_exit = 30
    elif regime.regime == RegimeType.BEAR:
        stop_loss = -0.05
        trailing_stop = 0.03
        time_exit = 10
    else:
        stop_loss = -0.05
        trailing_stop = 0.03
        time_exit = 10

    return {
        "position_size": position_size,
        "stop_loss_pct": stop_loss,
        "trailing_stop_pct": trailing_stop,
        "time_exit_days": time_exit,
    }
