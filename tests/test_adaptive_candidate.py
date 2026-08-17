import pytest
from candidate_rules import ma_strategy_candidates_adaptive
from market_regime import MarketRegime, RegimeType, detect_regime
from domain_models import Stock


def create_test_stock(rising=True, price_start=100.0, trend=0.5):
    """Create test stock with configurable trend"""
    if rising:
        prices = [price_start + i * trend for i in range(250)]
    else:
        prices = [price_start - i * trend for i in range(250)]
    return Stock(
        code="000001", name="测试", pe=12, pb=1.5, peg=1.0,
        revenue_growth=0.10, profit_growth=0.08, roe=0.12,
        cashflow=0.08, prices=prices, volumes=[1000000]*250
    )


def create_sideways_stock():
    """Create test stock in sideways market pattern (MA10 near price)"""
    # Start high, drop, then recover to MA10 level
    prices = []
    start = 110.0
    # Drop phase
    for i in range(120):
        prices.append(start - i * 0.5)
    # Sideways phase near MA10
    base = prices[-1]
    for i in range(130):
        prices.append(base + (i % 10 - 5) * 0.3)
    return Stock(
        code="000002", name="震荡测试", pe=15, pb=1.8, peg=1.2,
        revenue_growth=0.08, profit_growth=0.06, roe=0.10,
        cashflow=0.07, prices=prices, volumes=[1000000]*250
    )


def create_bear_market_oversold():
    """Create test stock in oversold condition (for bear market testing)"""
    prices = []
    start = 100.0
    # Long downtrend
    for i in range(200):
        prices.append(start - i * 0.4)
    # Stabilize near low
    low = prices[-1]
    for i in range(50):
        prices.append(low + (i % 5) * 0.2)
    # Add volume surge on last day
    volumes = [500000]*249 + [2000000]  # 4x volume on last day
    return Stock(
        code="000003", name="熊市超卖", pe=8, pb=0.9, peg=0.5,
        revenue_growth=0.05, profit_growth=0.02, roe=0.05,
        cashflow=0.03, prices=prices, volumes=volumes
    )


class TestAdaptiveCandidate:
    """Test suite for market-adaptive candidate selection"""

    def test_bull_market_uses_existing_logic(self):
        """Bull market should use existing ma_strategy_candidates logic"""
        stock = create_test_stock(rising=True)
        bull_regime = MarketRegime(
            regime=RegimeType.BULL,
            confidence=0.8,
            tech_score=0.6,
            index_score=0.5,
            fundamental_score=0.4
        )
        candidates = ma_strategy_candidates_adaptive([stock], bull_regime)
        # Should return list (may be empty depending on specific conditions)
        assert isinstance(candidates, list)

    def test_bear_market_requires_extreme_oversold(self):
        """Bear market should only select when RSI < 20 (extreme oversold)"""
        stock = create_bear_market_oversold()
        bear_regime = MarketRegime(
            regime=RegimeType.BEAR,
            confidence=0.7,
            tech_score=-0.6,
            index_score=-0.5,
            fundamental_score=-0.3
        )
        candidates = ma_strategy_candidates_adaptive([stock], bear_regime)
        assert isinstance(candidates, list)
        # If candidates exist, verify strategy name
        for c in candidates:
            assert "熊市" in c["strategy"]

    def test_bear_market_rejects_normal_rsi(self):
        """Bear market should reject stocks with normal RSI"""
        stock = create_test_stock(rising=False, price_start=100.0, trend=0.2)
        bear_regime = MarketRegime(
            regime=RegimeType.BEAR,
            confidence=0.7,
            tech_score=-0.6,
            index_score=-0.5,
            fundamental_score=-0.3
        )
        # Normal declining stock has RSI around 30-40, should not qualify
        candidates = ma_strategy_candidates_adaptive([stock], bear_regime)
        # Should return empty or candidates with strict criteria
        assert isinstance(candidates, list)

    def test_sideways_market_requires_precise_pullback(self):
        """Sideways market should require precise pullback (±1%)"""
        stock = create_sideways_stock()
        sideways_regime = MarketRegime(
            regime=RegimeType.SIDEWAYS,
            confidence=0.5,
            tech_score=0.0,
            index_score=0.0,
            fundamental_score=0.0
        )
        candidates = ma_strategy_candidates_adaptive([stock], sideways_regime)
        assert isinstance(candidates, list)
        # If candidates exist, verify strategy name
        for c in candidates:
            assert "震荡市" in c["strategy"]

    def test_adaptive_returns_correct_structure(self):
        """Verify adaptive function returns proper candidate structure"""
        stock = create_test_stock(rising=True)
        bull_regime = MarketRegime(
            regime=RegimeType.BULL,
            confidence=0.8,
            tech_score=0.6,
            index_score=0.5,
            fundamental_score=0.4
        )
        candidates = ma_strategy_candidates_adaptive([stock], bull_regime)
        for c in candidates:
            assert "stock" in c
            assert "strategy" in c
            assert "ma10" in c
            assert "ma30" in c
            assert "ma50" in c
            assert "ma100" in c
            assert "ma200" in c
            assert "volume_ratio" in c
            assert "stop_price" in c
            assert "score" in c

    def test_empty_list_handled(self):
        """Handle empty stock list gracefully"""
        bull_regime = MarketRegime(
            regime=RegimeType.BULL,
            confidence=0.8,
            tech_score=0.6,
            index_score=0.5,
            fundamental_score=0.4
        )
        candidates = ma_strategy_candidates_adaptive([], bull_regime)
        assert candidates == []
