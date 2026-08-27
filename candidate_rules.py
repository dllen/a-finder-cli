from dataclasses import dataclass
from typing import Dict, List, Optional

from candidate_schema import Candidate
from domain_models import Stock
from indicators import moving_average_slice, rsi, moving_average
from market_regime import MarketRegime, RegimeType

DEFAULT_STRATEGY_RATIOS: Dict[str, float] = {
    "多均线突破": 0.75,
    "多均线回踩": 0.25,
    "多均线趋势": 0.0,
}


@dataclass
class CandidateConfig:
    momentum_20_min: float = 0.075
    volatility_20_max: float = 0.45
    ma10_distance_max: float = 0.09
    breakout_volume_ratio_min: float = 1.2
    trend_follow_momentum_min: float = 0.02
    score_distance200_weight: float = 0.8
    score_distance50_weight: float = 0.3
    alignment_depth: int = 2
    slope200_weight: float = 3.0
    slope100_weight: float = 2.0
    momentum20_weight: float = 200.0
    momentum10_weight: float = 50.0
    volume_bonus_weight: float = 12.0


DEFAULT_CANDIDATE_CONFIG = CandidateConfig()


def ma_strategy_candidates(
    stocks: List[Stock],
    config: Optional[CandidateConfig] = None,
) -> List[Candidate]:
    config = config or DEFAULT_CANDIDATE_CONFIG
    candidates = []
    for stock in stocks:
        prices = stock.prices
        volumes = stock.volumes
        if len(prices) < 220:
            continue
        ma10 = moving_average_slice(prices, 10)
        ma30 = moving_average_slice(prices, 30)
        ma50 = moving_average_slice(prices, 50)
        ma100 = moving_average_slice(prices, 100)
        ma200 = moving_average_slice(prices, 200)
        ma10_prev = moving_average_slice(prices, 10, len(prices) - 5)
        ma30_prev = moving_average_slice(prices, 30, len(prices) - 5)
        ma200_prev = moving_average_slice(prices, 200, len(prices) - 20)
        ma50_prev = moving_average_slice(prices, 50, len(prices) - 5)
        ma100_prev = moving_average_slice(prices, 100, len(prices) - 5)
        price = prices[-1]
        recent_momentum_10 = price / prices[-10] - 1
        recent_momentum_20 = price / prices[-20] - 1
        volatility_20 = max(prices[-20:]) / min(prices[-20:]) - 1
        trend_ok = (
            all(
                a > b
                for a, b in [
                    (price, ma10),
                    (ma10, ma30),
                    (ma30, ma50),
                    (ma50, ma100),
                    (ma100, ma200),
                ][: config.alignment_depth]
            )
            and ma10 > ma10_prev
            and ma30 > ma30_prev
            and ma50 > ma50_prev
            and ma100 > ma100_prev
            and ma200 >= ma200_prev * 0.999
        )
        if not trend_ok:
            continue
        if recent_momentum_20 < config.momentum_20_min or volatility_20 > config.volatility_20_max:
            continue
        volume_ratio = volumes[-1] / (sum(volumes[-20:]) / 20)
        breakout = (
            price >= max(prices[-40:-1])
            and volume_ratio >= config.breakout_volume_ratio_min
            and recent_momentum_10 >= 0.01
        )
        pullback = min(prices[-5:]) <= ma10 * 1.01 and price >= ma10 and price >= prices[-2] and 0.85 <= volume_ratio <= 1.8
        trend_follow = (
            ma10 > ma10_prev
            and ma30 > ma30_prev
            and 0.9 <= volume_ratio <= 2.2
            and recent_momentum_20 >= config.trend_follow_momentum_min
        )
        if not (breakout or pullback or trend_follow):
            continue
        if breakout:
            strategy = "多均线突破"
        elif pullback:
            strategy = "多均线回踩"
        else:
            strategy = "多均线趋势"
        ma10_distance = price / ma10 - 1
        if ma10_distance > config.ma10_distance_max:
            continue
        recent_low = min(prices[-20:])
        stop_price = min(recent_low, ma30 * 0.985)
        distance200 = (price / ma200 - 1) * 100
        distance50 = (price / ma50 - 1) * 100
        slope200 = (ma200 / ma200_prev - 1) * 100
        slope100 = (ma100 / ma100_prev - 1) * 100
        score = (
            distance200 * config.score_distance200_weight
            + distance50 * config.score_distance50_weight
            + slope200 * config.slope200_weight
            + slope100 * config.slope100_weight
            + max(0.0, volume_ratio - 1) * config.volume_bonus_weight
            + recent_momentum_20 * config.momentum20_weight
            + recent_momentum_10 * config.momentum10_weight
            - max(0.0, volatility_20 * 100 - 18) * 0.6
        )
        if breakout:
            score += 10
        elif pullback:
            score += 8
        else:
            score += 5
        candidates.append(
            {
                "stock": stock,
                "strategy": strategy,
                "ma10": ma10,
                "ma30": ma30,
                "ma50": ma50,
                "ma100": ma100,
                "ma200": ma200,
                "volume_ratio": volume_ratio,
                "stop_price": stop_price,
                "score": score,
            }
        )
    return sorted(candidates, key=lambda item: item["score"], reverse=True)


