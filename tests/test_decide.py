"""10w 决策台：实际持仓仓储 + 对照判定逻辑。"""
import os
import tempfile

import pytest


@pytest.fixture
def db():
    from db_repository import open_db
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = open_db(path)
    conn.close()
    yield path
    os.unlink(path)


class TestRealPositionRepository:

    def test_upsert_and_get_stock_position(self, db):
        from db_repository import open_db
        from real_position_repository import (
            delete_real_position, get_real_positions, upsert_real_position,
        )
        conn = open_db(db)
        try:
            upsert_real_position(conn, {
                "code": "600519", "name": "贵州茅台", "shares": 100,
                "entry_price": 1500.0, "stop_price": 1400.0,
                "entry_date": "2026-10-20", "note": "",
            })
            rows = get_real_positions(conn)
            assert len(rows) == 1
            assert rows[0]["code"] == "600519"
            assert rows[0]["shares"] == 100
            assert rows[0]["entry_price"] == 1500.0

            # 再次写入应更新而非新增
            upsert_real_position(conn, {
                "code": "600519", "name": "贵州茅台", "shares": 200,
                "entry_price": 1520.0, "stop_price": None,
                "entry_date": "2026-10-20", "note": "加仓",
            })
            rows = get_real_positions(conn)
            assert len(rows) == 1
            assert rows[0]["shares"] == 200
            assert rows[0]["stop_price"] is None

            delete_real_position(conn, "600519")
            assert get_real_positions(conn) == []
        finally:
            conn.close()

    def test_upsert_requires_code_shares_entry_price(self, db):
        from db_repository import open_db
        from real_position_repository import upsert_real_position
        conn = open_db(db)
        try:
            with pytest.raises(ValueError):
                upsert_real_position(conn, {"code": "", "shares": 10, "entry_price": 1.0})
            with pytest.raises(ValueError):
                upsert_real_position(conn, {"code": "600519", "shares": 0, "entry_price": 1.0})
            with pytest.raises(ValueError):
                upsert_real_position(conn, {"code": "600519", "shares": 10, "entry_price": None})
        finally:
            conn.close()

    def test_cb_position_roundtrip(self, db):
        from db_repository import open_db
        from real_position_repository import (
            delete_real_cb_position, get_real_cb_positions, upsert_real_cb_position,
        )
        conn = open_db(db)
        try:
            upsert_real_cb_position(conn, {
                "code": "113059", "name": "转债A", "shares": 50,
                "entry_price": 108.5, "stop_price": 99.8,
                "entry_date": "2026-10-20", "note": "",
            })
            rows = get_real_cb_positions(conn)
            assert len(rows) == 1
            assert rows[0]["code"] == "113059"
            assert rows[0]["shares"] == 50

            delete_real_cb_position(conn, "113059")
            assert get_real_cb_positions(conn) == []
        finally:
            conn.close()


class TestConfig:

    def test_portfolio_10w_config(self):
        from config import PORTFOLIO_10W
        assert PORTFOLIO_10W["total_capital"] == 100000.0
        assert PORTFOLIO_10W["stock_ratio"] == 0.8
        assert PORTFOLIO_10W["cb_ratio"] == 0.2
        assert PORTFOLIO_10W["max_positions"] == 8
        assert PORTFOLIO_10W["max_single_weight"] == 0.10
        assert PORTFOLIO_10W["max_stock_exposure"] == 0.75
        assert PORTFOLIO_10W["cb_capital"] == 20000.0
        assert PORTFOLIO_10W["max_cb_positions"] == 3

