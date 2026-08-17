from dataclasses import dataclass
from enum import Enum
from typing import List, Dict, Optional


class RegimeType(Enum):
    BULL = "bull"
    BEAR = "bear"
    SIDEWAYS = "sideways"


@dataclass
class MarketRegime:
    regime: RegimeType
    confidence: float  # 0-1
    tech_score: float  # 技术指标维度得分 -1~1
    index_score: float  # 宽基指数维度得分 -1~1
    fundamental_score: float  # 基本面维度得分 -1~1


def _calc_tech_score(prices: List[float], lookback: int = 60) -> float:
    """技术指标维度（权重40%）"""
    if len(prices) < lookback:
        lookback = len(prices)
    recent = prices[-lookback:]

    # MA200方向（需要更多历史数据）
    ma20 = sum(prices[-20:]) / 20
    ma60 = sum(prices[-60:]) / 60
    ma20_prev = sum(prices[-21:-1]) / 20
    ma60_prev = sum(prices[-61:-1]) / 60

    score = 0.0
    # 均线方向
    if ma20 > ma60:
        score += 0.3
    else:
        score -= 0.3
    if ma20 > ma20_prev:
        score += 0.2
    else:
        score -= 0.2
    if ma60 > ma60_prev:
        score += 0.2
    else:
        score -= 0.2

    # 波动率（20日 HV）
    high_20 = max(prices[-20:])
    low_20 = min(prices[-20:])
    hv = (high_20 - low_20) / low_20
    if hv < 0.15:
        score += 0.3
    elif hv > 0.30:
        score -= 0.3

    return max(-1.0, min(1.0, score))


def _calc_index_score(prices: List[float]) -> float:
    """宽基指数维度（权重35%）"""
    if len(prices) < 20:
        return 0.0

    price = prices[-1]
    ma20 = sum(prices[-20:]) / 20

    score = 0.0
    # 20日均线方向
    if price > ma20:
        score += 0.4
    else:
        score -= 0.4

    # 短期动量
    momentum_5 = price / prices[-5] - 1
    if momentum_5 > 0.02:
        score += 0.3
    elif momentum_5 < -0.02:
        score -= 0.3

    # 20日涨幅
    momentum_20 = price / prices[-20] - 1
    if momentum_20 > 0.05:
        score += 0.3
    elif momentum_20 < -0.05:
        score -= 0.3

    return max(-1.0, min(1.0, score))


def _calc_fundamental_score(macro: Dict[str, float]) -> float:
    """基本面维度（权重25%）"""
    score = 0.0

    # 估值分位数
    pe_pct = macro.get("pe_percentile", 0.5)
    if pe_pct < 0.3:
        score += 0.4  # 低估值
    elif pe_pct > 0.7:
        score -= 0.4  # 高估值

    # 流动性
    m2_yoy = macro.get("m2_yoy", 0.10)
    if m2_yoy > 0.10:
        score += 0.3
    elif m2_yoy < 0.08:
        score -= 0.3

    return max(-1.0, min(1.0, score))


def detect_regime(
    prices: List[float],
    macro: Dict[str, float],
    lookback_days: int = 60,
) -> MarketRegime:
    """
    综合判断市场状态

    Args:
        prices: 价格序列（通常是沪深300指数）
        macro: 宏观指标 dict，包含 pe_percentile, m2_yoy
        lookback_days: 回顾天数

    Returns:
        MarketRegime 对象
    """
    if len(prices) < 20:
        return MarketRegime(
            regime=RegimeType.SIDEWAYS,
            confidence=0.0,
            tech_score=0.0,
            index_score=0.0,
            fundamental_score=0.0,
        )

    tech = _calc_tech_score(prices, lookback_days)
    index_score = _calc_index_score(prices)
    fundamental = _calc_fundamental_score(macro)

    # 加权综合得分
    weighted = tech * 0.4 + index_score * 0.35 + fundamental * 0.25

    if weighted > 0.3:
        regime = RegimeType.BULL
    elif weighted < -0.3:
        regime = RegimeType.BEAR
    else:
        regime = RegimeType.SIDEWAYS

    confidence = abs(weighted)

    return MarketRegime(
        regime=regime,
        confidence=confidence,
        tech_score=tech,
        index_score=index_score,
        fundamental_score=fundamental,
    )
