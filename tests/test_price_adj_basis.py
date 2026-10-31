"""复权口径（adjustment basis）的登记、选择与落库测试。

**背景（2026-10-29/30 事故）**
----------------------------
行情备源链里三个源的口径**并不一致**：

    eastmoney  push2his fqt=0   不复权（none）
    sina       getKLineData     不复权（none）
    tencent    fqkline qfq      前复权（qfq）

东财被限流时，链会**静默**退到腾讯，把**前复权**价格写进**不复权**的
``daily_prices`` 表。除权除息日两套口径的价差会在序列里留下一个**假跳空**，
污染 MA / 动量 / 回测窗口与当天的交易计划；而且因为表里**没有口径字段**，
事后**无法证明**它发生过。

另有一件必须记住的事：**2026-10-29 只有东财有**（腾讯 qfqday 与新浪当时都只到
10-28）—— 「退到腾讯」不是对最近交易日的补救，它只会**静默少一天**。

这些测试锁住三件事：
1. 口径登记表是对的（别把东财/新浪标成前复权，或把腾讯标成不复权）。
2. 备源链**优先**用不复权源，只有在完全没有不复权源时才退到前复权源，
   并且**必须把用到的口径回报给调用方**。
3. 口径会**落库**（``daily_prices.adj_basis``），于是混口径的事故是**可检测**的。
"""

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import data_providers
from data_providers import (
    BASIS_NONE,
    BASIS_QFQ,
    PREFERRED_BASIS,
    SOURCE_BASIS,
    SourceHealthTracker,
    fetch_daily_kline_smart,
    source_basis,
)
from db_repository import (
    PriceRow,
    get_adj_basis_breakdown,
    insert_prices,
    open_db,
)

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "db" / "migrations"
ADJ_BASIS_MIGRATION = MIGRATIONS_DIR / "2026_10_30_daily_prices_adj_basis.sql"


def _tracker_with(available_sources):
    """构造 tracker：只有 available_sources 里的源「可用」，其余不可用。

    顺序沿用 tracker.SOURCES 的默认优先级（eastmoney > sina > tencent），
    避免测试自己构造出「腾讯排第一」这种不真实的顺序。
    """
    tracker = SourceHealthTracker()
    for s in tracker.SOURCES:
        tracker._health[s]["available"] = s in available_sources
    tracker._ordered = [
        s for s in tracker.SOURCES if s in available_sources
    ] + [s for s in tracker.SOURCES if s not in available_sources]
    return tracker


def _make_row(code="600519", trade_date="2026-10-28", **kwargs):
    defaults = dict(
        open_price=10.0,
        close_price=11.0,
        high_price=12.0,
        low_price=9.0,
        volume=100.0,
        amount=1000.0,
        amplitude=3.0,
        pct_change=1.5,
        change=0.2,
        turnover=0.8,
    )
    defaults.update(kwargs)
    return PriceRow(code=code, trade_date=trade_date, **defaults)


# ---------------------------------------------------------------------------
# 口径登记表
# ---------------------------------------------------------------------------

class TestBasisRegistry:

    def test_registry_maps_each_source_to_correct_basis(self):
        """每个源的口径必须登记正确 —— 这张表是口径的唯一真相源。"""
        assert SOURCE_BASIS["eastmoney"] == BASIS_NONE
        assert SOURCE_BASIS["sina"] == BASIS_NONE
        assert SOURCE_BASIS["tencent"] == BASIS_QFQ

    def test_preferred_basis_is_none(self):
        """目标口径是不复权（与东财 fqt=0 / 新浪一致）。"""
        assert PREFERRED_BASIS == BASIS_NONE
        assert PREFERRED_BASIS == "none"

    def test_source_basis_lookup(self):
        """source_basis() 查已知源；未登记的源返回 None（必须被当成非首选口径）。"""
        assert source_basis("eastmoney") == "none"
        assert source_basis("sina") == "none"
        assert source_basis("tencent") == "qfq"
        assert source_basis("akshare") is None

    def test_only_tencent_is_non_preferred(self):
        """首选口径组 = {东财, 新浪}；腾讯是唯一的非首选源。"""
        preferred = {s for s, b in SOURCE_BASIS.items() if b == PREFERRED_BASIS}
        assert preferred == {"eastmoney", "sina"}
        assert SOURCE_BASIS["tencent"] != PREFERRED_BASIS


