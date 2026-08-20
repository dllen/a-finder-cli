from backtest.config import BacktestConfig
from backtest.models import OrderDirection


def calculate_costs(direction: OrderDirection, price: float, quantity: int, config: BacktestConfig):
    amount = price * quantity
    commission = amount * config.commission_rate
    stamp_tax = amount * config.stamp_tax_rate if direction == OrderDirection.SELL else 0.0
    slippage = amount * config.slippage_rate
    return commission, stamp_tax, slippage
