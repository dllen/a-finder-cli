from typing import Dict, List

from domain_models import Stock
from indicators import normalize


def score_stocks(stocks: List[Stock]) -> Dict[str, float]:
    pe_scores = normalize([s.pe for s in stocks], higher_is_better=False)
    pb_scores = normalize([s.pb for s in stocks], higher_is_better=False)
    peg_scores = normalize([s.peg for s in stocks], higher_is_better=False)
    value_scores = [(a + b + c) / 3 for a, b, c in zip(pe_scores, pb_scores, peg_scores)]
    revenue_scores = normalize([s.revenue_growth for s in stocks], higher_is_better=True)
    profit_scores = normalize([s.profit_growth for s in stocks], higher_is_better=True)
    growth_scores = [(a + b) / 2 for a, b in zip(revenue_scores, profit_scores)]
    roe_scores = normalize([s.roe for s in stocks], higher_is_better=True)
    cash_scores = normalize([s.cashflow for s in stocks], higher_is_better=True)
    quality_scores = [(a + b) / 2 for a, b in zip(roe_scores, cash_scores)]
    momentum_raw = [(s.prices[-1] / s.prices[-60] - 1) * 100 for s in stocks]
    momentum_scores = normalize(momentum_raw, higher_is_better=True)
    scores = {}
    for stock, value, growth, momentum, quality in zip(
        stocks, value_scores, growth_scores, momentum_scores, quality_scores
    ):
        total = 0.3 * value + 0.3 * growth + 0.2 * momentum + 0.2 * quality
        scores[stock.code] = round(total, 2)
    return scores
