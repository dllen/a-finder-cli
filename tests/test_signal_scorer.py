import pytest
from signal_rules import SignalScore, score_signal_strength, calculate_position, ConfidenceLevel
from domain_models import Stock
from market_regime import MarketRegime, RegimeType


def test_strong_signal():
    """Strong signal: multiple indicators aligned"""
    stock = Stock(
        code="000001", name="平安", pe=10, pb=1.2, peg=0.8,
        revenue_growth=0.15, profit_growth=0.12, roe=0.15,
        cashflow=0.10, prices=[100 + i for i in range(250)], volumes=[1000000]*250
    )
    result = score_signal_strength(stock, {"买入": ["均线突破", "MACD金叉", "RSI超卖"]})
    assert result.confidence == ConfidenceLevel.STRONG
    assert result.total >= 75


def test_weak_signal():
    """Weak signal: only 1-2 indicators"""
    stock = Stock(
        code="000002", name="万科", pe=8, pb=0.9, peg=0.6,
        revenue_growth=0.05, profit_growth=0.03, roe=0.08,
        cashflow=0.05, prices=[100 - i*0.2 for i in range(250)], volumes=[800000]*250
    )
    result = score_signal_strength(stock, {"买入": ["均线突破"]})
    assert result.total < 50


def test_medium_signal():
    """Medium signal: moderate score"""
    stock = Stock(
        code="000003", name="测试", pe=15, pb=1.5, peg=1.0,
        revenue_growth=0.10, profit_growth=0.08, roe=0.12,
        cashflow=0.08, prices=[100 - i*0.1 for i in range(250)], volumes=[900000]*250
    )
    result = score_signal_strength(stock, {"买入": ["均线突破", "MACD金叉"]})
    assert result.confidence == ConfidenceLevel.MEDIUM
    assert result.total >= 50
    assert result.total < 75


def test_no_signal():
    """No signals: should return NONE confidence"""
    stock = Stock(
        code="000004", name="无信号", pe=20, pb=2.0, peg=1.5,
        revenue_growth=0.02, profit_growth=0.01, roe=0.05,
        cashflow=0.02, prices=[100 for i in range(250)], volumes=[500000]*250
    )
    result = score_signal_strength(stock, {})
    assert result.confidence == ConfidenceLevel.NONE
    assert result.total == 0
    assert result.indicator_count == 0


def test_position_calculation_bull_strong():
    """Position calculation: strong signal + bull market = heavy position"""
    strong = SignalScore(total=85, confidence=ConfidenceLevel.STRONG, indicator_count=4,
                         spatial_score=30, historical_winrate=20, momentum_score=20)
    bull = MarketRegime(regime=RegimeType.BULL, confidence=0.8, tech_score=0.5,
                        index_score=0.6, fundamental_score=0.4)
    pos = calculate_position(strong, bull)
    assert pos["position_size"] >= 0.15  # Strong signal + bull = heavy position


def test_position_calculation_bear_weak():
    """Position calculation: weak signal + bear market = light position"""
    weak = SignalScore(total=30, confidence=ConfidenceLevel.WEAK, indicator_count=1,
                       spatial_score=10, historical_winrate=10, momentum_score=10)
    bear = MarketRegime(regime=RegimeType.BEAR, confidence=0.7, tech_score=-0.5,
                        index_score=-0.6, fundamental_score=-0.3)
    pos = calculate_position(weak, bear)
    assert pos["position_size"] <= 0.10  # Weak signal + bear = light position


def test_position_calculation_sideways():
    """Position calculation: sideways market defaults"""
    medium = SignalScore(total=55, confidence=ConfidenceLevel.MEDIUM, indicator_count=2,
                         spatial_score=20, historical_winrate=15, momentum_score=15)
    sideways = MarketRegime(regime=RegimeType.SIDEWAYS, confidence=0.5, tech_score=0.0,
                            index_score=0.0, fundamental_score=0.0)
    pos = calculate_position(medium, sideways)
    assert "position_size" in pos
    assert "stop_loss_pct" in pos
    assert "trailing_stop_pct" in pos
    assert "time_exit_days" in pos
    # Sideways should have moderate stop loss and time exit
    assert pos["stop_loss_pct"] == -0.05
    assert pos["time_exit_days"] == 10


def test_confidence_level_enum():
    """Test ConfidenceLevel enum values"""
    assert ConfidenceLevel.STRONG.value == "strong"
    assert ConfidenceLevel.MEDIUM.value == "medium"
    assert ConfidenceLevel.WEAK.value == "weak"
    assert ConfidenceLevel.NONE.value == "none"


def test_signal_score_dataclass():
    """Test SignalScore dataclass fields"""
    score = SignalScore(
        total=75.0,
        indicator_count=3,
        spatial_score=25.0,
        historical_winrate=20.0,
        momentum_score=20.0,
        confidence=ConfidenceLevel.STRONG
    )
    assert score.total == 75.0
    assert score.indicator_count == 3
    assert score.spatial_score == 25.0
    assert score.historical_winrate == 20.0
    assert score.momentum_score == 20.0
    assert score.confidence == ConfidenceLevel.STRONG
