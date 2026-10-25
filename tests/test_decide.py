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
        """有部分持仓时，计划买但没买的算漏买（空仓时归入待建仓，不算偏差）。"""
        from decide_service import compare_plan_vs_actual
        d = compare_plan_vs_actual(
            plan_rows=[self._plan(action="buy", code="600519"),
                       self._plan(action="buy", code="600036")],
            actual_rows=[self._actual(code="600519")],
            cb_rows=[], latest_prices={"600519": 100.0},
        )
        kinds = [x["kind"] for x in d["deviations"]]
        assert "missing_buy" in kinds
        missing = [x for x in d["deviations"] if x["kind"] == "missing_buy"]
        assert [x["code"] for x in missing] == ["600036"]

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
        # 用超配场景产生 danger 级偏差
        d = compare_plan_vs_actual(
            plan_rows=[],
            actual_rows=[self._actual(code=f"60000{i}") for i in range(9)],
            cb_rows=[], latest_prices={},
        )
        assert d["deviations"][0]["severity"] == "danger", "危险级应排最前"
        assert all(x["severity"] in ("info", "warn", "danger") for x in d["deviations"])


class TestPendingBuild:
    """空仓时「计划买入但未持有」应是待建仓清单，不是偏差。"""

    def _plan_buy(self, code="600519", shares=100, plan_price=100.0):
        return {
            "code": code, "name": "测试股", "action": "buy",
            "plan_price": plan_price, "shares": shares,
            "stop_price": 95.0, "tp_price": 110.0,
        }

    def test_empty_portfolio_returns_pending_build_not_deviations(self):
        from decide_service import compare_plan_vs_actual
        d = compare_plan_vs_actual(
            plan_rows=[self._plan_buy("600519"), self._plan_buy("600036")],
            actual_rows=[], cb_rows=[], latest_prices={},
        )
        assert d["deviations"] == [], "空仓时不应产生漏买偏差"
        assert len(d["pending_build"]) == 2
        codes = sorted(x["code"] for x in d["pending_build"])
        assert codes == ["600036", "600519"]
        first = d["pending_build"][0]
        assert first["shares"] == 100
        assert first["amount"] == 10000.0
        assert first["stop_price"] == 95.0
        assert first["tp_price"] == 110.0

    def test_partial_portfolio_still_reports_missing_buy(self):
        """有部分持仓时，计划买但没买的才算漏买。"""
        from decide_service import compare_plan_vs_actual
        d = compare_plan_vs_actual(
            plan_rows=[self._plan_buy("600519"), self._plan_buy("600036")],
            actual_rows=[{"code": "600519", "name": "测试股", "shares": 100,
                          "entry_price": 100.0, "stop_price": 95.0}],
            cb_rows=[], latest_prices={"600519": 100.0},
        )
        kinds = [x["kind"] for x in d["deviations"]]
        assert "missing_buy" in kinds
        missing = [x for x in d["deviations"] if x["kind"] == "missing_buy"]
        assert [x["code"] for x in missing] == ["600036"]
        assert d["pending_build"] == [], "已有持仓时不应有待建仓清单"

    def test_empty_portfolio_plan_count_still_reported(self):
        from decide_service import compare_plan_vs_actual
        d = compare_plan_vs_actual(
            plan_rows=[self._plan_buy("600519")],
            actual_rows=[], cb_rows=[], latest_prices={},
        )
        assert d["plan_count"] == 1
        assert d["actual_count"] == 0
        assert d["cash"] == 100000.0


