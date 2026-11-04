"""腾讯 newfqkline（bfq，不复权）源的口径、单位与字段完整性测试。

**为什么有这个源（2026-11-04 实测）**
--------------------------------------
东财 push2his（K 线接口）在本机**不可达**（RemoteDisconnected），
备源链退到新浪 getKLineData。但新浪**只给 OHLC + 成交量**，
amount / turnover / pct_change 全缺 => 落库的这三个字段全是 0.0：

    「量价齐升」策略的 pct_change 判据与回测的成交额口径**静默失效**
    （不报错，算出来全错）。

腾讯 newfqkline 的 bfq 口径与东财 fqt=0 **完全一致**，
且这三个字段齐全 => 可以当不复权主源用。

**这些测试锁住三件事**
----------------------
1. **口径**：tencent_bfq 登记为 none（不复权），不是 qfq。
2. **单位**：成交量按**板块**不同（主板是「手」，科创板 688xxx 是「股」），
   成交额是「万元」=> 落库统一成「手 / 元」。搞错就是 100 倍偏差。
3. **字段**：pct_change 必须由前收盘价算出（腾讯自己不给）。
"""
import pytest

from unittest.mock import MagicMock, patch

from data_providers import (
    BASIS_NONE,
    PREFERRED_BASIS,
    SOURCE_BASIS,
    SourceHealthTracker,
    fetch_daily_kline_smart,
    fetch_daily_kline_tencent_bfq,
    source_basis,
)


# ---------------------------------------------------------------------------
# 1. 口径登记
# ---------------------------------------------------------------------------

class TestTencentBfqBasis:

    def test_registered_as_none(self):
        """bfq 必须登记成「不复权」——它替代的是东财 fqt=0 的位置。"""
        assert SOURCE_BASIS["tencent_bfq"] == BASIS_NONE
        assert source_basis("tencent_bfq") == BASIS_NONE

    def test_is_preferred_basis(self):
        """必须是首选口径组的一员，否则东财不可达时根本轮不到它。"""
        assert SOURCE_BASIS["tencent_bfq"] == PREFERRED_BASIS

    def test_distinct_from_qfq_tencent(self):
        """两个腾讯源口径不同，不能互相顶替。

        tencent（fqkline + qfq）是前复权，tencent_bfq（newfqkline + bfq）
        是不复权。口径错标 = 2026-10-29 事故（假跳空污染 MA/动量/回测）。
        """
        assert SOURCE_BASIS["tencent"] != SOURCE_BASIS["tencent_bfq"]
        assert SOURCE_BASIS["tencent"] == "qfq"

    def test_preferred_group_contains_bfq(self):
        preferred = {s for s, b in SOURCE_BASIS.items() if b == PREFERRED_BASIS}
        assert "tencent_bfq" in preferred
        assert "tencent" not in preferred


# ---------------------------------------------------------------------------
# 2. 备源链：同口径下 bfq 优先于新浪
# ---------------------------------------------------------------------------

class TestTencentBfqInFallbackChain:

    def _tracker(self, available):
        tracker = SourceHealthTracker()
        for s in tracker.SOURCES:
            tracker._health[s]["available"] = s in available
        tracker._ordered = (
            [s for s in tracker.SOURCES if s in available]
            + [s for s in tracker.SOURCES if s not in available]
        )
        return tracker

    def test_preferred_over_sina_when_eastmoney_down(self):
        """东财挂 + 新浪可用 + 腾讯 bfq 可用 => 必须选 bfq（字段更全）。"""
        tracker = self._tracker(["tencent_bfq", "sina", "tencent"])
        with patch("data_providers.fetch_daily_kline_eastmoney") as mock_em, \
             patch("data_providers.fetch_daily_kline_tencent_bfq") as mock_bfq, \
             patch("data_providers.fetch_daily_kline_sina") as mock_sina:
            mock_em.side_effect = ConnectionError("RemoteDisconnected")
            mock_bfq.return_value = ([MagicMock()], "平安银行")
            mock_sina.return_value = ([MagicMock()], "平安银行")
            rows, name, source, basis = fetch_daily_kline_smart(
                "000001", "20261101", "20261104", tracker
            )

        assert source == "tencent_bfq"
        assert basis == BASIS_NONE
        mock_sina.assert_not_called()

    def test_basis_stamped_on_rows(self):
        """口径必须盖到每一行上。

        PriceRow.adj_basis 的默认值是 "none"（不复权）=> 不盖的话
        前复权行会带着 "none" 落库 —— **看起来**检测过了，实际已经混了。
        """
        tracker = self._tracker(["tencent_bfq"])
        row = MagicMock()
        row.adj_basis = "none"
        with patch("data_providers.fetch_daily_kline_tencent_bfq") as mock_bfq:
            mock_bfq.return_value = ([row], "平安银行")
            rows, name, source, basis = fetch_daily_kline_smart(
                "000001", "20261101", "20261104", tracker
            )

        assert basis == BASIS_NONE
        assert row.adj_basis == BASIS_NONE