# ---------------------------------------------------------------------------
# fetch_daily_kline_smart 的口径感知
# ---------------------------------------------------------------------------

class TestSmartFetchBasisAwareness:

    def test_prefers_none_basis_source_over_qfq(self):
        """东财 + 腾讯都可用 → 必须用东财（不复权），绝不能碰腾讯（前复权）。

        这是 2026-10-29 事故的核心：备源链「第一个非空胜出」的旧语义会让
        腾讯 qfq 混进不复权的表。
        """
        tracker = _tracker_with(["eastmoney", "tencent"])
        with patch("data_providers.fetch_daily_kline_eastmoney") as mock_em, \
             patch("data_providers.fetch_daily_kline_tencent") as mock_tx:
            mock_em.return_value = ([MagicMock()], "贵州茅台")
            mock_tx.return_value = ([MagicMock()], "贵州茅台")
            rows, name, source, basis = fetch_daily_kline_smart(
                "600519", "20261001", "20261024", tracker
            )

        mock_em.assert_called_once()
        mock_tx.assert_not_called()
        assert source == "eastmoney"
        assert basis == "none"

    def test_prefers_sina_over_tencent_when_eastmoney_down(self):
        """东财挂了、腾讯可用 → 必须用新浪（不复权），不用腾讯（前复权）。"""
        tracker = _tracker_with(["sina", "tencent"])
        with patch("data_providers.fetch_daily_kline_sina") as mock_sina, \
             patch("data_providers.fetch_daily_kline_tencent") as mock_tx:
            mock_sina.return_value = ([MagicMock()], "贵州茅台")
            mock_tx.return_value = ([MagicMock()], "贵州茅台")
            rows, name, source, basis = fetch_daily_kline_smart(
                "600519", "20261001", "20261024", tracker
            )

        mock_sina.assert_called_once()
        mock_tx.assert_not_called()
        assert source == "sina"
        assert basis == "none"

    def test_falls_back_to_qfq_only_when_no_preferred_source(self):
        """只剩腾讯可用 → 允许退到前复权，但**必须把口径回报出去**。

        退到 qfq 意味着该段行情与库里其它不复权行情**不同口径**，调用方
        只有拿到 basis 才可能把它记进库里、让问题事后可查。
        """
        tracker = _tracker_with(["tencent"])
        with patch("data_providers.fetch_daily_kline_tencent") as mock_tx:
            mock_tx.return_value = ([MagicMock()], "贵州茅台")
            rows, name, source, basis = fetch_daily_kline_smart(
                "600519", "20261001", "20261024", tracker
            )

        mock_tx.assert_called_once()
        assert source == "tencent"
        assert basis == "qfq"
        assert basis != PREFERRED_BASIS

    def test_preferred_group_exhausted_before_touching_qfq(self):
        """首选口径源全部失败 → 才尝试 qfq 源（顺序：组内先于跨组）。"""
        tracker = _tracker_with(["eastmoney", "sina", "tencent"])
        with patch("data_providers.fetch_daily_kline_eastmoney") as mock_em, \
             patch("data_providers.fetch_daily_kline_sina") as mock_sina, \
             patch("data_providers.fetch_daily_kline_tencent") as mock_tx:
            mock_em.side_effect = ConnectionError("blocked")
            mock_sina.side_effect = ConnectionError("blocked")
            mock_tx.return_value = ([MagicMock()], "贵州茅台")
            rows, name, source, basis = fetch_daily_kline_smart(
                "600519", "20261001", "20261024", tracker
            )

        assert mock_em.called and mock_sina.called
        assert source == "tencent"
        assert basis == "qfq"

    def test_returns_four_tuple_with_source_and_basis(self):
        """返回值是 (rows, name, source, basis) 四元组。"""
        tracker = _tracker_with(["eastmoney"])
        with patch("data_providers.fetch_daily_kline_eastmoney") as mock_em:
            mock_em.return_value = ([MagicMock()], "贵州茅台")
            result = fetch_daily_kline_smart(
                "600519", "20261001", "20261024", tracker
            )
        assert isinstance(result, tuple)
        assert len(result) == 4
        rows, name, source, basis = result
        assert len(rows) == 1
        assert name == "贵州茅台"
        assert source == "eastmoney"
        assert basis == "none"

    def test_all_sources_fail_returns_empty_quad(self):
        """全源失败 → ([], "", "", "")。"""
        tracker = _tracker_with(["eastmoney", "sina", "tencent"])
        with patch("data_providers.fetch_daily_kline_eastmoney") as mock_em, \
             patch("data_providers.fetch_daily_kline_sina") as mock_sina, \
             patch("data_providers.fetch_daily_kline_tencent") as mock_tx:
            mock_em.side_effect = Exception("fail")
            mock_sina.side_effect = Exception("fail")
            mock_tx.side_effect = Exception("fail")
            rows, name, source, basis = fetch_daily_kline_smart(
                "600519", "20261001", "20261024", tracker
            )
        assert rows == []
        assert name == ""
        assert source == ""
        assert basis == ""

    def test_unavailable_preferred_source_is_not_called(self):
        """首选口径源被 tracker 判为不可用 → 根本不调用，直接找下一个。

        这正是 2026-10-29 的场景：东财限流 → tracker 把它降级 → 链必须去找
        新浪（不复权），而不是直接落到腾讯（前复权）。
        """
        tracker = _tracker_with(["sina", "tencent"])
        tracker._health["eastmoney"]["available"] = False
        with patch("data_providers.fetch_daily_kline_eastmoney") as mock_em, \
             patch("data_providers.fetch_daily_kline_sina") as mock_sina, \
             patch("data_providers.fetch_daily_kline_tencent") as mock_tx:
            mock_sina.return_value = ([MagicMock()], "贵州茅台")
            mock_tx.return_value = ([MagicMock()], "贵州茅台")
            rows, name, source, basis = fetch_daily_kline_smart(
                "600519", "20261001", "20261024", tracker
            )
        mock_em.assert_not_called()
        mock_tx.assert_not_called()
        assert source == "sina"
        assert basis == "none"


