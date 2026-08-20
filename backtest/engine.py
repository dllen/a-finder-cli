import copy
from typing import List, Callable, Optional, Dict
from datetime import date
from backtest.config import BacktestConfig, MarketData
from backtest.models import Order, Trade, Position, Portfolio, DailyRecord, BacktestResult, OrderDirection, OrderType
from backtest.order_executor import OrderExecutor
from strategies.multi_factor_base import MultiFactorBase
from domain_models import Stock


class BacktestEngine:
    def __init__(self, config: BacktestConfig, strategy: MultiFactorBase,
                 market_data_provider: Callable[[date], MarketData]):
        self.config = config
        self.strategy = strategy
        self.market_data_provider = market_data_provider
        self.executor = OrderExecutor(config)

    def run(self, start_date: date, end_date: date,
            trade_dates: List[date], stock_pool: List[Stock],
            benchmark_data: Optional[Dict[date, float]] = None) -> BacktestResult:
        result = BacktestResult(
            config=self.config,
            start_date=start_date,
            end_date=end_date,
            initial_cash=self.config.initial_cash,
            final_value=self.config.initial_cash
        )

        portfolio = Portfolio(cash=self.config.initial_cash)

        for current_date in trade_dates:
            if current_date < start_date or current_date > end_date:
                continue

            market_data = self.market_data_provider(current_date)
            prices = market_data.close_prices
            portfolio.update(prices)

            # 调仓检查
            rebalance_orders = []
            signals = []
            if self._should_rebalance(current_date, self.strategy.config.rebalance_freq):
                selection = self.strategy.select(current_date, stock_pool)
                targets = {p.code: p.weight for p in selection.positions}
                signals.append(selection.rebalance_reason)
                rebalance_orders = self._generate_rebalance_orders(portfolio, targets, prices, current_date)

            # 止损检查
            stop_orders = self._check_stop_loss(portfolio, prices, current_date)
            # 止损/止盈优先: 该股票已整仓卖出时, 调仓不再重复卖出
            stop_codes = {o.code for o in stop_orders}
            rebalance_orders = [o for o in rebalance_orders
                                if not (o.direction == OrderDirection.SELL and o.code in stop_codes)]
            all_orders = rebalance_orders + stop_orders

            # 执行
            executed_trades, rejected = self.executor.execute(all_orders, market_data, portfolio, current_date)

            # 更新持仓
            self._update_portfolio(portfolio, executed_trades, current_date)
            portfolio.update(prices)

            # 记录
            daily_record = DailyRecord(
                date=current_date,
                portfolio=copy.deepcopy(portfolio),
                trades=executed_trades,
                signals=signals
            )
            result.daily_records.append(daily_record)
            result.all_trades.extend(executed_trades)

        result.final_value = portfolio.total_value
        return result

    def _should_rebalance(self, date: date, freq: str) -> bool:
        if freq == "monthly":
            return date.day <= 5
        elif freq == "quarterly":
            return date.day <= 5 and date.month in [1, 4, 7, 10]
        return False

    def _generate_rebalance_orders(self, portfolio: Portfolio, targets: Dict[str, float],
                                   prices: Dict[str, float], date: date) -> List[Order]:
        orders = []
        total_value = portfolio.total_value
        order_counter = 0

        target_positions = {}
        for code, weight in targets.items():
            if code in prices:
                target_value = total_value * weight
                qty = int(target_value / prices[code] / 100) * 100
                if qty > 0:
                    target_positions[code] = qty

        # 卖出
        for code, pos in portfolio.positions.items():
            if code not in target_positions and pos.quantity > 0:
                orders.append(Order(f"{date}_{order_counter}", date, code, OrderDirection.SELL, OrderType.MARKET, target_quantity=pos.quantity))
                order_counter += 1

        # 买入/调整
        for code, target_qty in target_positions.items():
            current_qty = portfolio.positions.get(code, Position(code=code)).quantity
            diff = target_qty - current_qty
            if diff != 0:
                orders.append(Order(f"{date}_{order_counter}", date, code,
                                   OrderDirection.BUY if diff > 0 else OrderDirection.SELL,
                                   OrderType.MARKET, target_quantity=abs(diff)))
                order_counter += 1

        return orders

    def _check_stop_loss(self, portfolio: Portfolio, prices: Dict[str, float], date: date) -> List[Order]:
        orders = []
        if self.config.stop_loss is None and self.config.take_profit is None:
            return orders
        for code, pos in portfolio.positions.items():
            if pos.avg_cost <= 0 or code not in prices:
                continue
            pct = prices[code] / pos.avg_cost
            if self.config.stop_loss is not None and pct <= self.config.stop_loss:
                orders.append(Order(f"{date}_sl_{code}", date, code, OrderDirection.SELL,
                                   OrderType.MARKET, target_quantity=pos.quantity))
            elif self.config.take_profit is not None and pct >= self.config.take_profit:
                orders.append(Order(f"{date}_tp_{code}", date, code, OrderDirection.SELL,
                                   OrderType.MARKET, target_quantity=pos.quantity))
        return orders

    def _update_portfolio(self, portfolio: Portfolio, trades: List[Trade], date: date):
        for trade in trades:
            pos = portfolio.positions.get(trade.code, Position(code=trade.code))

            if trade.direction == OrderDirection.BUY:
                total_cost = pos.quantity * pos.avg_cost + trade.quantity * trade.price
                pos.quantity += trade.quantity
                pos.avg_cost = total_cost / pos.quantity if pos.quantity > 0 else 0
                pos.buy_date = date
                portfolio.cash -= trade.net_amount
            else:
                if pos.quantity <= 0:
                    continue
                sell_qty = min(trade.quantity, pos.quantity)
                if sell_qty != trade.quantity:
                    trade.net_amount = trade.net_amount * (sell_qty / trade.quantity)
                    trade.quantity = sell_qty
                pos.quantity -= sell_qty
                if pos.quantity == 0:
                    pos.avg_cost = 0
                    pos.buy_date = None
                portfolio.cash += trade.net_amount

            if pos.quantity > 0:
                portfolio.positions[trade.code] = pos
            elif trade.code in portfolio.positions:
                del portfolio.positions[trade.code]
