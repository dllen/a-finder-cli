"""SourceHealthTracker: 数据源健康状态跟踪与动态优先级排序。"""
import threading
from unittest.mock import MagicMock, patch

import pytest

from data_providers import SourceHealthTracker


def _make_tracker():
    return SourceHealthTracker()


class TestProbe:

    def test_probe_marks_eastmoney_unavailable_when_blocked(self):
        """东财被封 → probe 标记不可用，优先级排到末尾。"""
        tracker = _make_tracker()
        with patch("data_providers.fetch_daily_kline_eastmoney") as mock_em, \
             patch("data_providers.fetch_daily_kline_sina") as mock_sina, \
             patch("data_providers.fetch_daily_kline_tencent") as mock_tx:
            mock_em.side_effect = ConnectionError("RemoteDisconnected")
            mock_sina.return_value = ([MagicMock()], "茅台")
            mock_tx.return_value = ([MagicMock()], "茅台")
            tracker.probe()

        assert tracker.is_available("eastmoney") is False
        assert tracker.is_available("sina") is True
        ordered = tracker.get_ordered_sources()
        assert ordered[0] == "sina"
        assert ordered[-1] == "eastmoney"

    def test_probe_all_available_keeps_default_order(self):
        """全部可用 → 保持默认顺序（eastmoney 优先）。"""
        tracker = _make_tracker()
        with patch("data_providers.fetch_daily_kline_eastmoney") as mock_em, \
             patch("data_providers.fetch_daily_kline_sina") as mock_sina, \
             patch("data_providers.fetch_daily_kline_tencent") as mock_tx:
            mock_em.return_value = ([MagicMock()], "茅台")
            mock_sina.return_value = ([MagicMock()], "茅台")
            mock_tx.return_value = ([MagicMock()], "茅台")
            tracker.probe()

        assert tracker.get_ordered_sources() == ["eastmoney", "sina", "tencent"]

    def test_probe_empty_rows_counts_as_failure(self):
        """源返回空列表 → 视为失败。"""
        tracker = _make_tracker()
        with patch("data_providers.fetch_daily_kline_eastmoney") as mock_em, \
             patch("data_providers.fetch_daily_kline_sina") as mock_sina, \
             patch("data_providers.fetch_daily_kline_tencent") as mock_tx:
            mock_em.return_value = ([], "")       # 空 = 失败
            mock_sina.return_value = ([MagicMock()], "茅台")
            mock_tx.return_value = ([MagicMock()], "茅台")
            tracker.probe()

        assert tracker.is_available("eastmoney") is False


class TestRecordFailure:

    def test_consecutive_failures_demote_source(self):
        """连续失败 MAX_CONSECUTIVE_FAILURES 次 → 降级到末尾。"""
        tracker = _make_tracker()
        for _ in range(tracker.MAX_CONSECUTIVE_FAILURES):
            tracker.record_failure("eastmoney")

        assert tracker.is_available("eastmoney") is False
        assert tracker.get_ordered_sources()[-1] == "eastmoney"

    def test_below_threshold_stays_available(self):
        """未达阈值 → 仍可用。"""
        tracker = _make_tracker()
        for _ in range(tracker.MAX_CONSECUTIVE_FAILURES - 1):
            tracker.record_failure("sina")
        assert tracker.is_available("sina") is True


class TestRecordSuccess:

    def test_success_resets_fail_count(self):
        """成功后 fail_count 归零。"""
        tracker = _make_tracker()
        tracker.record_failure("sina")
        tracker.record_failure("sina")
        assert tracker._health["sina"]["fail_count"] == 2

        tracker.record_success("sina")
        assert tracker._health["sina"]["fail_count"] == 0
        assert tracker.is_available("sina") is True


class TestThreadSafety:

    def test_concurrent_record_no_crash(self):
        """多线程并发调用 record_* 不抛异常。"""
        tracker = _make_tracker()
        errors = []

        def worker():
            try:
                for _ in range(20):
                    tracker.record_failure("sina")
                    tracker.record_success("sina")
                    tracker.get_ordered_sources()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Thread errors: {errors}"


from data_providers import fetch_daily_kline_smart