class TestBuildPlanFromPicks:
    """从「买入信号」榜单构建 10w 建仓计划（报告第 3/4 章）。"""

    def _pick(self, rank, code, buy, stop=None, target=None, score=50.0):
        return {
            "rank": rank, "code": code, "name": f"股{code}",
            "buy": buy, "stop": stop or round(buy * 0.9, 2),
            "target": target or round(buy * 1.2, 2), "score": score,
        }

    def test_builds_at_most_max_positions(self):
        from decide_service import build_plan_from_picks
        picks = [self._pick(i, f"6000{i:02d}", 10.0) for i in range(1, 21)]
        r = build_plan_from_picks(picks)
        assert len(r["rows"]) == 8
        assert [x["rank"] for x in r["rows"]] == [1, 2, 3, 4, 5, 6, 7, 8]

    def test_target_amount_is_min_of_cap_and_even_split(self):
        """单票目标 = min(10% × 10w, 8w / 8) = min(10000, 10000) = 10000。"""
        from decide_service import build_plan_from_picks
        r = build_plan_from_picks([self._pick(1, "600000", 10.0)])
        assert r["target_amount"] == 10000.0
        assert r["rows"][0]["shares"] == 1000      # 10000/10 = 1000 股
        assert r["rows"][0]["amount"] == 10000.0

    def test_skips_high_price_stock_and_falls_through(self):
        """高价股一手超上限 → 跳过，顺延下一名。"""
        from decide_service import build_plan_from_picks
        picks = [
            self._pick(1, "301269", 108.20),   # 一手 10820 > 10000 → 跳过
            self._pick(2, "300760", 177.30),   # 一手 17730 > 10000 → 跳过
            self._pick(3, "600886", 15.68),    # 可买
        ]
        r = build_plan_from_picks(picks)
        assert [x["code"] for x in r["rows"]] == ["600886"]
        assert [x["code"] for x in r["skipped"]] == ["301269", "300760"]
        assert "超单票上限" in r["skipped"][0]["reason"]

    def test_shares_rounded_down_to_lot(self):
        from decide_service import build_plan_from_picks
        r = build_plan_from_picks([self._pick(1, "600000", 33.33)])
        # 10000/33.33 = 300.03 → 300 股
        assert r["rows"][0]["shares"] == 300
        assert r["rows"][0]["amount"] == round(300 * 33.33, 2)

    def test_respects_max_positions_when_skipping(self):
        """跳过若干只后仍只凑满 8 只。"""
        from decide_service import build_plan_from_picks
        picks = [self._pick(1, "301269", 200.0)]  # 高价跳过
        picks += [self._pick(i, f"6000{i:02d}", 10.0) for i in range(2, 15)]
        r = build_plan_from_picks(picks)
        assert len(r["rows"]) == 8
        assert len(r["skipped"]) == 1
        assert all(x["code"] != "301269" for x in r["rows"])

    def test_totals_within_stock_capital(self):
        from decide_service import build_plan_from_picks
        picks = [self._pick(i, f"6000{i:02d}", 10.0) for i in range(1, 9)]
        r = build_plan_from_picks(picks)
        assert r["planned_amount"] <= r["stock_capital"]
        assert r["remaining_stock_cash"] == r["stock_capital"] - r["planned_amount"]

    def test_orders_by_rank_not_input_order(self):
        from decide_service import build_plan_from_picks
        picks = [self._pick(3, "600003", 10.0),
                 self._pick(1, "600001", 10.0),
                 self._pick(2, "600002", 10.0)]
        r = build_plan_from_picks(picks)
        assert [x["code"] for x in r["rows"]] == ["600001", "600002", "600003"]


class TestStrategyPassthrough:
    """待建仓清单要带出每只票的策略（均线突破/动量突破/回调买入）。"""

    def _pick(self, rank, code, buy, strategy='回调买入', score=50.0):
        return {
            "rank": rank, "code": code, "name": f"股{code}",
            "buy": buy, "stop": round(buy * 0.9, 2),
            "target": round(buy * 1.2, 2), "score": score,
            "strategy": strategy,
        }

    def test_rows_include_strategy(self):
        from decide_service import build_plan_from_picks
        r = build_plan_from_picks([
            self._pick(1, "600000", 10.0, "动量突破"),
            self._pick(2, "600001", 12.0, "回调买入"),
        ])
        assert r["rows"][0]["strategy"] == "动量突破"
        assert r["rows"][1]["strategy"] == "回调买入"

    def test_skipped_include_strategy(self):
        from decide_service import build_plan_from_picks
        r = build_plan_from_picks([self._pick(1, "301269", 200.0, "均线突破")])
        assert r["rows"] == []
        assert r["skipped"][0]["strategy"] == "均线突破"

    def test_missing_strategy_is_none(self):
        from decide_service import build_plan_from_picks
        p = self._pick(1, "600000", 10.0)
        p.pop("strategy")
        r = build_plan_from_picks([p])
        assert r["rows"][0]["strategy"] is None