# ---------------------------------------------------------------------------
# 迁移
# ---------------------------------------------------------------------------

class TestAdjBasisMigration:

    def test_migration_file_exists_and_contains_alter(self):
        """迁移文件必须存在，且包含 ALTER TABLE ... ADD COLUMN adj_basis。"""
        assert ADJ_BASIS_MIGRATION.exists()
        sql = ADJ_BASIS_MIGRATION.read_text()
        assert "ALTER TABLE daily_prices ADD COLUMN adj_basis TEXT;" in sql

    def test_migration_applies_to_existing_db(self):
        """已部署库（表已存在、没有该列）→ 迁移后列存在。

        只跑本仓库的这条迁移（不跑整包），以便精确断言这一条的效果。
        """
        path = ":memory:"
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE daily_prices (code TEXT NOT NULL, trade_date TEXT NOT NULL, "
            "close REAL, PRIMARY KEY (code, trade_date))"
        )
        conn.commit()
        conn.executescript(ADJ_BASIS_MIGRATION.read_text())
        cols = {r[1] for r in conn.execute("PRAGMA table_info(daily_prices)")}
        conn.close()
        assert "adj_basis" in cols

    def test_migration_is_rerunnable_on_fresh_db(self):
        """全新库跑两次 open_db() → 不崩，列只有一列。"""
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name

        conn = open_db(path)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(daily_prices)")]
        conn.close()
        assert "adj_basis" in cols

        # 第二次打开：迁移已被记账，直接跳过。
        conn = open_db(path)
        cols2 = [r[1] for r in conn.execute("PRAGMA table_info(daily_prices)")]
        conn.close()
        assert cols2.count("adj_basis") == 1

    def test_migration_is_idempotent_when_column_already_exists(self):
        """账本落后于实际 schema（列已存在但没记账）→ 重跑不崩，且会被记账。

        SQLite 的 ADD COLUMN 没有 IF NOT EXISTS，纯 SQL 无法自己兜住；
        _run_migrations 对 "duplicate column name" 做幂等处理。
        """
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name

        conn = open_db(path)
        conn.close()

        # 模拟「手工加过列、但 _applied_migrations 没记账」。
        conn = sqlite3.connect(path)
        conn.execute("DELETE FROM _applied_migrations WHERE filename = ?",
                     (ADJ_BASIS_MIGRATION.name,))
        conn.commit()
        conn.close()

        # 重跑：不得抛异常。
        conn = open_db(path)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(daily_prices)")]
        applied = {
            r[0] for r in conn.execute("SELECT filename FROM _applied_migrations")
        }
        conn.close()

        assert cols.count("adj_basis") == 1
        assert ADJ_BASIS_MIGRATION.name in applied

    def test_fresh_db_schema_has_adj_basis(self):
        """全新库的 CREATE TABLE 里直接带 adj_basis（db_schema.py）。"""
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        conn = open_db(path)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(daily_prices)")}
        conn.close()
        assert "adj_basis" in cols


