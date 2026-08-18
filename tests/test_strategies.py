from strategies.backtest import run_strategy_backtest
from strategies.base import StrategySignal, bollinger, kdj
from strategies.base import bollinger as _bollinger
from domain_models import Stock
from market_regime import RegimeType
from strategies.box_breakout import detect as box_detect
from strategies.new_high import detect as high_detect
from strategies.bollinger_rebound import detect as rebound_detect
from strategies.kdj_cross import detect as kdj_detect
from strategies.volume_price import detect as vp_detect


def test_bollinger_shape_and_values():
    prices = [100.0, 101, 102, 103, 104, 105, 106, 107, 108, 109,
              110, 111, 112, 113, 114, 115, 116, 117, 118, 119]
    mid, upper, lower = bollinger(prices, window=20, k=2.0)
    assert upper > mid > lower
    assert abs(mid - sum(prices) / 20) < 1e-6


def test_kdj_cross_signal_values():
    prices = [100.0] * 8 + [90.0, 91, 92, 93, 94, 95, 96, 97, 98, 99, 100]
    ks, ds, js = kdj(prices)
    assert len(ks) == len(prices)
    assert ks[-1] > 0
    # J = 3K - 2D，标准 KDJ 的 J 可超过 100（超买/超卖钝化），只断言非负
    assert js[-1] >= 0


def test_signal_dataclass_fields():
    sig = StrategySignal(code="600519", strategy="箱体突破",
                         entry=100.0, stop=97.0, tp=106.0, score=60.0)
    assert sig.code == "600519"
    assert sig.tp > sig.entry > sig.stop


def make_stock(prices, volumes=None, pct=None):
    n = len(prices)
    if volumes is None:
        volumes = [1_000_000] * n
    if pct is None:
        pct = [0.0] * n
    return Stock(code="000001", name="测试", pe=10, pb=1.0, peg=1.0,
                 revenue_growth=0.1, profit_growth=0.1, roe=0.1, cashflow=0.1,
                 prices=prices, volumes=volumes, pct_change=pct)


def test_box_breakout_fires_on_breakout():
    prices = [100.0] * 24 + [103.0]
    volumes = [1_000_000] * 24 + [3_000_000]
    sigs = box_detect(make_stock(prices, volumes), RegimeType.BULL)
    assert len(sigs) == 1
    assert sigs[0].entry == 103.0
    assert sigs[0].stop < 103.0


def test_box_breakout_not_in_bear():
    prices = [100.0] * 24 + [103.0]
    volumes = [1_000_000] * 24 + [3_000_000]
    assert box_detect(make_stock(prices, volumes), RegimeType.BEAR) == []


def test_new_high_fires():
    prices = [100.0 + i for i in range(60)] + [161.0]
    volumes = [1_000_000] * 60 + [3_000_000]
    sigs = high_detect(make_stock(prices, volumes), RegimeType.BULL)
    assert len(sigs) == 1


def test_bollinger_rebound_fires():
    prices = [100.0 - i for i in range(40)]  # 100..61，长下跌 → RSI 低
    volumes = [1_000_000] * len(prices)
    # 先跌破下轨再收回；下轨本身受末两根影响，迭代到稳定即可
    for _ in range(10):
        _mid, _upper, lower = _bollinger(prices)
        prices[-2] = lower * 0.99   # 跌破下轨
        prices[-1] = lower * 1.01   # 收回
    sigs = rebound_detect(make_stock(prices, volumes), RegimeType.SIDEWAYS)
    assert len(sigs) == 1
    assert sigs[0].entry == prices[-1]


def test_kdj_cross_fires_on_low_cross():
    # 长跌后小幅反弹，K 在低位上穿 D（K 仍 < 20）
    prices = [100.0] * 5 + [100.0 - i * 2.0 for i in range(15)] + [40.0, 40.5, 41.0]
    volumes = [1_000_000] * len(prices)
    sigs = kdj_detect(make_stock(prices, volumes), RegimeType.SIDEWAYS)
    assert len(sigs) == 1
    assert sigs[0].strategy == "KDJ低位金叉"
    assert sigs[0].entry == prices[-1]


def test_volume_price_fires():
    prices = [100.0] * 20 + [103.0]
    volumes = [1_000_000] * 20 + [2_500_000]
    pct = [0.0] * 20 + [3.0]
    sigs = vp_detect(make_stock(prices, volumes, pct), RegimeType.BULL)
    assert len(sigs) == 1
    assert sigs[0].entry == 103.0


class FakeDetector:
    def __init__(self, signals):
        self.signals = signals

    def detect(self, stock, regime):
        # 仅当快照最新价等于 signal.entry 时触发，保证确定性单笔交易。
        price = stock.prices[-1]
        return [s for s in self.signals if s.entry == price]


def _up_stock(n=80):
    prices = [100.0 + i for i in range(n)]
    volumes = [1_000_000] * n
    return Stock(code="000001", name="测试", pe=10, pb=1.0, peg=1.0,
                 revenue_growth=0.1, profit_growth=0.1, roe=0.1, cashflow=0.1,
                 prices=prices, volumes=volumes)


def test_run_strategy_backtest_always_win():
    stock = _up_stock()
    # 快照 idx=60 时价 160.0 触发；随后 165.0 触及止盈（先于止损 150.0）。
    sig = StrategySignal("000001", "x", entry=160.0, stop=150.0, tp=165.0, score=50.0)
    result = run_strategy_backtest([stock], FakeDetector([sig]).detect, {}, [RegimeType.BULL] * 80, max_hold=10)
    assert result.trades == 1
    assert result.win_rate == 1.0
    assert result.profit_factor > 1.5
    assert result.expectancy > 0
    assert result.passed is True


def test_run_strategy_backtest_always_lose():
    # 前 61 根持平于 160.0（idx=60 触发），随后下跌跌破止损 → 确定性亏损。
    prices = [160.0] * 61 + [159.0 - i for i in range(19)]
    volumes = [1_000_000] * len(prices)
    stock = Stock(code="000001", name="测试", pe=10, pb=1.0, peg=1.0,
                  revenue_growth=0.1, profit_growth=0.1, roe=0.1, cashflow=0.1,
                  prices=prices, volumes=volumes)
    sig = StrategySignal("000001", "x", entry=160.0, stop=159.5, tp=165.0, score=50.0)
    result = run_strategy_backtest([stock], FakeDetector([sig]).detect, {}, [RegimeType.BULL] * 80, max_hold=10)
    assert result.trades == 1
    assert result.win_rate == 0.0
    assert result.expectancy < 0
    assert result.passed is False
