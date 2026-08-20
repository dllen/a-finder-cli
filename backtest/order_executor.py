from typing import List, Tuple, Dict
from datetime import date
from backtest.config import BacktestConfig, MarketData
from backtest.models import Order, Trade, Portfolio, OrderDirection
from backtest.cost_calculator import calculate_costs


class OrderExecutor:
    def __init__(self, config: BacktestConfig):
        self.config = config
        self.hold_history: Dict[str, List[date]] = {}

    def execute(self, orders: List[Order], market_data: MarketData,
                portfolio: Portfolio, exec_date: date) -> Tuple[List[Trade], List[Order]]:
        executed, rejected = [], []
        trade_counter = 0

        for order in orders:
            trade, updated_order = self._execute_single(order, market_data, portfolio, exec_date, trade_counter)
            trade_counter += 1
            if trade:
                executed.append(trade)
            else:
                rejected.append(updated_order)

        return executed, rejected

    def _execute_single(self, order: Order, market_data: MarketData,
                        portfolio: Portfolio, exec_date: date, trade_id: int):
        code = order.code

        # 停牌检查
        if code in market_data.suspended:
            order.status = "REJECTED"
            order.reason = "停牌"
            return None, order

        # 获取价格
        if code not in market_data.close_prices:
            order.status = "REJECTED"
            order.reason = "无价格"
            return None, order

        exec_price = market_data.close_prices[code]

        # 涨跌停检查
        if order.direction == OrderDirection.BUY:
            if code in market_data.limit_up and not self.config.allow_limit_up_buy:
                order.status = "REJECTED"
                order.reason = "涨停"
                return None, order
        elif order.direction == OrderDirection.SELL:
            if code in market_data.limit_down and not self.config.allow_limit_down_sell:
                order.status = "REJECTED"
                order.reason = "跌停"
                return None, order
            # T+1: 当日买入不可当日卖出
            if self.config.enable_T1 and code in self.hold_history \
                    and self.hold_history[code] and self.hold_history[code][-1] == exec_date:
                order.status = "REJECTED"
                order.reason = "T+1"
                return None, order

        # 应用滑点
        if order.direction == OrderDirection.BUY:
            exec_price *= (1 + self.config.slippage_rate)
        else:
            exec_price *= (1 - self.config.slippage_rate)

        quantity = order.target_quantity
        commission, stamp_tax, slippage = calculate_costs(order.direction, exec_price, quantity, self.config)

        gross = exec_price * quantity
        if order.direction == OrderDirection.BUY:
            net_amount = gross + commission + stamp_tax      # 买入: 总支出
        else:
            net_amount = gross - commission - stamp_tax      # 卖出: 净收入

        trade = Trade(
            trade_id=f"T{trade_id}",
            order_id=order.order_id,
            date=exec_date,
            code=code,
            direction=order.direction,
            price=exec_price,
            quantity=quantity,
            commission=commission,
            stamp_tax=stamp_tax,
            net_amount=net_amount
        )

        order.status = "FILLED"
        order.filled_quantity = quantity

        # 记录当日买入(用于 T+1 判断)
        if order.direction == OrderDirection.BUY:
            self.hold_history.setdefault(code, []).append(exec_date)

        return trade, order
