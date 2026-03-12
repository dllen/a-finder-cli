from typing import Dict, List

from domain_models import Stock
from scoring import score_stocks


def get_scores(stocks: List[Stock]) -> Dict[str, float]:
    return score_stocks(stocks)
