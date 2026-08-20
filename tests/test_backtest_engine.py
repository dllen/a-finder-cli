import pytest
from datetime import date
from backtest.config import BacktestConfig, MarketData
from backtest.models import Order, OrderDirection, OrderType, Portfolio
from backtest.order_executor import OrderExecutor


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
