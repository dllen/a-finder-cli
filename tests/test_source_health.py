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
        with patch("data_providers.fetch_daily_kline_with_name") as mock_em, \
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
        with patch("data_providers.fetch_daily_kline_with_name") as mock_em, \
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
        with patch("data_providers.fetch_daily_kline_with_name") as mock_em, \
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
