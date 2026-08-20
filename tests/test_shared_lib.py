"""Tests for shared_lib — PlanRow, params_hash, and risk_manager facade."""
import pytest

# --- Task 2: PlanRow + params_hash ------------------------------------------

def test_plan_row_defaults():
    row = PlanRow(
        code="600519",
        action="buy",
        plan_price=100.0,
        size_pct=0.1,
        stop_price=95.0,
        tp_price=110.0,
        rr_ratio=2.0,
        rationale={"score": 1.2},
        status="ok",
        reason="",
    )
    assert row.code == "600519"
    assert row.status == "ok"
    assert row.rationale == {"score": 1.2}


def test_params_hash_deterministic():
    a = params_hash({"a": 1, "b": 2})
    b = params_hash({"b": 2, "a": 1})
    assert a == b
    assert len(a) == 64  # sha256 hex


def test_params_hash_changes_with_value():
    assert params_hash({"x": 1}) != params_hash({"x": 2})


def test_plan_row_default_shares():
    from shared_lib.strategy import PlanRow
    r = PlanRow(code="600519", action="buy", plan_price=100.0, size_pct=0.1,
                stop_price=92.0, tp_price=120.0, rr_ratio=2.0)
    assert r.shares == 200


# --- Task 3: risk_manager facade + compute_plan_prices ----------------------

def test_risk_manager_re_exported():
    """shared_lib re-exports the canonical risk_manager.RiskManager."""
    from risk_manager import RiskManager as Real
    from shared_lib import RiskManager as ReExported
    assert ReExported is Real


def test_position_config_re_exported():
    from risk_manager import PositionConfig as Real
    from shared_lib import PositionConfig as ReExported
    assert ReExported is Real


def test_default_candidate_config_re_exported():
    from shared_lib import default_candidate_config
    cfg = default_candidate_config()
    # CandidateConfig has slope200_weight + momentum20_weight per real schema
    assert cfg.slope200_weight == 3.0
    assert cfg.momentum20_weight == 200.0


def test_compute_plan_prices_uses_pct():
    """stop_price and tp_price come from position_config pct fields."""
    from dataclasses import dataclass

    @dataclass
    class _Stub:
        stop_loss_pct: float
        profit_target_pct: float
        position_size: float = 0.15
        trailing_stop_pct: float = 0.05
        time_exit_days: int = 30

    cfg = _Stub(stop_loss_pct=-0.08, profit_target_pct=0.20)
    stop, tp = compute_plan_prices(100.0, cfg)
    assert stop == pytest.approx(92.0)
    assert tp == pytest.approx(120.0)


def test_compute_plan_prices_with_real_position_config():
    """End-to-end: RiskManager → PositionConfig → compute_plan_prices."""
    from market_regime import RegimeType
    rm = RiskManager()
    cfg = rm.get_config(RegimeType.BULL, signal_strength=1.0)
    stop, tp = compute_plan_prices(100.0, cfg)
    assert stop < 100.0
    assert tp > 100.0
    assert stop == pytest.approx(100.0 * (1.0 + cfg.stop_loss_pct))
    assert tp == pytest.approx(100.0 * (1.0 + cfg.profit_target_pct))


# --- Local imports (last, so test collection works even if module broken) ----

from shared_lib import (  # noqa: E402
    PlanRow,
    params_hash,
    compute_plan_prices,
    RiskManager,
)