class TestFetchDailyKlineSmart:

    def _make_tracker_with_priority(self, available_sources):
        """构造一个 tracker，只有 available_sources 中的源可用。"""
        tracker = SourceHealthTracker()
        for s in tracker.SOURCES:
            tracker._health[s]["available"] = s in available_sources
        tracker._ordered = [
            s for s in tracker.SOURCES if s in available_sources
        ] + [s for s in tracker.SOURCES if s not in available_sources]
        return tracker

    def test_skips_unavailable_source(self):
        """eastmoney 不可用 → 直接调 sina，不调 eastmoney。"""
        tracker = self._make_tracker_with_priority(["sina", "tencent"])
        with patch("data_providers.fetch_daily_kline_eastmoney") as mock_em, \
             patch("data_providers.fetch_daily_kline_sina") as mock_sina:
            mock_sina.return_value = ([MagicMock()], "茅台")
            rows, name = fetch_daily_kline_smart(
                "600519", "20261001", "20261024", tracker
            )
        mock_em.assert_not_called()
        mock_sina.assert_called_once()
        assert len(rows) > 0

    def test_falls_through_on_first_source_failure(self):
        """第一个可用源失败 → 自动尝试下一个。"""
        tracker = self._make_tracker_with_priority(
            ["eastmoney", "sina", "tencent"]
        )
        with patch("data_providers.fetch_daily_kline_eastmoney") as mock_em, \
             patch("data_providers.fetch_daily_kline_sina") as mock_sina:
            mock_em.side_effect = ConnectionError("blocked")
            mock_sina.return_value = ([MagicMock()], "茅台")
            rows, name = fetch_daily_kline_smart(
                "600519", "20261001", "20261024", tracker
            )
        assert len(rows) > 0
        assert tracker._health["eastmoney"]["fail_count"] == 1

    def test_all_sources_fail_returns_empty(self):
        """所有源都失败 → 返回 ([], '')。"""
        tracker = self._make_tracker_with_priority(
            ["eastmoney", "sina", "tencent"]
        )
        with patch("data_providers.fetch_daily_kline_eastmoney") as mock_em, \
             patch("data_providers.fetch_daily_kline_sina") as mock_sina, \
             patch("data_providers.fetch_daily_kline_tencent") as mock_tx:
            mock_em.side_effect = Exception("fail")
            mock_sina.side_effect = Exception("fail")
            mock_tx.side_effect = Exception("fail")
            rows, name = fetch_daily_kline_smart(
                "600519", "20261001", "20261024", tracker
            )
        assert rows == []
        assert name == ""

    def test_empty_rows_counts_as_failure_and_falls_through(self):
        """源返回空列表 → 视为失败，尝试下一个源。"""
        tracker = self._make_tracker_with_priority(
            ["eastmoney", "sina"]
        )
        with patch("data_providers.fetch_daily_kline_eastmoney") as mock_em, \
             patch("data_providers.fetch_daily_kline_sina") as mock_sina:
            mock_em.return_value = ([], "")  # 空 = 失败
            mock_sina.return_value = ([MagicMock()], "茅台")
            rows, name = fetch_daily_kline_smart(
                "600519", "20261001", "20261024", tracker
            )
        assert len(rows) > 0
        mock_sina.assert_called_once()


class TestSyncServiceIntegration:

    def test_fetch_with_retry_uses_smart_fetcher(self, tmp_path):
        """_fetch_with_retry 调用 fetch_daily_kline_smart 而非 fetch_daily_kline_with_name。"""
        from sync_service import _fetch_with_retry, RateLimiter
        from data_providers import SourceHealthTracker

        tracker = SourceHealthTracker()
        tracker.probe = MagicMock()  # skip probe
        limiter = RateLimiter(rate_per_sec=1000)

        with patch(
            "sync_service.fetch_daily_kline_smart"
        ) as mock_smart:
            mock_smart.return_value = ([MagicMock()], "test")
            rows, name = _fetch_with_retry(
                "600519", "20261001", "20261024",
                retries=0, backoff=0.1,
                limiter=limiter, tracker=tracker,
            )
        mock_smart.assert_called_once()
        assert len(rows) > 0

    def test_sync_hs300_uses_smart_fetcher(self, tmp_path):
        """sync_hs300（增量路径）也走 fetch_daily_kline_smart。"""
        from sync_service import sync_hs300
        from db_repository import open_db

        db_path = str(tmp_path / "t.db")
        open_db(db_path).close()

        with patch("sync_service.fetch_hs300_constituents") as mock_cons, \
             patch("sync_service.fetch_daily_kline_smart") as mock_smart:
            mock_cons.return_value = {"600519": "贵州茅台"}
            mock_smart.return_value = ([], "")
            sync_hs300(db_path, "incremental", 1)

        mock_smart.assert_called()
