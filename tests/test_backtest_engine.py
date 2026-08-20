import pytest
from datetime import date
from backtest.config import BacktestConfig, MarketData
from backtest.models import Order, OrderDirection, OrderType, Portfolio, Position
from backtest.order_executor import OrderExecutor
from backtest.engine import BacktestEngine
from strategies.multi_factor_base import MultiFactorBase, MultiFactorConfig, SelectionResult, TargetPosition


def test_t1_restriction():
    config = BacktestConfig(enable_T1=True)
    executor = OrderExecutor(config)

    market_data = MarketData(
        date=date(2024, 1, 2),
        close_prices={"1": 10.0},
        open_prices={"1": 10.0},
        suspended=set(),
        limit_up=set(),
        limit_down=set()
    )

    portfolio = Portfolio(cash=10000)

    # 买入
    buy_order = Order("1", date(2024, 1, 2), "1", OrderDirection.BUY, OrderType.MARKET, target_quantity=100)
    trades, _ = executor.execute([buy_order], market_data, portfolio, date(2024, 1, 2))
    assert len(trades) == 1

    # 当日再卖 - 应被拒绝
    sell_order = Order("2", date(2024, 1, 2), "1", OrderDirection.SELL, OrderType.MARKET, target_quantity=100)
    trades2, rejected = executor.execute([sell_order], market_data, portfolio, date(2024, 1, 2))
    assert len(rejected) == 1
    assert rejected[0].reason == "T+1"


def test_limit_up_buy_blocked():
    config = BacktestConfig(allow_limit_up_buy=False)
    executor = OrderExecutor(config)

    market_data = MarketData(
        date=date(2024, 1, 2),
        close_prices={"1": 11.0},
        open_prices={"1": 10.0},
        suspended=set(),
        limit_up={"1"},
        limit_down=set()
    )

    portfolio = Portfolio(cash=10000)

    buy_order = Order("1", date(2024, 1, 2), "1", OrderDirection.BUY, OrderType.MARKET, target_quantity=100)
    trades, rejected = executor.execute([buy_order], market_data, portfolio, date(2024, 1, 2))
    assert len(rejected) == 1
    assert "涨停" in rejected[0].reason


def _make_market_data(d, prices, suspended=(), limit_up=(), limit_down=()):
    return MarketData(
        date=d,
        close_prices=prices,
        open_prices=prices,
        suspended=set(suspended),
        limit_up=set(limit_up),
        limit_down=set(limit_down),
    )


def test_net_amount_sign_and_magnitude():
    config = BacktestConfig(slippage_rate=0, enable_T1=False,
                            commission_rate=0.00025, stamp_tax_rate=0.001)
    executor = OrderExecutor(config)
    market_data = _make_market_data(date(2024, 1, 2), {"1": 10.0})
    portfolio = Portfolio(cash=100000)

    buy = Order("b1", date(2024, 1, 2), "1", OrderDirection.BUY, OrderType.MARKET, target_quantity=100)
    trades, _ = executor.execute([buy], market_data, portfolio, date(2024, 1, 2))
    assert len(trades) == 1
    t = trades[0]
    gross = t.price * t.quantity
    commission = gross * config.commission_rate
    assert t.stamp_tax == 0.0
    # 买入: 净支出 = 成交额 + 佣金 + 印花税(买入为 0)
    assert t.net_amount == gross + commission + t.stamp_tax
    assert t.net_amount > 0

    sell = Order("s1", date(2024, 1, 2), "1", OrderDirection.SELL, OrderType.MARKET, target_quantity=100)
    trades2, _ = executor.execute([sell], market_data, portfolio, date(2024, 1, 2))
    assert len(trades2) == 1
    t2 = trades2[0]
    gross2 = t2.price * t2.quantity
    commission2 = gross2 * config.commission_rate
    stamp_tax2 = gross2 * config.stamp_tax_rate
    # 卖出: 净收入 = 成交额 - 佣金 - 印花税
    assert t2.net_amount == gross2 - commission2 - stamp_tax2
    assert t2.net_amount > 0


