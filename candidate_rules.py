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
        ma50 = moving_average_slice(prices, 50)
        ma200 = moving_average_slice(prices, 200)
        ma200_prev = moving_average_slice(prices, 200, len(prices) - 20)
        ma50_prev = moving_average_slice(prices, 50, len(prices) - 10)
        price = prices[-1]
        if price < ma200:
            continue
        trend_ok = ma50 > ma200 and ma200 >= ma200_prev * 0.998 and ma50 > ma50_prev
        if not trend_ok:
            continue
        volume_ratio = volumes[-1] / (sum(volumes[-20:]) / 20)
        breakout = price >= max(prices[-40:]) and volume_ratio >= 1.1
        pullback = abs(price - ma50) / ma50 <= 0.03
        trend_follow = volume_ratio >= 0.9
        if not (breakout or pullback or trend_follow):
            continue
        if breakout:
            strategy = "均线突破"
        elif pullback:
            strategy = "回调均线"
        else:
            strategy = "趋势跟随"
        recent_low = min(prices[-20:])
        stop_price = min(recent_low, ma50 * 0.97)
        distance = (price / ma200 - 1) * 100
        slope = (ma200 / ma200_prev - 1) * 100
        score = distance * 1.2 + slope * 3.5 + max(0.0, volume_ratio - 1) * 15
        if breakout:
            score += 10
        else:
            score += 5
        candidates.append(
            {
                "stock": stock,
                "strategy": strategy,
                "ma50": ma50,
                "ma200": ma200,
                "volume_ratio": volume_ratio,
                "stop_price": stop_price,
                "score": score,
            }
        )
    return sorted(candidates, key=lambda item: item["score"], reverse=True)