def normalize_strategy_ratios(ratios: Dict[str, float] | None) -> Dict[str, float]:
    base = dict(DEFAULT_STRATEGY_RATIOS)
    if ratios:
        for key in ratios:
            base[key] = float(ratios[key])
    return base


def select_candidates_with_quota(
    candidates: List[Candidate],
    top: int,
    ratios: Dict[str, float] | None = None,
) -> List[Candidate]:
    if top <= 0:
        return []
    ranked = sorted(candidates, key=lambda item: item["score"], reverse=True)
    if len(ranked) <= top:
        return ranked
    groups: Dict[str, List[Candidate]] = {}
    for item in ranked:
        groups.setdefault(item["strategy"], []).append(item)
    # 稳定顺序：优先 DEFAULT_STRATEGY_RATIOS 的声明顺序，再按候选首次出现顺序补未知策略。
    order = [key for key in DEFAULT_STRATEGY_RATIOS if key in groups]
    order += [key for key in groups if key not in order]
    normalized = normalize_strategy_ratios(ratios)
    for key in list(normalized):
        if key not in groups:
            normalized.pop(key)
    for key in order:
        normalized.setdefault(key, 0.0)
    total = sum(normalized.values())
    if total <= 0:
        normalized = {key: 1 / len(order) for key in order}
    else:
        normalized = {key: normalized.get(key, 0.0) / total for key in order}
    targets: Dict[str, int] = {}
    fractions = []
    allocated = 0
    for strategy in order:
        raw_target = top * normalized.get(strategy, 0.0)
        base = int(raw_target)
        targets[strategy] = base
        allocated += base
        fractions.append((raw_target - base, strategy))
    for _, strategy in sorted(fractions, reverse=True):
        if allocated >= top:
            break
        targets[strategy] = targets.get(strategy, 0) + 1
        allocated += 1
    selected: List[Candidate] = []
    used_codes = set()
    for strategy in order:
        for item in groups[strategy]:
            if len(selected) >= top or targets.get(strategy, 0) <= 0:
                break
            if item["stock"].code in used_codes:
                continue
            selected.append(item)
            used_codes.add(item["stock"].code)
            targets[strategy] -= 1
    # 回填跳过在 ratios 中显式配 0 的策略（被进化压制的）；未声明 key 维持原弹性行为
    zero_quota = {k for k, v in (ratios or {}).items() if v <= 0}
    if len(selected) < top:
        for item in ranked:
            if len(selected) >= top:
                break
            if item["stock"].code in used_codes or item["strategy"] in zero_quota:
                continue
            selected.append(item)
            used_codes.add(item["stock"].code)
    return selected


def ma_strategy_candidates_adaptive(
    stocks: List[Stock],
    regime: MarketRegime,
    config: Optional[CandidateConfig] = None,
) -> List[Candidate]:
    """
    Market-adaptive stock selection.

    Bull market: use existing trend-following logic
    Bear market: strict oversold conditions (RSI<20, near 20d low, volume surge)
    Sideways market: stricter signals (precise pullback ±1%, tighter stop)
    """
    config = config or DEFAULT_CANDIDATE_CONFIG

    if regime.regime == RegimeType.BULL:
        # Bull market: use existing logic
        return ma_strategy_candidates(stocks, config)

    elif regime.regime == RegimeType.BEAR:
        # Bear market: strict oversold bounce conditions
        return _bear_market_candidates(stocks, config)

    else:
        # Sideways market: reduce frequency, stricter signals
        return _sideways_market_candidates(stocks, config)


