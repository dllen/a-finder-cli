from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SyncConfig:
    mode: str
    limit: Optional[int]
    sleep_seconds: float = 0.05


# Daily execution plan defaults (used by plan_builder sanity gate).
RR_TARGET = 2.0        # take-profit / stop-loss ratio
MAX_SINGLE = 0.15      # max single-position weight (reference only; fixed-share lots)
MAX_TOTAL = 0.95       # max total portfolio weight
MAX_POSITIONS = 8      # max positions per tier (diversification: pick top-N by score, equal-weight)
SLIPPAGE = 0.001       # paper-trade fill slippage (0.1%)
STOP_ATR_MULT = 2.0    # ATR multiple for stop

# Initial-capital tiers for the daily plan (元). Default active capital = 10W.
# 5W → 50W every 5W（10 档），CLI/页面/plan_builder 共用同一份。
CAPITAL_TIERS = [50000, 100000, 150000, 200000, 250000, 300000, 350000, 400000, 450000, 500000]
DEFAULT_CAPITAL = 100000


# === 可转债策略（独立于股票体系）===
CB_SCORING_WEIGHTS = {"a": 0.40, "b": 0.30, "c": 0.30}   # 三组权重

CB_FILTERS = {
    "premium_min": -5.0,        # 转股溢价率下限 (%)
    "premium_max": 40.0,        # 转股溢价率上限 (%)
    "price_min": 80.0,          # 债现价下限
    "price_max": 200.0,         # 债现价上限
    "min_issue_size": 0.5,      # 发行规模下限（亿元）
    "min_convert_value": 60.0,  # 转股价值下限
    "redeem_dist_min": 3.0,     # 强赎触发距离下限 (%)
    "exclude_ratings": ("B", "CC", "C"),  # 评级含这些子串即剔除
    "min_factors": 2,           # 最少可用因子数，低于则不进入榜单
}

CB_RISK = {
    "max_single": 0.15,       # 单只上限 15%
    "max_positions": 10,      # 最多 10 只
    "stop_loss_pct": 0.08,    # 止损 -8%
    "take_profit_pct": 0.20,  # 止盈 +20%
    "lot_size": 10,           # 整手：10 张 = 面值 1000 元
    # 价格区间与 CB_FILTERS 同源，避免两处漂移
    "min_price": CB_FILTERS["price_min"],
    "max_price": CB_FILTERS["price_max"],
}

# ---------------------------------------------------------------------------
# 10w 决策台：专属配置（报告 docs/reports/10w-decision-system.md 第 4/5 章）
# 与全局 MAX_SINGLE / MAX_POSITIONS 等区分——这是 10w 本金场景的独立参数，
# 不影响其他资金档位的 plan 生成。
# ---------------------------------------------------------------------------
PORTFOLIO_10W = {
    "total_capital": 100000.0,
    "stock_ratio": 0.8,
    "cb_ratio": 0.2,
    "max_positions": 8,
    "max_single_weight": 0.10,
    "max_stock_exposure": 0.75,
    "cb_capital": 20000.0,
    "max_cb_positions": 3,
}
