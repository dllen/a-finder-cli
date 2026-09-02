"""Pure functions for plan_builder + shared types for plan rows.

No DB I/O, no network, no logging side-effects. Re-exports canonical types
(risk_manager, market_regime) so plan_builder and ma_backtest pull from the
same source.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Literal

from risk_manager import PositionConfig  # for type hint; re-exported from package root

LOT_SIZE = 100  # A股一手


def size_shares(capital: float, size_pct: float, price: float) -> int:
    """A股整手建仓：预算 = capital*size_pct，股数 = floor(预算/价格/100)*100。

    不足一手返回 0。前端 PLAN_SCRIPT.sizeShares 为此函数的 JS 镜像，禁止单边改动。
    """
    if capital <= 0 or size_pct <= 0 or price <= 0:
        return 0
    budget = float(capital) * float(size_pct)
    return int(budget // (price * LOT_SIZE)) * LOT_SIZE


@dataclass
class PlanRow:
    code: str
    action: Literal["buy", "hold", "exit"]
    plan_price: float
    size_pct: float
    stop_price: float
    tp_price: float
    rr_ratio: float
    rationale: dict = field(default_factory=dict)
    status: Literal["ok", "failed"] = "ok"
    reason: str = ""
    shares: int = 200


def params_hash(d: dict) -> str:
    """Deterministic sha256 of a params dict (sorted keys)."""
    payload = json.dumps(d, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_plan_prices(plan_price: float, cfg: PositionConfig) -> tuple[float, float]:
    """Translate PositionConfig percentages into absolute stop/tp prices.

    stop_loss_pct is negative (e.g. -0.08 = 8% below entry).
    profit_target_pct is positive (e.g. 0.20 = 20% above entry).
    """
    stop = plan_price * (1.0 + cfg.stop_loss_pct)
    tp = plan_price * (1.0 + cfg.profit_target_pct)
    return round(stop, 4), round(tp, 4)