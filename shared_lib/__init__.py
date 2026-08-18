"""shared_lib — single source of truth for plan + backtest shared logic.

Both `plan_builder` and `ma_backtest` import risk_manager / market_regime from
here, so the two code paths cannot drift apart.
"""
from .strategy import (
    PlanRow,
    params_hash,
    compute_plan_prices,
)

# Re-exports from canonical modules — never re-implement.
from risk_manager import RiskManager, PositionConfig  # noqa: F401
from ma_backtest import default_candidate_config  # noqa: F401

__all__ = [
    "PlanRow",
    "params_hash",
    "compute_plan_prices",
    "RiskManager",
    "PositionConfig",
    "default_candidate_config",
]