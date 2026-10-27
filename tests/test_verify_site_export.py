"""部署前导出校验（scripts/verify_site_export.py）的测试。

存在的意义：2026-10-27 线上站点被一个「导出成功但内容为空」的部署清空，
所以这道守卫必须有测试兜住。
"""

import importlib.util
import json
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "verify_site_export",
    Path(__file__).resolve().parent.parent / "scripts" / "verify_site_export.py",
)
vs = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(vs)


def _make_site(tmp_path, dates=None, n_json=10, index_size=4000):
    site = tmp_path / "site"
    (site / "data").mkdir(parents=True)
    (site / "index.html").write_text("x" * index_size, encoding="utf-8")
    payload = {"dates": dates if dates is not None else ["2026-10-23"]}
    (site / "data" / "dates.json").write_text(json.dumps(payload), encoding="utf-8")
    for i in range(n_json):
        (site / "data" / f"picks-{i}.json").write_text("{}", encoding="utf-8")
    return site


def test_complete_site_passes(tmp_path):
    assert vs.check(_make_site(tmp_path)) == []


def test_empty_dates_rejected(tmp_path):
    problems = vs.check(_make_site(tmp_path, dates=[]))
    assert any("dates 只有 0 个" in p for p in problems)


def test_missing_index_rejected(tmp_path):
    site = _make_site(tmp_path)
    (site / "index.html").unlink()
    assert any("index.html" in p for p in vs.check(site))


def test_tiny_index_rejected(tmp_path):
    site = _make_site(tmp_path, index_size=10)
    assert any("过小" in p for p in vs.check(site))


def test_too_few_json_rejected(tmp_path):
    site = _make_site(tmp_path, n_json=2)
    problems = vs.check(site)
    assert any("JSON（要求 >= 10）" in p for p in problems), problems


def test_missing_site_dir_fails(tmp_path):
    assert vs.main(["--site", str(tmp_path / "nope")]) == 1


def test_cli_pass_and_fail(tmp_path):
    site = _make_site(tmp_path)
    assert vs.main(["--site", str(site)]) == 0
    (site / "data" / "dates.json").write_text('{"dates": []}', encoding="utf-8")
    assert vs.main(["--site", str(site)]) == 1
