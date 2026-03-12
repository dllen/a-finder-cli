from typing import TypedDict

from domain_models import Stock


class Candidate(TypedDict):
    stock: Stock
    strategy: str
    ma10: float
    ma30: float
    ma50: float
    ma100: float
    ma200: float
    volume_ratio: float
    stop_price: float
    score: float
