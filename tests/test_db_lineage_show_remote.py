"""scripts/db_lineage.py 的 `show --remote`。

**为什么加这个（2026-11-05 实际踩过）**
-------------------------------------
`show` 原本**只**看本地库。排查时我把它输出的
`max_trade_date: 2026-11-05` 当成了**线上**的谱系指纹，
于是以为「线上已经是最新、不用更新」——
实际上线上还停在 **2026-11-04**。

⇒ **本地与线上必须能分开看**，否则会基于错误的前提做决策。
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "db_lineage",
    Path(__file__).resolve().parent.parent / "scripts" / "db_lineage.py",
)
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)


class _FakeAsset:
    name = mod.META_ASSET


class TestShowRemote:

    def test_show_has_remote_flag(self):
        """`show` 必须有 --remote（否则看不出线上谱系）。"""
        root = Path(__file__).resolve().parents[1]
        r = subprocess.run(
            [sys.executable, str(root / "scripts" / "db_lineage.py"), "show", "--help"],
            capture_output=True, text=True)
        assert r.returncode == 0
        assert "--remote" in r.stdout

    def test_read_remote_meta_returns_exists_false_without_release(self, monkeypatch):
        """线上没有谱系时返回 {"exists": False}（与本地口径一致）。"""
        monkeypatch.setattr(mod, "get_release", lambda: None)
        out = mod.read_remote_meta()
        assert out.get("exists") is False

    def test_read_remote_meta_parses_asset(self, monkeypatch):
        """线上有 meta 时把它读出来。"""
        payload = json.dumps({
            "exists": True,
            "max_trade_date": "2026-11-04",
            "min_bars_per_code": 222,
        }).encode("utf-8")
        monkeypatch.setattr(mod, "get_release", lambda: {"id": 1})
        monkeypatch.setattr(mod, "find_asset", lambda rel, name: _FakeAsset())
        monkeypatch.setattr(mod, "download_asset", lambda asset: payload)
        out = mod.read_remote_meta()
        assert out["exists"] is True
        assert out["max_trade_date"] == "2026-11-04"
        assert out["min_bars_per_code"] == 222

    def test_read_remote_meta_survives_bad_payload(self, monkeypatch):
        """线上 meta 坏了不要抛异常 —— 返回 exists=False + 错误信息。"""
        monkeypatch.setattr(mod, "get_release", lambda: {"id": 1})
        monkeypatch.setattr(mod, "find_asset", lambda rel, name: _FakeAsset())
        monkeypatch.setattr(mod, "download_asset", lambda asset: b"not-json")
        out = mod.read_remote_meta()
        assert out.get("exists") is False
        assert "error" in out
