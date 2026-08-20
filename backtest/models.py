from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Dict, List, Optional

from backtest.config import BacktestConfig


class OrderDirection(Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


@dataclass
class Order:
    """一笔委托。"""
    order_id: str
    date: date
    code: str
    direction: OrderDirection
    order_type: OrderType
    price: float = 0.0
    target_quantity: int = 0
    filled_quantity: int = 0
    status: str = "PENDING"
    reason: str = ""


@dataclass
class Trade:
    """一笔成交。"""
    trade_id: str
    order_id: str
    date: date
    code: str
    direction: OrderDirection
    price: float
    quantity: int
    commission: float
    stamp_tax: float
    net_amount: float


@dataclass
class Position:
    """单只股票的持仓。"""
    code: str
    name: str = ""
    quantity: int = 0
    avg_cost: float = 0.0
    current_price: float = 0.0
    current_value: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0
    buy_date: Optional[date] = None


@dataclass
class Portfolio:
    """组合：现金 + 持仓。"""
    cash: float = 0.0
    positions: Dict[str, Position] = field(default_factory=dict)
    total_value: float = 0.0

    def update(self, prices: Dict[str, float]):
        self.total_value = self.cash
        for code, pos in self.positions.items():
            if code in prices:
                pos.current_price = prices[code]
                pos.current_value = pos.quantity * pos.current_price
                pos.unrealized_pnl = pos.current_value - pos.quantity * pos.avg_cost
                if pos.avg_cost > 0:
                    pos.unrealized_pnl_pct = pos.unrealized_pnl / (pos.quantity * pos.avg_cost)
            self.total_value += pos.current_value


@dataclass
class DailyRecord:
    """单个交易日的记录。"""
    date: date
    portfolio: Portfolio
    trades: List[Trade] = field(default_factory=list)
    signals: List[str] = field(default_factory=list)


@dataclass
class BacktestResult:
    """回测结果汇总。"""
    config: BacktestConfig
    start_date: date
    end_date: date
    initial_cash: float
    final_value: float = 0.0
    daily_records: List[DailyRecord] = field(default_factory=list)
    all_trades: List[Trade] = field(default_factory=list)
