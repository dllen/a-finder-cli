from typing import List

from candidate_schema import Candidate
from domain_models import Stock
from indicators import moving_average_slice


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
        trend_ok = (
            price > ma20 > ma30 > ma50 > ma100 > ma200
            and ma50 > ma50_prev
            and ma100 > ma100_prev
            and ma200 >= ma200_prev * 0.999
        )
        if not trend_ok:
            continue
        volume_ratio = volumes[-1] / (sum(volumes[-20:]) / 20)
        breakout = price >= max(prices[-30:]) and volume_ratio >= 1.15
        pullback = min(prices[-5:]) <= ma20 * 1.01 and price >= ma20 and price >= prices[-2]
        trend_follow = ma20 > ma20_prev and ma30 > ma30_prev and 0.9 <= volume_ratio <= 2.8
        if not (breakout or pullback or trend_follow):
            continue
        if breakout:
            strategy = "多均线突破"
        elif pullback:
            strategy = "多均线回踩"
        else:
            strategy = "多均线趋势"
        ma20_distance = price / ma20 - 1
        if ma20_distance > 0.12:
            continue
        recent_low = min(prices[-20:])
        stop_price = min(recent_low, ma30 * 0.98)
        distance200 = (price / ma200 - 1) * 100
        distance50 = (price / ma50 - 1) * 100
        slope200 = (ma200 / ma200_prev - 1) * 100
        slope100 = (ma100 / ma100_prev - 1) * 100
        score = distance200 * 0.8 + distance50 * 0.6 + slope200 * 2.5 + slope100 * 2.0 + max(0.0, volume_ratio - 1) * 10
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