def _bear_market_candidates(stocks: List[Stock], config: CandidateConfig) -> List[Candidate]:
    """
    Bear market oversold bounce stock selection.

    Strict conditions:
    - RSI < 20 (extreme oversold)
    - Price near 20-day low
    - Volume surge signal (money flowing in)
    """
    candidates = []
    for stock in stocks:
        prices = stock.prices
        volumes = stock.volumes
        if len(prices) < 30:
            continue

        # Calculate RSI
        rsi_value = rsi(prices)
        if rsi_value is None or rsi_value >= 20:
            continue  # Must have RSI < 20

        # Price position: near 20-day low
        low_20 = min(prices[-20:])
        price = prices[-1]
        price_near_low = price <= low_20 * 1.03  # Within 3% of 20-day low

        # MA stabilization: MA20 flattening or turning up
        ma20 = moving_average(prices, 20)
        ma20_prev = sum(prices[-21:-1]) / 20
        ma_stabilizing = ma20 >= ma20_prev * 0.995

        # Volume surge signal
        avg_volume_20 = sum(volumes[-20:]) / 20
        volume_ratio = volumes[-1] / avg_volume_20
        volume_surge = volume_ratio >= 1.5

        # All conditions must be met
        if price_near_low and ma_stabilizing and volume_surge:
            ma10 = moving_average(prices, 10)
            ma30 = moving_average(prices, 30)
            stop_price = min(min(prices[-20:]), ma30 * 0.985)

            # Scoring
            score = (
                (20 - rsi_value) * 2 +  # Lower RSI = higher score
                volume_ratio * 10 +
                (1 - price / low_20) * 50  # Closer to low = higher score
            )

            candidates.append({
                "stock": stock,
                "strategy": "熊市超跌反弹",
                "ma10": ma10,
                "ma30": ma30,
                "ma50": moving_average(prices, 50),
                "ma100": moving_average(prices, 100),
                "ma200": moving_average(prices, 200),
                "volume_ratio": volume_ratio,
                "stop_price": stop_price,
                "score": score,
            })

    return sorted(candidates, key=lambda item: item["score"], reverse=True)


def _sideways_market_candidates(stocks: List[Stock], config: CandidateConfig) -> List[Candidate]:
    """
    Sideways market stock selection.

    Stricter conditions:
    - Pullback requires precise (±1%) MA10 touch
    - Tighter stop loss
    - Max holding period of 10 days implied
    """
    candidates = []
    for stock in stocks:
        prices = stock.prices
        volumes = stock.volumes
        if len(prices) < 220:
            continue

        # Basic moving averages
        ma10 = sum(prices[-10:]) / 10
        ma30 = sum(prices[-30:]) / 30
        ma50 = sum(prices[-50:]) / 50
        ma100 = sum(prices[-100:]) / 100
        ma200 = sum(prices[-200:]) / 200

        price = prices[-1]

        # Check for sideways-market valid signals
        # Condition: price near MA10 with precise pullback (±1%)
        pullback = abs(price / ma10 - 1) <= 0.01
        if not pullback:
            continue

        # Trend confirmation (not too strong)
        trend_ok = price > ma10 > ma30 > ma50
        if not trend_ok:
            continue

        # Volume confirmation
        avg_volume_20 = sum(volumes[-20:]) / 20
        volume_ratio = volumes[-1] / avg_volume_20
        volume_ok = 0.9 <= volume_ratio <= 2.0

        if pullback and trend_ok and volume_ok:
            stop_price = min(min(prices[-20:]), ma30 * 0.97)  # Tighter stop

            # Scoring
            score = (
                (1 - abs(price / ma10 - 1)) * 30 +  # Pullback precision
                volume_ratio * 15 +
                (price / ma200 - 1) * 20
            )

            candidates.append({
                "stock": stock,
                "strategy": "震荡市精准回踩",
                "ma10": ma10,
                "ma30": ma30,
                "ma50": ma50,
                "ma100": ma100,
                "ma200": ma200,
                "volume_ratio": volume_ratio,
                "stop_price": stop_price,
                "score": score,
            })

    return sorted(candidates, key=lambda item: item["score"], reverse=True)
