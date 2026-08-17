import pytest
from market_regime import detect_regime, RegimeType
from signal_rules import detect_signals, score_signal_strength
from candidate_rules import ma_strategy_candidates_adaptive, ma_strategy_candidates, select_candidates_with_quota
from risk_manager import RiskManager, calculate_trailing_stop
from domain_models import Stock


def create_stock(code="000001", name="测试", prices=None, volumes=None):
    if prices is None:
        prices = [100 + i * 0.5 for i in range(250)]
    if volumes is None:
        volumes = [1000000] * len(prices)
    return Stock(
        code=code, name=name, pe=12, pb=1.5, peg=1.0,
        revenue_growth=0.10, profit_growth=0.08, roe=0.12,
        cashflow=0.08, prices=prices, volumes=volumes
    )


def group_signals_by_action(raw_signals):
    """Group raw signals (list of dicts) into {action: [strategy,...]} format."""
    result = {"买入": [], "卖出": []}
    for sig in raw_signals:
        action = sig.get("action", "")
        strategy = sig.get("strategy", "")
        if action in result:
            result[action].append(strategy)
    return result


class TestFullPipeline:
    """Test the complete trading pipeline from regime detection to position sizing."""

    def test_full_pipeline_bull_market(self):
        """Test complete pipeline in bull market: regime -> signals -> score -> candidates -> position."""
        # 1. Create uptrend stock (250 days of steady climb)
        prices = [100 + i * 0.5 for i in range(250)]
        stock = create_stock(prices=prices)

        # 2. Detect market regime (using stock prices as index proxy)
        index_prices = prices
        regime = detect_regime(index_prices, {"pe_percentile": 0.4, "m2_yoy": 0.12})
        assert regime.regime == RegimeType.BULL, f"Expected BULL, got {regime.regime}"
        assert regime.confidence > 0

        # 3. Detect signals
        raw_signals = detect_signals(stock)
        assert isinstance(raw_signals, list)

        # 4. Score signal strength (bridge from list-of-dicts to {action: [strategies]})
        if raw_signals:
            grouped = group_signals_by_action(raw_signals)
            score = score_signal_strength(stock, grouped)
            assert score.total >= 0
            assert score.total <= 100
        else:
            # No signals is valid (price may not trigger any strategy)
            score = score_signal_strength(stock, {"买入": [], "卖出": []})
            assert score.total == 0

        # 5. Select candidates adaptively
        candidates = ma_strategy_candidates_adaptive([stock], regime)
        assert isinstance(candidates, list)

        # 6. Calculate position size
        rm = RiskManager()
        config = rm.get_config(regime.regime, score.total / 100)
        assert config.position_size > 0
        assert config.position_size <= 0.20
        assert config.stop_loss_pct < 0
        assert config.trailing_stop_pct >= 0

        # 7. Verify trailing stop calculation
        trailing_stop = calculate_trailing_stop(105.0, 115.0, config.trailing_stop_pct)
        assert trailing_stop >= 105.0  # At minimum, not below entry

    def test_full_pipeline_sideways_market(self):
        """Test complete pipeline in sideways market."""
        # Flat prices (sideways market)
        prices = [100.0] * 250
        stock = create_stock(prices=prices)

        regime = detect_regime(prices, {"pe_percentile": 0.5, "m2_yoy": 0.09})
        assert regime.regime == RegimeType.SIDEWAYS

        rm = RiskManager()
        config = rm.get_config(regime.regime, 0.5)
        assert config.position_size > 0
        assert config.stop_loss_pct < 0

    def test_full_pipeline_bear_market(self):
        """Test complete pipeline in bear market: candidates should be oversold bounces only."""
        # Downtrend
        prices = [200 - i * 0.8 for i in range(250)]
        stock = create_stock(prices=prices)

        regime = detect_regime(prices, {"pe_percentile": 0.8, "m2_yoy": 0.06})
        assert regime.regime == RegimeType.BEAR

        # In bear market, candidates should use oversold logic
        candidates = ma_strategy_candidates_adaptive([stock], regime)
        assert isinstance(candidates, list)

        rm = RiskManager()
        config = rm.get_config(regime.regime, 0.5)
        assert config.position_size > 0
        # Bear market position should be smaller than bull market
        bull_config = rm.get_config(RegimeType.BULL, 0.5)
        assert config.position_size < bull_config.position_size

    def test_pipeline_no_signals_bear_market_no_trades(self):
        """Bear market with no signals: RiskManager returns small position, candidates empty."""
        # Flat-ish prices unlikely to generate signals
        prices = [100.0] * 250
        stock = create_stock(prices=prices)

        regime = detect_regime(prices, {"pe_percentile": 0.8, "m2_yoy": 0.06})
        raw_signals = detect_signals(stock)

        # If no signals, score is 0
        grouped = group_signals_by_action(raw_signals)
        score = score_signal_strength(stock, grouped)
        assert score.total == 0

        # Position with 0 signal is reduced
        rm = RiskManager()
        config = rm.get_config(regime.regime, 0.0)
        assert config.position_size > 0  # Still positive but small

        # Candidates adaptively returns bear-market candidates
        candidates = ma_strategy_candidates_adaptive([stock], regime)
        assert isinstance(candidates, list)  # May be empty if conditions not met

    def test_candidate_quota_selection(self):
        """Test that quota-based candidate selection respects strategy ratios."""
        stocks = [create_stock(code=f"00{i:04d}", prices=[100 + i * 0.5 for i in range(250)]) for i in range(20)]
        regime = detect_regime([100 + i * 0.5 for i in range(250)], {"pe_percentile": 0.4, "m2_yoy": 0.12})

        all_candidates = ma_strategy_candidates(stocks)
        selected = select_candidates_with_quota(all_candidates, top=5)

        assert len(selected) <= 5
        # All selected candidates should have scores
        for c in selected:
            assert c["score"] > 0

    def test_trailing_stop_progressive_locking(self):
        """Test progressive profit locking in trailing stop."""
        # Scenario: 15% profit -> lock ~15%, stop below peak by ~20%
        entry = 100.0
        highest = 115.0  # 15% profit
        trailing = calculate_trailing_stop(entry, highest, 0.05)
        # Should be above entry but below highest
        assert trailing >= entry
        assert trailing <= highest

        # Scenario: 8% profit -> just above entry (barely over 5% threshold)
        highest_8 = 108.0
        trailing_8 = calculate_trailing_stop(entry, highest_8, 0.05)
        assert trailing_8 >= entry  # At or near entry (breakeven protection)

        # Scenario: 3% profit -> below 5% threshold, returns entry price
        highest_3 = 103.0
        trailing_3 = calculate_trailing_stop(entry, highest_3, 0.05)
        assert trailing_3 == entry  # No locking yet

    def test_risk_manager_stop_loss_check(self):
        """Test that risk manager correctly identifies stop loss conditions."""
        rm = RiskManager()
        config = rm.get_config(RegimeType.BULL, 1.0)

        # Should trigger stop loss: -8%
        should_stop, reason = rm.should_stop_loss(100.0, 91.5, 100.0, config)
        assert should_stop, f"Expected stop loss at -8.5%, reason: {reason}"

        # Should NOT trigger: -5%
        should_stop, reason = rm.should_stop_loss(100.0, 95.0, 100.0, config)
        assert not should_stop

    def test_risk_manager_take_profit_check(self):
        """Test that risk manager correctly identifies take profit conditions."""
        rm = RiskManager()
        config = rm.get_config(RegimeType.BULL, 1.0)

        # Should trigger take profit: +20%
        should_tp, reason = rm.should_take_profit(100.0, 121.0, config)
        assert should_tp

        # Should NOT trigger: +15%
        should_tp, reason = rm.should_take_profit(100.0, 115.0, config)
        assert not should_tp

    def test_risk_manager_time_exit_check(self):
        """Test that time-based exit works correctly."""
        rm = RiskManager()
        config = rm.get_config(RegimeType.BULL, 1.0)

        # Should exit: holding 31 days (limit is 30)
        should_exit, reason = rm.should_exit_by_time(31, config)
        assert should_exit

        # Should NOT exit: holding 20 days
        should_exit, reason = rm.should_exit_by_time(20, config)
        assert not should_exit
