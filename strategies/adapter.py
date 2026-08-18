from typing import Dict, List, Optional, Set

from candidate_rules import (
    DEFAULT_STRATEGY_RATIOS,
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


def load_passed_strategies(report_path: str = "strategies/report.json") -> Set[str]:
    """从回测报告读取达标（passed=True）的策略名。报告缺失/损坏时返回空集。"""
    import json
    from pathlib import Path

    path = Path(report_path)
    if not path.exists():
        return set()
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    return {row["strategy"] for row in rows if row.get("passed")}


def merged_strategy_ratios(
    passed_strategies: Set[str],
    new_budget: float = 0.30,
) -> Dict[str, float]:
    """为达标新策略分配配额：多均线保留 (1-new_budget)，达标策略均分 new_budget。"""
    ratios: Dict[str, float] = dict(DEFAULT_STRATEGY_RATIOS)
    total_ma = sum(ratios.values())
    if total_ma > 0:
        scale = (1 - new_budget) / total_ma
        ratios = {key: value * scale for key, value in ratios.items()}
    passed = list(passed_strategies)
    if passed:
        share = new_budget / len(passed)
        for name in passed:
            ratios[name] = share
    return ratios