class TestCompare:

    def _plan(self, action="buy", code="600519", shares=100, plan_price=100.0,
              stop=95.0, tp=110.0):
        return {
            "code": code, "name": "测试股", "action": action,
            "plan_price": plan_price, "shares": shares,
            "stop_price": stop, "tp_price": tp,
        }

    def _actual(self, code="600519", shares=100, entry_price=100.0, stop=95.0):
        return {
            "code": code, "name": "测试股", "shares": shares,
            "entry_price": entry_price, "stop_price": stop,
        }

    def test_no_deviations_when_perfect_match(self):
        from decide_service import compare_plan_vs_actual
        d = compare_plan_vs_actual(
            plan_rows=[self._plan()], actual_rows=[self._actual()],
            cb_rows=[], latest_prices={"600519": 100.0},
        )
        assert d["deviations"] == []
        assert d["stock_used"] == 10000.0
        assert d["cb_used"] == 0.0
        assert d["cash"] == 90000.0

    def test_detects_missing_buy(self):
        from decide_service import compare_plan_vs_actual
        d = compare_plan_vs_actual(
            plan_rows=[self._plan(action="buy")], actual_rows=[],
            cb_rows=[], latest_prices={},
        )
        kinds = [x["kind"] for x in d["deviations"]]
        assert "missing_buy" in kinds

    def test_detects_unplanned_position(self):
        from decide_service import compare_plan_vs_actual
        d = compare_plan_vs_actual(
            plan_rows=[], actual_rows=[self._actual(code="000001")],
            cb_rows=[], latest_prices={},
        )
        kinds = [x["kind"] for x in d["deviations"]]
        assert "unplanned" in kinds

    def test_detects_too_many_positions(self):
        from decide_service import compare_plan_vs_actual
        actual = [self._actual(code=f"60000{i}") for i in range(9)]
        d = compare_plan_vs_actual(
            plan_rows=[], actual_rows=actual, cb_rows=[], latest_prices={},
        )
        kinds = [x["kind"] for x in d["deviations"]]
        assert "too_many_positions" in kinds

    def test_detects_single_weight_over_limit(self):
        from decide_service import compare_plan_vs_actual
        # 1 只票占 15000 / 100000 = 15% > 10%
        d = compare_plan_vs_actual(
            plan_rows=[], actual_rows=[self._actual(shares=150, entry_price=100.0)],
            cb_rows=[], latest_prices={},
        )
        kinds = [x["kind"] for x in d["deviations"]]
        assert "single_weight_over" in kinds

    def test_detects_missing_stop_loss(self):
        from decide_service import compare_plan_vs_actual
        d = compare_plan_vs_actual(
            plan_rows=[self._plan()], actual_rows=[self._actual(stop=None)],
            cb_rows=[], latest_prices={"600519": 100.0},
        )
        kinds = [x["kind"] for x in d["deviations"]]
        assert "missing_stop" in kinds

    def test_detects_stop_loss_breached(self):
        from decide_service import compare_plan_vs_actual
        d = compare_plan_vs_actual(
            plan_rows=[self._plan()], actual_rows=[self._actual(stop=95.0)],
            cb_rows=[], latest_prices={"600519": 90.0},
        )
        kinds = [x["kind"] for x in d["deviations"]]
        assert "stop_breached" in kinds

    def test_detects_total_exposure_over_limit(self):
        from decide_service import compare_plan_vs_actual
        # 8 只票各 10000 元 = 80000 > 75000
        actual = [self._actual(code=f"60000{i}", shares=100, entry_price=100.0)
                  for i in range(8)]
        d = compare_plan_vs_actual(
            plan_rows=[], actual_rows=actual, cb_rows=[], latest_prices={},
        )
        kinds = [x["kind"] for x in d["deviations"]]
        assert "exposure_over" in kinds

    def test_detects_cb_over_allocation(self):
        from decide_service import compare_plan_vs_actual
        cb = [{"code": "113059", "name": "转债A", "shares": 200,
               "entry_price": 110.0, "stop_price": None}]
        d = compare_plan_vs_actual(
            plan_rows=[], actual_rows=[], cb_rows=cb, latest_prices={},
        )
        kinds = [x["kind"] for x in d["deviations"]]
        assert "cb_over" in kinds

    def test_detects_take_profit_hit(self):
        from decide_service import compare_plan_vs_actual
        d = compare_plan_vs_actual(
            plan_rows=[self._plan(tp=110.0)], actual_rows=[self._actual()],
            cb_rows=[], latest_prices={"600519": 115.0},
        )
        kinds = [x["kind"] for x in d["deviations"]]
        assert "tp_hit" in kinds

    def test_duplicate_plan_rows_deduped(self):
        """同一 code 的重复 plan 行只算一次（10W 档位历史数据有重复）。"""
        from decide_service import compare_plan_vs_actual
        plan = [self._plan(action="buy"), self._plan(action="buy"),
                self._plan(action="buy")]
        d = compare_plan_vs_actual(
            plan_rows=plan, actual_rows=[self._actual()],
            cb_rows=[], latest_prices={"600519": 100.0},
        )
        missing = [x for x in d["deviations"] if x["kind"] == "missing_buy"]
        assert missing == []
        assert d["plan_count"] == 1

    def test_cash_remaining_calculation(self):
        from decide_service import compare_plan_vs_actual
        actual = [self._actual(shares=100, entry_price=100.0)]
        cb = [{"code": "113059", "name": "转债A", "shares": 50,
               "entry_price": 110.0, "stop_price": None}]
        d = compare_plan_vs_actual(
            plan_rows=[], actual_rows=actual, cb_rows=cb, latest_prices={},
        )
        assert d["stock_used"] == 10000.0
        assert d["cb_used"] == 5500.0
        assert d["cash"] == 100000.0 - 10000.0 - 5500.0

    def test_deviation_severity_levels(self):
        from decide_service import compare_plan_vs_actual
        d = compare_plan_vs_actual(
            plan_rows=[self._plan(action="buy")], actual_rows=[],
            cb_rows=[], latest_prices={},
        )
        assert d["deviations"][0]["severity"] in ("info", "warn", "danger")