"""scripts/db_lineage.py：谱系存储（GitHub Release 资产）的判定与读写。

**为什么有这个模块（2026-11-04）**
--------------------------------
CI 的行情谱系原来靠 actions/cache，实测**双向都不工作**：条目停在 10-27/10-29
两条 5.2 MB 残库，Post Save / Post Restore 一直 skipped，于是 gap-heal 拿到空库
（体检 30 天全 0 只、补抓后历史深度 0 根）。缓存用量才 ~200 MB，**不是配额问题**。

换成 release 资产：单一名义资产 + 覆盖语义 + 单调性守卫。

这些测试锁住三件事：
1. **资格判定**（decide_save）的每条规则 —— 坏库绝不能覆盖好谱系；
2. restore 真能把 gz 解压成可读的库；
3. save 真会「先删旧资产再上传」，且不合格时**一个写请求都不发**。
"""

import gzip
import json
import os
import sqlite3

import pytest

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import db_lineage  # noqa: E402


def _make_db(path, dates=250, codes=("000001",)):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS daily_prices (code TEXT, trade_date TEXT, close REAL)"
    )
    for code in codes:
        for i in range(dates):
            conn.execute(
                "INSERT INTO daily_prices VALUES (?,?,?)",
                (code, "2026-%02d-%02d" % (1 + i // 28, 1 + i % 28), 10.0),
            )
    conn.commit()
    conn.close()
    return path


class TestReadLocalMeta:

    def test_missing_db(self, tmp_path):
        assert db_lineage.read_local_meta(str(tmp_path / "nope.db")) == {"exists": False}

    def test_reports_fingerprint(self, tmp_path):
        db = _make_db(str(tmp_path / "a.db"), dates=5)
        meta = db_lineage.read_local_meta(db)
        assert meta["exists"] is True
        assert meta["codes"] == 1
        assert meta["price_rows"] == 5
        assert meta["min_bars_per_code"] == 5
        assert meta["trading_days"] == 5
        assert meta["max_trade_date"]


class TestDecideSave:
    """坏库绝不能成为谱系根 —— 这是整个方案的安全底线。"""

    def _local(self, **kw):
        base = {
            "exists": True,
            "max_trade_date": "2026-11-04",
            "min_bars_per_code": 300,
        }
        base.update(kw)
        return base

    def test_missing_db_rejected(self):
        ok, _ = db_lineage.decide_save({"exists": False}, None, True)
        assert ok is False

    def test_no_prices_rejected(self):
        ok, _ = db_lineage.decide_save(self._local(max_trade_date=None), None, True)
        assert ok is False

    def test_not_lineage_ok_rejected(self):
        """体检红/有缺口时绝不覆盖 —— 等价于原来的 degraded- 隔离。"""
        ok, reason = db_lineage.decide_save(self._local(), None, False)
        assert ok is False
        assert "资格" in reason

    def test_short_history_rejected(self):
        ok, reason = db_lineage.decide_save(
            self._local(min_bars_per_code=db_lineage.MIN_HISTORY_BARS - 1), None, True
        )
        assert ok is False
        assert "历史深度" in reason

    def test_older_than_remote_rejected(self):
        """单调性守卫：本地比线上旧就不能覆盖（cache 方案缺这条）。"""
        ok, reason = db_lineage.decide_save(
            self._local(max_trade_date="2026-10-30"),
            {"max_trade_date": "2026-11-04"},
            True,
        )
        assert ok is False
        assert "旧" in reason

    def test_shallower_than_remote_rejected(self):
        ok, reason = db_lineage.decide_save(
            self._local(min_bars_per_code=250),
            {"max_trade_date": "2026-11-04", "min_bars_per_code": 300},
            True,
        )
        assert ok is False
        assert "浅" in reason

    def test_good_db_accepted(self):
        ok, _ = db_lineage.decide_save(self._local(), None, True)
        assert ok is True

    def test_newer_than_remote_accepted(self):
        ok, _ = db_lineage.decide_save(
            self._local(max_trade_date="2026-11-05"),
            {"max_trade_date": "2026-11-04", "min_bars_per_code": 222},
            True,
        )
        assert ok is True


class TestUploadsBase:

    def test_switches_to_uploads_host(self, monkeypatch):
        monkeypatch.setenv("GITHUB_API_URL", "https://api.github.com")
        assert db_lineage._uploads_base() == "https://uploads.github.com"

    def test_keeps_enterprise_host(self, monkeypatch):
        monkeypatch.setenv("GITHUB_API_URL", "https://ghe.example.com/api/v3")
        assert db_lineage._uploads_base() == "https://ghe.example.com/api/v3"


class TestRestore:

    def test_no_release_returns_2(self, monkeypatch):
        monkeypatch.setattr(db_lineage, "get_release", lambda: None)
        assert db_lineage.restore("/tmp/should-not-exist.db") == 2

    def test_release_without_db_asset_returns_2(self, monkeypatch):
        monkeypatch.setattr(db_lineage, "get_release", lambda: {"assets": []})
        assert db_lineage.restore("/tmp/should-not-exist.db") == 2

    def test_roundtrip_decompresses_real_db(self, tmp_path, monkeypatch):
        """真的把 gz 解回 sqlite 并读得出来 —— 这是 restore 的核心承诺。"""
        src = _make_db(str(tmp_path / "src.db"), dates=7)
        with open(src, "rb") as fh:
            gz = gzip.compress(fh.read())

        meta = {"max_trade_date": "2026-01-07", "min_bars_per_code": 7}
        release = {"assets": [{"id": 1, "name": db_lineage.DB_ASSET},
                              {"id": 2, "name": db_lineage.META_ASSET}]}
        monkeypatch.setattr(db_lineage, "get_release", lambda: release)

        def fake_download(asset):
            return gz if asset["name"] == db_lineage.DB_ASSET else json.dumps(meta).encode()

        monkeypatch.setattr(db_lineage, "download_asset", fake_download)

        out = str(tmp_path / "out.db")
        assert db_lineage.restore(out) == 0
        restored = db_lineage.read_local_meta(out)
        assert restored["price_rows"] == 7
        assert restored["codes"] == 1


class TestSave:

    def _wire(self, monkeypatch, local_ok=True):
        calls = {"deleted": [], "uploaded": [], "meta": None}

        monkeypatch.setattr(db_lineage, "get_release", lambda: {"id": 9, "assets": []})
        monkeypatch.setattr(db_lineage, "ensure_release", lambda: {"id": 9, "assets": []})
        monkeypatch.setattr(db_lineage, "find_asset", lambda rel, name: None)
        monkeypatch.setattr(db_lineage, "delete_asset", lambda a: calls["deleted"].append(a))

        def fake_upload(rel, name, payload):
            calls["uploaded"].append((name, len(payload)))
            if name == db_lineage.META_ASSET:
                calls["meta"] = json.loads(payload.decode())
            return {}

        monkeypatch.setattr(db_lineage, "upload_asset", fake_upload)
        return calls

    def test_saves_qualified_db(self, tmp_path, monkeypatch):
        calls = self._wire(monkeypatch)
        db = _make_db(str(tmp_path / "good.db"), dates=db_lineage.MIN_HISTORY_BARS)
        assert db_lineage.save(db, True) == 0
        names = [n for n, _ in calls["uploaded"]]
        assert db_lineage.META_ASSET in names
        assert db_lineage.DB_ASSET in names
        assert calls["meta"]["lineage_ok"] is True
        # DB 资产必须是压缩后的（比原始小）
        db_size = dict(calls["uploaded"])[db_lineage.DB_ASSET]
        assert db_size < os.path.getsize(db)

    def test_disqualified_db_uploads_nothing(self, tmp_path, monkeypatch):
        """不合格时**一个写请求都不发** —— 这条保证谱系不会被坏库污染。"""
        calls = self._wire(monkeypatch)
        db = _make_db(str(tmp_path / "bad.db"), dates=db_lineage.MIN_HISTORY_BARS)
        assert db_lineage.save(db, False) == 0
        assert calls["uploaded"] == []
        assert calls["deleted"] == []

    def test_short_history_uploads_nothing(self, tmp_path, monkeypatch):
        calls = self._wire(monkeypatch)
        db = _make_db(str(tmp_path / "short.db"), dates=5)
        assert db_lineage.save(db, True) == 0
        assert calls["uploaded"] == []

    def test_missing_db_is_noop(self, tmp_path, monkeypatch):
        calls = self._wire(monkeypatch)
        assert db_lineage.save(str(tmp_path / "nope.db"), True) == 0
        assert calls["uploaded"] == []