# ---------------------------------------------------------------------------
# 落库：PriceRow → daily_prices
# ---------------------------------------------------------------------------

class TestPriceRowBasisPersistence:

    def test_price_row_defaults_to_none(self):
        """PriceRow 的 adj_basis 默认 "none" —— 其它模块的构造点无需改动。"""
        row = _make_row()
        assert row.adj_basis == "none"

    def test_price_row_accepts_explicit_basis(self):
        """可以显式指定口径。"""
        row = _make_row(adj_basis="qfq")
        assert row.adj_basis == "qfq"

    def test_writer_persists_non_default_basis(self):
        """写入器必须把非默认口径落库（否则混口径就查不出来）。"""
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        conn = open_db(path)
        insert_prices(conn, [_make_row(adj_basis="qfq")])
        conn.commit()
        got = conn.execute(
            "SELECT adj_basis FROM daily_prices WHERE code = '600519'"
        ).fetchone()
        conn.close()
        assert got is not None
        assert got[0] == "qfq"

    def test_writer_persists_default_basis(self):
        """默认口径也要写进去（"none"），不能留 NULL。"""
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        conn = open_db(path)
        insert_prices(conn, [_make_row()])
        conn.commit()
        got = conn.execute(
            "SELECT adj_basis FROM daily_prices WHERE code = '600519'"
        ).fetchone()
        conn.close()
        assert got[0] == "none"

    def test_writer_upserts_basis_on_reinsert(self):
        """重写同一 (code, trade_date) → 口径被更新（INSERT OR REPLACE 语义）。"""
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        conn = open_db(path)
        insert_prices(conn, [_make_row(adj_basis="none")])
        conn.commit()
        insert_prices(conn, [_make_row(adj_basis="qfq")])
        conn.commit()
        got = conn.execute(
            "SELECT adj_basis FROM daily_prices WHERE code = '600519'"
        ).fetchone()
        conn.close()
        assert got[0] == "qfq"

    def test_mixed_basis_is_detectable(self):
        """混口径能被查出来 —— 这是本次改动的全部意义所在。"""
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        conn = open_db(path)
        insert_prices(conn, [
            _make_row(code="600519", trade_date="2026-10-28", adj_basis="none"),
            _make_row(code="600519", trade_date="2026-10-29", adj_basis="qfq"),
            _make_row(code="000001", trade_date="2026-10-29", adj_basis="none"),
        ])
        conn.commit()

        breakdown = get_adj_basis_breakdown(conn)
        by_date = {(b, d): n for b, d, n in breakdown}
        conn.close()

        # 10-29 这天同时存在两种口径 → 事故现场被记录下来了。
        assert by_date[("none", "2026-10-29")] == 1
        assert by_date[("qfq", "2026-10-29")] == 1
        assert by_date[("none", "2026-10-28")] == 1

    def test_breakdown_can_filter_by_date_range(self):
        """按区间过滤 —— 排查时通常只看最近几天。"""
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        conn = open_db(path)
        insert_prices(conn, [
            _make_row(code="600519", trade_date="2026-10-20", adj_basis="none"),
            _make_row(code="600519", trade_date="2026-10-29", adj_basis="qfq"),
        ])
        conn.commit()
        breakdown = get_adj_basis_breakdown(
            conn, start_date="2026-10-28", end_date="2026-10-30"
        )
        conn.close()
        assert breakdown == [("qfq", "2026-10-29", 1)]


