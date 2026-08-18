from dataclasses import dataclass
from typing import Callable, Dict, List

from domain_models import Stock
from market_regime import RegimeType

from strategies.base import StrategySignal


@dataclass
class BacktestResult:
    strategy: str
    trades: int
    wins: int
    win_rate: float
    profit_factor: float
    expectancy: float
    passed: bool


def _snapshot(stock: Stock, idx: int) -> Stock:
    from dataclasses import replace
    return replace(
        stock,
        prices=stock.prices[: idx + 1],
        volumes=stock.volumes[: idx + 1],
        turnover=stock.turnover[: idx + 1] if stock.turnover else [],
        amount=stock.amount[: idx + 1] if stock.amount else [],
        pct_change=stock.pct_change[: idx + 1] if stock.pct_change else [],
    )


def _simulate(stock: Stock, entry_idx: int, sig: StrategySignal, max_hold: int) -> float:
    prices = stock.prices
    end = min(entry_idx + max_hold, len(prices) - 1)
    if end <= entry_idx:
        return 0.0
    for j in range(entry_idx + 1, end + 1):
        if prices[j] >= sig.tp:
            return sig.tp / sig.entry - 1
        if prices[j] <= sig.stop:
            return sig.stop / sig.entry - 1
    return prices[end] / sig.entry - 1


def run_strategy_backtest(
    stocks: List[Stock],
    detect: Callable,
    lows_map: Dict[str, List[float]],
    regimes: List[RegimeType],
    max_hold: int = 10,
    strategy: str = "",
) -> BacktestResult:
    valid = [s for s in stocks if len(s.prices) >= 61 and len(s.prices) == len(s.volumes)]
    if not valid:
        return BacktestResult(strategy, 0, 0, 0.0, 0.0, 0.0, False)
    n = min(len(s.prices) for s in valid)
    returns: List[float] = []
    for idx in range(60, n - 1):
        regime = regimes[idx] if idx < len(regimes) else RegimeType.SIDEWAYS
        for s in valid:
            sigs = detect(_snapshot(s, idx), regime)
            for sig in sigs:
                returns.append(_simulate(s, idx, sig, max_hold))
    wins = sum(1 for r in returns if r > 0)
    losses = [r for r in returns if r <= 0]
    gains = [r for r in returns if r > 0]
    avg_win = sum(gains) / len(gains) if gains else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    win_rate = wins / len(returns) if returns else 0.0
    profit_factor = (avg_win / abs(avg_loss)) if avg_loss != 0 else float("inf")
    expectancy = win_rate * avg_win - (1 - win_rate) * abs(avg_loss)
    passed = (win_rate >= 0.45 and profit_factor >= 1.5) or expectancy > 0
    return BacktestResult(strategy, len(returns), wins, win_rate, profit_factor, expectancy, passed)