def test_generate_rebalance_orders_sell_and_buy():
    strategy = MultiFactorBase(MultiFactorConfig(name="dummy"))
    engine = BacktestEngine(BacktestConfig(), strategy, lambda d: None)
    prices = {"A": 10.0, "B": 20.0}
    portfolio = Portfolio(cash=100000)
    portfolio.positions["A"] = Position(code="A", quantity=300, avg_cost=10.0)
    portfolio.positions["B"] = Position(code="B", quantity=100, avg_cost=20.0)
    portfolio.update(prices)

    targets = {"B": 0.5}

    orders = engine._generate_rebalance_orders(portfolio, targets, prices, date(2024, 1, 2))

    sells = [o for o in orders if o.direction == OrderDirection.SELL]
    buys = [o for o in orders if o.direction == OrderDirection.BUY]

    # A 不在目标中 → 恰好一笔全额卖出
    sell_a = [o for o in sells if o.code == "A"]
    assert len(sell_a) == 1
    assert sell_a[0].target_quantity == 300

    # B 目标数量超过当前持仓 → 买入差额, 且为 100 的整数倍
    buy_b = [o for o in buys if o.code == "B"]
    assert len(buy_b) == 1
    target_qty = int(portfolio.total_value * 0.5 / prices["B"] / 100) * 100
    assert buy_b[0].target_quantity == target_qty - 100
    assert buy_b[0].target_quantity % 100 == 0


def test_cash_conservation_full_run():
    class ConstantStrategy(MultiFactorBase):
        def select(self, date, candidates):
            return SelectionResult(
                date=date,
                positions=[TargetPosition(code="1", name="s", weight=0.5, score=0.0)],
                rebalance_reason="test",
            )

    strategy = ConstantStrategy(MultiFactorConfig(name="const", rebalance_freq="monthly"))
    config = BacktestConfig(initial_cash=100000, slippage_rate=0)

    def provider(d):
        return _make_market_data(d, {"1": 10.0})

    engine = BacktestEngine(config, strategy, provider)
    dates = [date(2024, 1, 2), date(2024, 1, 3)]
    result = engine.run(date(2024, 1, 2), date(2024, 1, 3), dates, [])

    assert len(result.all_trades) >= 1
    buys = [t for t in result.all_trades if t.direction == OrderDirection.BUY]
    sells = [t for t in result.all_trades if t.direction == OrderDirection.SELL]
    assert all(t.net_amount > 0 for t in buys)
    assert all(t.net_amount > 0 for t in sells)

    # 现金流入恒等式: 期末现金 = 初始现金 + Σ(卖出净额) - Σ(买入净额)
    final_portfolio = result.daily_records[-1].portfolio
    net_flow = sum(t.net_amount for t in sells) - sum(t.net_amount for t in buys)
    assert final_portfolio.cash == pytest.approx(config.initial_cash + net_flow)

    # 费用恒等式: 价格恒定、无滑点时, 组合净值 = 初始现金 - 总费用
    total_fees = sum(t.commission + t.stamp_tax for t in result.all_trades)
    holdings_value = sum(p.quantity * p.current_price for p in final_portfolio.positions.values())
    assert final_portfolio.cash + holdings_value == pytest.approx(config.initial_cash - total_fees)
    assert result.final_value == pytest.approx(config.initial_cash - total_fees)

    # 任何交易日持仓均不为负
    for rec in result.daily_records:
        for pos in rec.portfolio.positions.values():
            assert pos.quantity >= 0


def test_stop_loss_and_rebalance_no_double_sell():
    class FlipStrategy(MultiFactorBase):
        def __init__(self, config):
            super().__init__(config)
            self.calls = 0

        def select(self, date, candidates):
            self.calls += 1
            if self.calls == 1:
                return SelectionResult(
                    date=date,
                    positions=[TargetPosition(code="1", name="s", weight=0.5, score=0.0)],
                    rebalance_reason="buy",
                )
            return SelectionResult(date=date, positions=[], rebalance_reason="sell all")

    strategy = FlipStrategy(MultiFactorConfig(name="flip", rebalance_freq="monthly"))
    config = BacktestConfig(initial_cash=100000, slippage_rate=0, stop_loss=0.99)

    def provider(d):
        price = {"1": 10.0} if d == date(2024, 1, 2) else {"1": 9.0}
        return _make_market_data(d, price)

    engine = BacktestEngine(config, strategy, provider)
    dates = [date(2024, 1, 2), date(2024, 1, 3)]
    result = engine.run(date(2024, 1, 2), date(2024, 1, 3), dates, [])

    sells = [t for t in result.all_trades if t.direction == OrderDirection.SELL and t.code == "1"]
    # 止损与调仓同日, 只应产生一笔卖出
    assert len(sells) == 1
    assert sells[0].quantity == 5000

    # 卖出总量不超过初始持仓, 且任何交易日无负持仓
    initial_holding = 5000
    assert sum(t.quantity for t in sells) <= initial_holding
    for rec in result.daily_records:
        for pos in rec.portfolio.positions.values():
            assert pos.quantity >= 0