# ---------------------------------------------------------------------------
# 口径必须盖到「行」上，而不只是「返回值」上
# ---------------------------------------------------------------------------

class TestBasisStampedOnRows:

    """返回的 basis 正确 ≠ 落库的 basis 正确。

    `PriceRow.adj_basis` 的默认值是 `"none"`（不复权）。如果
    `fetch_daily_kline_smart` 只是把口径放进返回值、却没盖到每一行上，
    那么腾讯（前复权）抓回来的行会带着默认的 `"none"` 落库 ——
    **比没有这一列更坏**：看起来检测过了（全是 none、没有混口径告警），
    实际数据已经混了口径，而库里的标记却在说「没混」。

    这个 bug 真实发生过（2026-10-30 复核时发现并修掉），所以必须有测试守住。
    """

    def _row(self):
        return PriceRow(
            code="600519", trade_date="2026-10-30", open_price=1.0,
            close_price=1.0, high_price=1.0, low_price=1.0, volume=1.0,
            amount=1.0, amplitude=0.0, pct_change=0.0, change=0.0, turnover=0.0,
        )

    def test_qfq_rows_are_stamped_qfq(self):
        """腾讯（前复权）胜出 → 每一行都要标成 qfq。"""
        tracker = _tracker_with(["tencent"])
        with patch("data_providers.fetch_daily_kline_tencent") as mock_tx:
            mock_tx.return_value = ([self._row()], "贵州茅台")
            rows, name, source, basis = fetch_daily_kline_smart(
                "600519", "20261001", "20261024", tracker
            )

        assert source == "tencent"
        assert basis == "qfq"
        assert [r.adj_basis for r in rows] == ["qfq"], \
            "腾讯（前复权）的行必须标 qfq，否则落库后与不复权序列无法区分"

    def test_none_basis_rows_are_stamped_none(self):
        """东财（不复权）胜出 → 每一行都要标成 none。"""
        tracker = _tracker_with(["eastmoney"])
        with patch("data_providers.fetch_daily_kline_eastmoney") as mock_em:
            mock_em.return_value = ([self._row()], "贵州茅台")
            rows, name, source, basis = fetch_daily_kline_smart(
                "600519", "20261001", "20261024", tracker
            )

        assert source == "eastmoney"
        assert basis == "none"
        assert [r.adj_basis for r in rows] == ["none"]

    def test_stamped_basis_survives_to_the_database(self):
        """端到端：抓回来的 qfq 行落库后，库里必须记着 qfq。"""
        tracker = _tracker_with(["tencent"])
        with patch("data_providers.fetch_daily_kline_tencent") as mock_tx:
            mock_tx.return_value = ([self._row()], "贵州茅台")
            rows, name, source, basis = fetch_daily_kline_smart(
                "600519", "20261001", "20261024", tracker
            )

        conn = open_db(":memory:")
        insert_prices(conn, rows)
        conn.commit()
        stored = conn.execute(
            "SELECT DISTINCT adj_basis FROM daily_prices").fetchall()
        assert stored == [("qfq",)], \
            "库里必须记得这些行是前复权来的，否则混口径不可检测"
