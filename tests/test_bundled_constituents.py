"""内置成分股名单（data/hs300_constituents.json）与 _resolve_constituents 三层回退。

**为什么需要这层兜底（2026-11-04 实测）**
----------------------------------------
CI 的行情谱系有个致命组合：

1. 缓存 restore 失败 → 库里没有任何表；
2. Eastmoney 成分股接口**只有这一个源、没有备源** → 不可达时返回空。

两者同时发生时 `_resolve_constituents` 会 `raise FetchError`，一行行情都抓不到。
实测表现是「补抓 2 秒就结束」「补抓后历史深度 0 根 K 线」，而 run 只是变红，
看不出根因。

三层回退现在是这样：

    1. Eastmoney 成分股接口
    2. 本地库已有代码列表（冷启动时库是空的，这层失效）
    3. **仓库内置名单**（本模块测的这层，让冷启动不再依赖那个接口）
"""

import json
import os
import tempfile

import pytest

import sync_service
from sync_service import (
    BUNDLED_CONSTITUENTS_PATH,
    _load_bundled_constituents,
    _resolve_constituents,
)


class TestBundledFile:

    def test_file_exists_and_parses(self):
        assert os.path.exists(BUNDLED_CONSTITUENTS_PATH)
        with open(BUNDLED_CONSTITUENTS_PATH, encoding="utf-8") as fh:
            payload = json.load(fh)
        assert isinstance(payload, dict)

    def test_has_full_index_size(self):
        """沪深300 = 300 只。少太多说明文件被截断了。"""
        b = _load_bundled_constituents()
        assert len(b["codes"]) == 300

    def test_codes_are_six_digit_strings(self):
        b = _load_bundled_constituents()
        for code in b["codes"]:
            assert isinstance(code, str)
            assert len(code) == 6 and code.isdigit()

    def test_codes_unique(self):
        b = _load_bundled_constituents()
        assert len(set(b["codes"])) == len(b["codes"])

    def test_snapshot_date_present(self):
        """快照日期必须写清楚 —— 名单会过时，用的时候要知道是哪一版。"""
        b = _load_bundled_constituents()
        assert b["snapshot_date"]


class TestLoaderDegrades:

    def test_missing_file_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            sync_service, "BUNDLED_CONSTITUENTS_PATH", "/nonexistent/x.json"
        )
        assert _load_bundled_constituents() == {}

    def test_broken_json_returns_empty(self, tmp_path, monkeypatch):
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        monkeypatch.setattr(sync_service, "BUNDLED_CONSTITUENTS_PATH", str(bad))
        assert _load_bundled_constituents() == {}

    def test_empty_codes_returns_empty(self, tmp_path, monkeypatch):
        p = tmp_path / "empty.json"
        p.write_text(json.dumps({"codes": []}), encoding="utf-8")
        monkeypatch.setattr(sync_service, "BUNDLED_CONSTITUENTS_PATH", str(p))
        assert _load_bundled_constituents() == {}

    def test_non_dict_returns_empty(self, tmp_path, monkeypatch):
        p = tmp_path / "list.json"
        p.write_text(json.dumps(["000001"]), encoding="utf-8")
        monkeypatch.setattr(sync_service, "BUNDLED_CONSTITUENTS_PATH", str(p))
        assert _load_bundled_constituents() == {}


class TestResolveConstituentsFallback:
    """三层回退的优先级与最终行为。"""

    def _empty_db(self):
        return os.path.join(tempfile.mkdtemp(), "empty.db")

    def test_api_wins_when_available(self, monkeypatch):
        monkeypatch.setattr(
            sync_service, "fetch_hs300_constituents", lambda: {"600519": "贵州茅台"}
        )
        out = _resolve_constituents(self._empty_db())
        assert out == {"600519": "贵州茅台"}

    def test_db_codes_used_before_bundled(self, monkeypatch, tmp_path):
        """库里有代码时不要用内置名单（库里的更贴近当前状态）。"""
        import sqlite3

        from db_repository import open_db

        db = str(tmp_path / "with_codes.db")
        conn = open_db(db)
        conn.execute(
            "INSERT OR IGNORE INTO hs300_constituents (code, name) VALUES (?, ?)",
            ("000001", ""),
        )
        conn.commit()
        conn.close()

        monkeypatch.setattr(sync_service, "fetch_hs300_constituents", lambda: {})
        out = _resolve_constituents(db)
        assert list(out) == ["000001"]

    def test_bundled_used_when_api_down_and_db_empty(self, monkeypatch):
        """**这就是 CI 那次事故的场景**：接口挂 + 空库。

        修复前：raise FetchError → 一行行情都抓不到。
        修复后：用内置名单继续，同步能起得来。
        """
        monkeypatch.setattr(sync_service, "fetch_hs300_constituents", lambda: {})
        out = _resolve_constituents(self._empty_db())
        assert len(out) == 300

    def test_raises_only_when_everything_fails(self, monkeypatch):
        monkeypatch.setattr(sync_service, "fetch_hs300_constituents", lambda: {})
        monkeypatch.setattr(
            sync_service, "BUNDLED_CONSTITUENTS_PATH", "/nonexistent/x.json"
        )
        with pytest.raises(Exception):
            _resolve_constituents(self._empty_db())
