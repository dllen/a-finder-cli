"""每日简报：数据提取、Flask 路由、静态导出、插件契约的回归测试。"""
from __future__ import annotations
import json
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_script():
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import generate_briefing_input as mod
        return mod
    finally:
        sys.path.pop(0)


class TestBriefingInputExtraction:
    def test_extracts_intraday_peak(self):
        mod = _load_script()
        islands = {
            "initData": {
                "date": "2026-11-24",
                "times": ["09:31", "14:57", "15:00"],
                "bars": [10.17, 24.84, 3.88],
            },
            "cmpData": {"latest": {"date": "2026-11-24", "amount": 856.21, "netflow": -118.7}},
            "dailyData": {"dates": ["2026-11-24"], "amount": [856.21], "netflow": [-118.7]},
            "broadData": {
                "dates": ["2026-11-24"],
                "cats": ["沪深300", "中证1000"],
                "series": {"沪深300": [-36.13], "中证1000": [46.96]},
                "total": [-115.86],
            },
        }
        result = mod.extract_intraday(islands)
        assert result["peak_minute"]["time"] == "14:57"
        assert result["peak_minute"]["amount_yi"] == 24.84
        assert result["total_amount_yi"] == 856.21
        assert result["total_netflow_yi"] == -118.7

    def test_extracts_sectors_top3(self):
        mod = _load_script()
        islands = {
            "broadData": {
                "dates": ["2026-11-24"],
                "cats": ["沪深300", "中证1000", "科创50", "创业板指"],
                "series": {"沪深300": [-36.13], "中证1000": [46.96], "科创50": [-12.89], "创业板指": [-11.77]},
                "total": [-115.86],
            },
        }
        result = mod.extract_sectors(islands)
        assert result["top_inflow"][0]["name"] == "中证1000"
        assert result["top_outflow"][0]["name"] == "沪深300"
        assert len(result["top_inflow"]) <= 3
        assert len(result["top_outflow"]) <= 3

    def test_extracts_picks_from_json(self, tmp_path):
        mod = _load_script()
        picks_file = tmp_path / "picks-2026-11-24.json"
        picks_file.write_text(json.dumps({
            "date": "2026-11-24",
            "groups": {
                "买入信号": [{"rank": 1, "code": "002179", "name": "中航光电", "strategy": "均线突破", "day_pct": 1.83}],
                "高胜率": [{"rank": 1, "code": "601689", "name": "拓普集团", "strategy": "量价齐升（6次）", "day_pct": 2.63}],
            },
        }), encoding="utf-8")
        result = mod.extract_picks(picks_file)
        assert result["buy_signals"][0]["code"] == "002179"
        assert result["high_winrate"][0]["code"] == "601689"
        assert result["count_by_group"]["买入信号"] == 1

    def test_missing_picks_returns_empty(self, tmp_path):
        mod = _load_script()
        result = mod.extract_picks(tmp_path / "nonexistent.json")
        assert result["buy_signals"] == []
        assert result["count_by_group"] == {}

    def test_recent_events_from_daily_data(self):
        mod = _load_script()
        islands = {
            "dailyData": {
                "dates": ["2026-11-18", "2026-11-19", "2026-11-20", "2026-11-23", "2026-11-24"],
                "netflow": [50.0, -20.0, 903.87, 160.47, -118.7],
            },
            "cmpData": {"support": [{"date": "2026-11-20", "amount": 1545.07, "netflow": 903.87}]},
        }
        events = mod.extract_recent_events(islands, n=5)
        assert len(events) >= 1
        assert any(e["type"] == "放量日" for e in events)

    def test_full_build_produces_valid_schema(self, tmp_path):
        mod = _load_script()
        etf_dir = tmp_path / "etf"
        etf_dir.mkdir()
        (etf_dir / "islands.json").write_text(json.dumps({
            "initData": {"date": "2026-11-24", "times": ["14:57"], "bars": [24.84]},
            "cmpData": {"latest": {"date": "2026-11-24", "amount": 856.21, "netflow": -118.7}, "support": []},
            "dailyData": {"dates": ["2026-11-24"], "amount": [856.21], "netflow": [-118.7]},
            "broadData": {"dates": ["2026-11-24"], "cats": ["沪深300"], "series": {"沪深300": [-36.13]}, "total": [-36.13]},
        }), encoding="utf-8")
        site_data = tmp_path / "site" / "data"
        site_data.mkdir(parents=True)
        (site_data / "picks-2026-11-24.json").write_text(json.dumps({"date": "2026-11-24", "groups": {}}), encoding="utf-8")

        result = mod.build_briefing_input(etf_dir, site_data, db_path=None)
        assert result["date"] == "2026-11-24"
        assert result["is_trading_day"] is True
        assert "intraday" in result
        assert "sectors" in result
        assert "picks" in result
        assert "recent_events" in result
