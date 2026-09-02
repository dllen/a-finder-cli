import pytest

from shared_lib.strategy import size_shares, LOT_SIZE


def test_size_shares_rounds_down_to_lot():
    # 10W * 12.5% = 12500 元，买 100 元/股 → 125 股 → 向下取整 1 手 = 100 股
    assert size_shares(100000, 0.125, 100.0) == 100


def test_size_shares_multiple_lots():
    # 10W * 12.5% = 12500 元，买 50 元/股 → 250 股 → 2 手 = 200 股
    assert size_shares(100000, 0.125, 50.0) == 200


def test_size_shares_zero_when_below_one_lot():
    # 5W * 10% = 5000 元，买 1400 元/股 → 不足一手 → 0
    assert size_shares(50000, 0.10, 1400.0) == 0


def test_size_shares_zero_on_non_positive_inputs():
    assert size_shares(0, 0.10, 100.0) == 0
    assert size_shares(100000, 0.0, 100.0) == 0
    assert size_shares(100000, 0.10, 0.0) == 0
    assert size_shares(-100000, 0.10, 100.0) == 0


def test_lot_size_is_100():
    assert LOT_SIZE == 100
