from typing import List, Optional, Set

from candidate_rules import (
    Candidate,
    CandidateConfig,
    ma_strategy_candidates_adaptive,
)
from domain_models import Stock
from market_regime import MarketRegime, RegimeType
from strategies import STRATEGIES
from strategies.base import StrategySignal


def signal_to_candidate(stock: Stock, sig: StrategySignal) -> Candidate:
    prices = stock.prices
    volumes = stock.volumes
    if not prices:
        prices = [0.0]

    def ma(w: int) -> float:
        return sum(prices[-w:]) / min(w, len(prices))

    avg_vol = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else 0.0
    volume_ratio = (volumes[-1] / avg_vol) if avg_vol > 0 and volumes else 0.0
    return {
        "stock": stock,
        "strategy": sig.strategy,
        "ma10": ma(10),
        "ma30": ma(30),
        "ma50": ma(50),
        "ma100": ma(100),
        "ma200": ma(200),
        "volume_ratio": volume_ratio,
        "stop_price": sig.stop,
        "score": sig.score,
    }


def merge_candidates(
    stocks: List[Stock],
    regime: RegimeType,
    passed_strategies: Optional[Set[str]] = None,
    config: Optional[CandidateConfig] = None,
) -> List[Candidate]:
    candidates: List[Candidate] = list(
        ma_strategy_candidates_adaptive(
            stocks,
            MarketRegime(
                regime=regime,
                confidence=0.0,
                tech_score=0.0,
                index_score=0.0,
                fundamental_score=0.0,
            ),
            config,
        )
    )
    passed = passed_strategies or set()
    for stock in stocks:
        for name in passed:
            detect = STRATEGIES.get(name)
            if detect is None:
                continue
            for sig in detect(stock, regime):
                candidates.append(signal_to_candidate(stock, sig))
    return candidates
