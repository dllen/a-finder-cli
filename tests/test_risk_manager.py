import pytest
from risk_manager import PositionConfig, RiskManager, calculate_trailing_stop
from market_regime import MarketRegime, RegimeType


def test_bull_trailing_stop():
    """牛市移动止损跟踪"""
    entry_price = 100.0
    current_price = 115.0  # 盈利15%
    result = calculate_trailing_stop(entry_price, current_price, trailing_pct=0.05)
    assert result == 109.25  # 锁定盈利5%


def test_bear_tighter_stop():
    """熊市更严格止损"""
    rm = RiskManager()
    config = rm.get_config(RegimeType.BEAR)
    assert config.stop_loss_pct == -0.05  # 熊市-5%止损


def test_profit_protection():
    """盈利保护：5%时保本"""
    entry_price = 100.0
    current_price = 105.0  # 盈利5%
    result = calculate_trailing_stop(entry_price, current_price, trailing_pct=0.05)
    assert result == entry_price  # 保本线


def test_bull_config():
    """牛市配置：更宽松的止损和更长的持仓"""
    rm = RiskManager()
    config = rm.get_config(RegimeType.BULL)
    assert config.stop_loss_pct == -0.08  # -8%
    assert config.time_exit_days == 30
    assert config.position_size == 0.15


def test_sideways_config():
    """震荡市配置：与熊市类似"""
    rm = RiskManager()
    config = rm.get_config(RegimeType.SIDEWAYS)
    assert config.stop_loss_pct == -0.05
    assert config.time_exit_days == 10


def test_trailing_stop_10_percent_profit():
    """盈利10%时锁定5%"""
    entry_price = 100.0
    highest_price = 110.0  # 盈利10%
    result = calculate_trailing_stop(entry_price, highest_price, trailing_pct=0.05)
    # stop_price = 110 * 0.95 = 104.5
    assert result == 104.5


def test_trailing_stop_max_lock():
    """最高锁定15%盈利"""
    entry_price = 100.0
    highest_price = 130.0  # 盈利30%，但最高只锁定15%
    result = calculate_trailing_stop(entry_price, highest_price, trailing_pct=0.05)
    # stop_price = 130 * 0.95 = 123.5
    assert result == 123.5


def test_signal_strength_adjustment():
    """信号强度调整仓位"""
    rm = RiskManager()
    # 最强信号
    config_strong = rm.get_config(RegimeType.BULL, signal_strength=1.0)
    assert config_strong.position_size == 0.15

    # 最弱信号
    config_weak = rm.get_config(RegimeType.BULL, signal_strength=0.0)
    assert config_weak.position_size == 0.075  # 0.15 * 0.5

    # 中等信号
    config_mid = rm.get_config(RegimeType.BULL, signal_strength=0.5)
    assert config_mid.position_size == pytest.approx(0.1125)  # 0.15 * 0.75


def test_max_position_cap():
    """仓位最大20%上限"""
    rm = RiskManager()
    # 使用超出范围的信号强度测试
    # position_size = 0.15 * (0.5 + 1.5 * 0.5) = 0.15 * 1.25 = 0.1875
    # 然后 min(0.20, 0.1875) = 0.1875 (未超过上限)
    # 要超过上限需要 signal_strength > 1.833
    config = rm.get_config(RegimeType.BULL, signal_strength=2.0)
    assert config.position_size == 0.20  # 被限制在20%


def test_should_stop_loss_fixed():
    """固定止损触发"""
    rm = RiskManager()
    config = rm.get_config(RegimeType.BULL)
    entry_price = 100.0
    current_price = 91.0  # -9%，超过-8%止损线
    highest_price = 100.0

    should_stop, reason = rm.should_stop_loss(entry_price, current_price, highest_price, config)
    assert should_stop is True
    assert "固定止损" in reason


def test_should_stop_loss_trailing():
    """移动止损触发"""
    rm = RiskManager()
    config = rm.get_config(RegimeType.BULL)
    entry_price = 100.0
    highest_price = 115.0  # 曾经涨到15%
    current_price = 109.0  # 跌破移动止损线109.25

    should_stop, reason = rm.should_stop_loss(entry_price, current_price, highest_price, config)
    assert should_stop is True
    assert "移动止损" in reason


def test_should_not_stop_loss():
    """正常持仓不触发止损"""
    rm = RiskManager()
    config = rm.get_config(RegimeType.BULL)
    entry_price = 100.0
    highest_price = 110.0
    current_price = 108.0  # 仍在移动止损线上方

    should_stop, reason = rm.should_stop_loss(entry_price, current_price, highest_price, config)
    assert should_stop is False
    assert reason == ""


def test_should_take_profit():
    """目标止盈触发"""
    rm = RiskManager()
    config = rm.get_config(RegimeType.BULL)
    entry_price = 100.0
    current_price = 121.0  # 超过20%目标

    should_tp, reason = rm.should_take_profit(entry_price, current_price, config)
    assert should_tp is True
    assert "目标止盈" in reason


def test_should_not_take_profit():
    """未到目标不止盈"""
    rm = RiskManager()
    config = rm.get_config(RegimeType.BULL)
    entry_price = 100.0
    current_price = 115.0  # 15%，未到20%目标

    should_tp, reason = rm.should_take_profit(entry_price, current_price, config)
    assert should_tp is False