# ---------------------------------------------------------------------------
# 3. 单位换算（最关键：搞错就是 100 倍偏差）
# ---------------------------------------------------------------------------

class TestTencentBfqUnits:
    """腾讯 newfqkline 的行格式：

    [日期, 开, 收, 高, 低, 量, {}, 换手率%, 成交额(万元), ?, ?]

    ⚠️ **成交量单位按板块不同**：主板/创业板（000/002/300/60）是「手」，
    科创板（688xxx）是「股」。库里统一存「手」=> 688 要 /100。
    """

    def _entry(self, date, close, volume, turnover, amount_wan):
        return [
            date, "11.00", close, "11.50", "10.50", volume,
            {}, turnover, amount_wan, "0.00", "0.00",
        ]

    def _fetch(self, code, entries):
        sym = ("sh" if code.startswith(("60", "688")) else "sz") + code
        payload = {"day": entries, "qt": {sym: ["51", "平安银行", code]}}
        with patch("data_providers.fetch_url_json",
                   return_value={"data": {sym: payload}}):
            rows, name = fetch_daily_kline_tencent_bfq(
                code, "2026-11-01", "2026-11-04"
            )
        return rows

    def test_main_board_volume_is_hands(self):
        """主板：腾讯给的就是「手」=> 原样落库，不换算。"""
        rows = self._fetch(
            "000001",
            [self._entry("2026-11-04", "11.74", "1437906.00", "0.74", "168918.40")],
        )
        assert len(rows) == 1
        assert rows[0].volume == pytest.approx(1437906.0)

    def test_star_board_volume_divided_by_100(self):
        """科创板 688xxx：腾讯给的是「股」=> 必须 /100 才是「手」。

        实测 688008 的 46199662（股）= 461997 手（东财 f47 同值）。
        漏掉这个换算 => 成交量大 100 倍 => 换手率/量价指标全错。
        """
        rows = self._fetch(
            "688008",
            [self._entry("2026-11-04", "167.82", "46199662.00", "4.04", "729313.63")],
        )
        assert len(rows) == 1
        assert rows[0].volume == pytest.approx(461996.62)

    def test_amount_wan_converted_to_yuan(self):
        """成交额：腾讯给「万元」=> 落库统一「元」。"""
        rows = self._fetch(
            "000001",
            [self._entry("2026-11-04", "11.74", "1437906.00", "0.74", "168918.40")],
        )
        assert rows[0].amount == pytest.approx(168918.40 * 10000.0)

    def test_turnover_kept_as_percent(self):
        """换手率本来就是 %，原样落库。"""
        rows = self._fetch(
            "000001",
            [self._entry("2026-11-04", "11.74", "1437906.00", "0.74", "168918.40")],
        )
        assert rows[0].turnover == pytest.approx(0.74)


# ---------------------------------------------------------------------------
# 4. pct_change：腾讯不给，必须自己算
# ---------------------------------------------------------------------------

