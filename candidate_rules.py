from typing import Dict, List

from candidate_schema import Candidate
from domain_models import Stock
from indicators import moving_average_slice

DEFAULT_STRATEGY_RATIOS: Dict[str, float] = {
    "多均线突破": 0.4,
    "多均线回踩": 0.3,
    "多均线趋势": 0.3,
}


def ma_strategy_candidates(stocks: List[Stock]) -> List[Candidate]:
    candidates = []
    for stock in stocks:
        prices = stock.prices
        volumes = stock.volumes
        if len(prices) < 220:
            continue
        ma20 = moving_average_slice(prices, 20)
        ma30 = moving_average_slice(prices, 30)
        ma50 = moving_average_slice(prices, 50)
        ma100 = moving_average_slice(prices, 100)
        ma200 = moving_average_slice(prices, 200)
        ma20_prev = moving_average_slice(prices, 20, len(prices) - 5)
        ma30_prev = moving_average_slice(prices, 30, len(prices) - 5)
        ma200_prev = moving_average_slice(prices, 200, len(prices) - 20)
        ma50_prev = moving_average_slice(prices, 50, len(prices) - 5)
        ma100_prev = moving_average_slice(prices, 100, len(prices) - 5)
        price = prices[-1]
        recent_momentum_10 = price / prices[-10] - 1
        recent_momentum_20 = price / prices[-20] - 1
        volatility_20 = max(prices[-20:]) / min(prices[-20:]) - 1
        trend_ok = (
            price > ma20 > ma30 > ma50 > ma100 > ma200
            and ma20 > ma20_prev
            and ma30 > ma30_prev
            and ma50 > ma50_prev
            and ma100 > ma100_prev
            and ma200 >= ma200_prev * 0.999
        )
        if not trend_ok:
            continue
        if recent_momentum_20 < 0.015 or volatility_20 > 0.35:
            continue
        volume_ratio = volumes[-1] / (sum(volumes[-20:]) / 20)
        breakout = price >= max(prices[-40:-1]) and volume_ratio >= 1.1 and recent_momentum_10 >= 0.01
        pullback = min(prices[-5:]) <= ma20 * 1.01 and price >= ma20 and price >= prices[-2] and 0.85 <= volume_ratio <= 1.8
        trend_follow = ma20 > ma20_prev and ma30 > ma30_prev and 0.9 <= volume_ratio <= 2.2 and recent_momentum_20 >= 0.03
        if not (breakout or pullback or trend_follow):
            continue
        if breakout:
            strategy = "多均线突破"
        elif pullback:
            strategy = "多均线回踩"
        else:
            strategy = "多均线趋势"
        ma20_distance = price / ma20 - 1
        if ma20_distance > 0.09:
            continue
        recent_low = min(prices[-20:])
        stop_price = min(recent_low, ma30 * 0.985)
        distance200 = (price / ma200 - 1) * 100
        distance50 = (price / ma50 - 1) * 100
        slope200 = (ma200 / ma200_prev - 1) * 100
        slope100 = (ma100 / ma100_prev - 1) * 100
        score = (
            distance200 * 0.8
            + distance50 * 0.6
            + slope200 * 2.5
            + slope100 * 2.0
            + max(0.0, volume_ratio - 1) * 10
            + recent_momentum_20 * 150
            + recent_momentum_10 * 80
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
                "ma20": ma20,
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
        for key in base:
            if key in ratios and ratios[key] > 0:
                base[key] = float(ratios[key])
    total = sum(base.values())
    if total <= 0:
        return dict(DEFAULT_STRATEGY_RATIOS)
    return {key: value / total for key, value in base.items()}


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
    groups: Dict[str, List[Candidate]] = {"多均线突破": [], "多均线回踩": [], "多均线趋势": []}
    for item in ranked:
        strategy = item["strategy"]
        if strategy in groups:
            groups[strategy].append(item)
    normalized = normalize_strategy_ratios(ratios)
    targets: Dict[str, int] = {}
    fractions = []
    allocated = 0
    for strategy, ratio in normalized.items():
        raw_target = top * ratio
        base = int(raw_target)
        targets[strategy] = base
        allocated += base
        fractions.append((raw_target - base, strategy))
    for _, strategy in sorted(fractions, reverse=True):
        if allocated >= top:
            break
        targets[strategy] += 1
        allocated += 1
    selected: List[Candidate] = []
    used_codes = set()
    for strategy in ["多均线突破", "多均线回踩", "多均线趋势"]:
        quota = targets[strategy]
        if quota <= 0:
            continue
        for item in groups[strategy]:
            if len(selected) >= top or quota <= 0:
                break
            code = item["stock"].code
            if code in used_codes:
                continue
            selected.append(item)
            used_codes.add(code)
            quota -= 1
    if len(selected) < top:
        for item in ranked:
            if len(selected) >= top:
                break
            code = item["stock"].code
            if code in used_codes:
                continue
            selected.append(item)
            used_codes.add(code)
    return selected
