"""回测示例"""
import sys
from datetime import date, timedelta
from pathlib import Path
import random

# 支持 `python backtest/example.py` 直接运行: 将仓库根目录加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.engine import BacktestEngine
from backtest.config import BacktestConfig, MarketData
from backtest.performance import PerformanceAnalyzer
from strategies.pharma_multi_factor import PharmaMultiFactorStrategy
from domain_models import Stock


def generate_mock_stocks(n=50):
    random.seed(42)                      # 可复现
    stocks = []
    for i in range(n):
        base = random.uniform(20, 100)
        prices = []
        price = base
        drift = random.uniform(-0.001, 0.002)   # 每日漂移
        for _ in range(400):                     # >= 365 满足策略过滤
            price *= (1 + drift + random.uniform(-0.02, 0.02))
            prices.append(price)
        stocks.append(Stock(
            code=f"60{i:04d}",
            name=f"医药股{i}",
            pe=random.uniform(10, 80),
            pb=random.uniform(1, 10),
            peg=random.uniform(0.5, 3),
            revenue_growth=random.uniform(-0.1, 0.5),
            profit_growth=random.uniform(-0.2, 0.4),
            roe=random.uniform(0.08, 0.20),
            cashflow=random.uniform(0.05, 0.3),
            prices=prices,
            volumes=[random.randint(1000000, 10000000) for _ in range(400)],
            sector="医药生物",
            dividend_yield=random.uniform(0, 0.08),
            volatility_120d=random.uniform(0.15, 0.40),
        ))
    return stocks


def make_market_data_provider(stocks):
    series_len = len(stocks[0].prices)

    def provider(target_date):
        # 行情随交易日推进: 按日期序数在价格序列中取数, 使回测期内 NAV 真正波动
        idx = target_date.toordinal() % series_len
        prices = {s.code: s.prices[idx] for s in stocks}
        return MarketData(
            date=target_date,
            close_prices=prices,
            open_prices=prices,
            suspended=set(),
            limit_up=set(),
            limit_down=set(),
        )
    return provider


def get_trade_dates(start, end):
    dates = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            dates.append(current)
        current += timedelta(days=1)
    return dates


if __name__ == "__main__":
    # 初始化
    mock_stocks = generate_mock_stocks(50)
    strategy = PharmaMultiFactorStrategy()

    config = BacktestConfig(
        initial_cash=1_000_000,
        commission_rate=0.00025,
        stop_loss=0.75
    )

    # 回测
    engine = BacktestEngine(config, strategy, make_market_data_provider(mock_stocks))
    trade_dates = get_trade_dates(date(2024, 1, 1), date(2024, 6, 30))

    result = engine.run(date(2024, 1, 1), date(2024, 6, 30), trade_dates, mock_stocks)

    # 分析
    analyzer = PerformanceAnalyzer()
    metrics = analyzer.analyze(result)

    print(f"总收益: {metrics.total_return:.2%}")
    print(f"年化收益: {metrics.annualized_return:.2%}")
    print(f"夏普比率: {metrics.sharpe_ratio:.2f}")
    print(f"最大回撤: {metrics.max_drawdown:.2%}")
    print(f"交易次数: {metrics.total_trades}")
