from dataclasses import dataclass
from typing import Dict, List, Optional
from datetime import date
import math


@dataclass
class PerformanceMetrics:
    total_return: float = 0.0
    annualized_return: float = 0.0
    volatility: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_date: Optional[date] = None
    total_trades: int = 0


class PerformanceAnalyzer:
    def __init__(self, risk_free_rate: float = 0.02):
        self.rf = risk_free_rate

    def analyze(self, result) -> PerformanceMetrics:
        nav_series = self._build_nav_series(result)

        metrics = PerformanceMetrics()
        metrics.total_return = self._total_return(nav_series)
        metrics.annualized_return = self._annualized_return(nav_series)
        metrics.volatility = self._volatility(nav_series)
        metrics.sharpe_ratio = self._sharpe(nav_series)
        metrics.max_drawdown, metrics.max_drawdown_date = self._max_drawdown(nav_series)
        metrics.total_trades = len(result.all_trades)

        return metrics

    def _build_nav_series(self, result):
        data = [(rec.date, rec.portfolio.total_value / result.initial_cash) for rec in result.daily_records]
        return dict(data)

    def _daily_returns(self, nav: dict) -> List[float]:
        values = list(nav.values())
        return [values[i] / values[i - 1] - 1 for i in range(1, len(values))]

    def _total_return(self, nav):
        values = list(nav.values())
        return (values[-1] / values[0]) - 1 if values else 0

    def _annualized_return(self, nav):
        dates = list(nav.keys())
        values = list(nav.values())
        if len(values) < 2:
            return 0
        tr = self._total_return(nav)
        years = (dates[-1] - dates[0]).days / 365
        return (1 + tr) ** (1 / years) - 1 if years > 0 else 0

    def _volatility(self, nav):
        values = list(nav.values())
        if len(values) < 2:
            return 0
        returns = self._daily_returns(nav)
        if not returns:
            return 0
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / len(returns)
        return math.sqrt(variance * 252)

    def _sharpe(self, nav):
        values = list(nav.values())
        if len(values) < 2:
            return 0
        returns = self._daily_returns(nav)
        if not returns:
            return 0
        mean = sum(returns) / len(returns)
        std = math.sqrt(sum((r - mean) ** 2 for r in returns) / len(returns))
        if std == 0:
            return 0
        excess = mean - self.rf / 252
        return excess / std * math.sqrt(252)

    def _max_drawdown(self, nav):
        values = list(nav.values())
        if not values:
            return 0, None
        peak = values[0]
        max_dd = 0
        max_dd_date = None
        dates = list(nav.keys())
        for i, v in enumerate(values):
            if v > peak:
                peak = v
            dd = (v - peak) / peak
            if dd < max_dd:
                max_dd = dd
                max_dd_date = dates[i]
        return max_dd, max_dd_date
