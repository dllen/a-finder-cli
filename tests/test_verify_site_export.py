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
    (site / "data").mkdir(parents=True, exist_ok=True)
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


# ---------------------------------------------------------------------------
# 单调性守卫（--not-older-than / --expect-latest）
#
# 为什么要有：2026-10-29 查明 CI 缓存库与本地库是两条独立谱系（CI 的行情谱系
# 断在 2026-10-13，站点却一直由本地库发布到 10-29）。孤儿库导出的
# dates.json=["2026-10-13"] + 十几个 JSON 能通过 --min-dates 1 --min-json 10
# 的门禁 → CI 会把比线上旧 16 天的站点 force-push 上去，比 2026-10-27 的
# 空站点更隐蔽（页面正常、数据停在半个月前）。单调性守卫把这种静默回退变成
# 步骤失败。
# ---------------------------------------------------------------------------


def test_not_older_than_blocks_stale_export(tmp_path):
    """产物比线上旧 → 拒绝部署（孤儿 CI 库的核心回归场景）。"""
    site = _make_site(tmp_path, dates=["2026-10-13", "2026-10-12"])
    problems = vs.check(site, not_older_than="2026-10-29")
    assert any("比线上最新日 2026-10-29 更旧" in p for p in problems), problems


def test_not_older_than_allows_equal_or_newer(tmp_path):
    """等于或新于线上 → 通过（单调性只拦回退，不拦前进）。"""
    site = _make_site(tmp_path, dates=["2026-10-29", "2026-10-28"])
    assert vs.check(site, not_older_than="2026-10-29") == []

    site = _make_site(tmp_path, dates=["2026-10-30"])
    assert vs.check(site, not_older_than="2026-10-29") == []


def test_expect_latest_requires_reaching_expected_day(tmp_path):
    """产物未达到期望交易日 → 拒绝部署（行情可能没落地）。"""
    site = _make_site(tmp_path, dates=["2026-10-28"])
    problems = vs.check(site, expect_latest="2026-10-29")
    assert any("未达到期望交易日 2026-10-29" in p for p in problems), problems


def test_expect_latest_passes_when_reached(tmp_path):
    site = _make_site(tmp_path, dates=["2026-10-29"])
    assert vs.check(site, expect_latest="2026-10-29") == []


def test_stale_export_still_passes_plain_gate(tmp_path):
    """回归证据：只靠 min-dates/min-json 门禁时，孤儿库的产物**能**通过。

    这条测试锁住「为什么必须有单调性守卫」——若将来有人认为守卫多余而删掉它，
    这条会提醒你它挡的正是这个场景。
    """
    site = _make_site(tmp_path, dates=["2026-10-13"], n_json=12)
    assert vs.check(site) == [], "孤儿库的产物在旧门禁下确实会通过"


def test_normalize_date_accepts_compact_form():
    assert vs._normalize_date("20261029") == "2026-10-29"
    assert vs._normalize_date("2026-10-29") == "2026-10-29"


def test_cli_monotonicity_flags(tmp_path):
    site = _make_site(tmp_path, dates=["2026-10-13"])
    assert vs.main(["--site", str(site), "--not-older-than", "2026-10-29"]) == 1
    assert vs.main(["--site", str(site), "--expect-latest", "2026-10-29"]) == 1
    assert vs.main(["--site", str(site),
                    "--not-older-than", "2026-10-13",
                    "--expect-latest", "2026-10-13"]) == 0
