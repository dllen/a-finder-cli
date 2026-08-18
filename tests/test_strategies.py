from strategies.base import StrategySignal, bollinger, kdj


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