class TestTencentBfqPctChange:

    def _fetch(self, entries):
        payload = {"day": entries, "qt": {"sz000001": ["51", "平安银行", "000001"]}}
        with patch("data_providers.fetch_url_json",
                   return_value={"data": {"sz000001": payload}}):
            rows, name = fetch_daily_kline_tencent_bfq(
                "000001", "2026-11-01", "2026-11-04"
            )
        return rows

    def test_pct_change_computed_from_prev_close(self):
        """腾讯不返回涨跌幅 => 用前收盘价算。

        实测 000001 11-04：11.74 / 11.77 - 1 = -0.2549%
        （东财实时 f170 = -25 -> -0.25%，一致）。
        """
        rows = self._fetch([
            ["2026-11-03", "11.56", "11.77", "11.78", "11.42", "2139674.00",
             {}, "1.10", "248870.18", "0.00", "0.00"],
            ["2026-11-04", "11.75", "11.74", "11.86", "11.68", "1437906.00",
             {}, "0.74", "168918.40", "0.00", "0.00"],
        ])
        by_date = {r.trade_date: r for r in rows}
        assert by_date["2026-11-03"].pct_change == pytest.approx(0.0)
        assert by_date["2026-11-04"].pct_change == pytest.approx(-0.2549, abs=1e-3)
        assert by_date["2026-11-04"].change == pytest.approx(-0.03, abs=1e-6)

    def test_prev_close_carried_from_outside_window(self):
        """窗口外的上一根收盘价也要用来推进 prev_close。

        否则窗口第一根算不出 pct_change（=0.0）=> 量价齐升策略的判据失效。
        """
        rows = self._fetch([
            ["2026-10-30", "11.53", "11.64", "11.67", "11.40", "1864245.00",
             {}, "0.96", "215239.39", "0.00", "0.00"],
            ["2026-11-04", "11.75", "11.74", "11.86", "11.68", "1437906.00",
             {}, "0.74", "168918.40", "0.00", "0.00"],
        ])
        by_date = {r.trade_date: r for r in rows}
        assert "2026-10-30" not in by_date
        assert by_date["2026-11-04"].pct_change == pytest.approx(
            (11.74 - 11.64) / 11.64 * 100.0
        )

    def test_pct_change_is_not_turnover(self):
        """回归防护：腾讯 field[7] 是**换手率**，不是涨跌幅。

        000001 11-04：换手率 0.74%，但涨跌幅是 **-0.2549%**（下跌）。
        把两者搞混 => 11-04 会被当成上涨 0.74% => 动量/量价选股全错。
        """
        rows = self._fetch([
            ["2026-11-03", "11.56", "11.77", "11.78", "11.42", "2139674.00",
             {}, "1.10", "248870.18", "0.00", "0.00"],
            ["2026-11-04", "11.75", "11.74", "11.86", "11.68", "1437906.00",
             {}, "0.74", "168918.40", "0.00", "0.00"],
        ])
        row = [r for r in rows if r.trade_date == "2026-11-04"][0]
        assert row.turnover == pytest.approx(0.74)
        assert row.pct_change < 0
        assert row.pct_change != pytest.approx(0.74)


# ---------------------------------------------------------------------------
# 5. count 参数语义：往回取，不是往后取
# ---------------------------------------------------------------------------

class TestTencentBfqWindow:

    def test_count_covers_whole_range(self):
        """count 是「从 end_date 往回取 N 根」。

        窗口给小了 => 早于窗口的交易日整段缺失（而腾讯**不会报错**，
        只是少给）=> 静默少一天，正是 2026-10-29 的那种事故形态。
        """
        captured = {}

        def fake_json(url, params=None, referer=None):
            captured["param"] = (params or {})["param"]
            return {"data": {"sz000001": {"day": [], "qt": {}}}}

        with patch("data_providers.fetch_url_json", side_effect=fake_json):
            fetch_daily_kline_tencent_bfq("000001", "2026-01-01", "2026-11-04")

        param = captured["param"]
        count = int(param.split(",")[4])
        assert count >= 307
        assert count <= 800
