from dataclasses import dataclass
from datetime import date
from typing import Dict, Set, Optional


@dataclass
class BacktestConfig:
    """回测配置参数。"""
    initial_cash: float = 1_000_000.0
    commission_rate: float = 0.00025
    stamp_tax_rate: float = 0.001
    slippage_rate: float = 0.001
    enable_T1: bool = True
    allow_limit_up_buy: bool = False
    allow_limit_down_sell: bool = False
    max_single_position: float = 0.10
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    price_type: str = "close"


@dataclass
class MarketData:
    """某交易日的截面行情数据。"""
    date: date
    close_prices: Dict[str, float]
    open_prices: Dict[str, float]
    suspended: Set[str]
    limit_up: Set[str]
    limit_down: Set[str]
