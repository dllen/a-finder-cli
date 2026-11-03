"""refresh_agent 的纯逻辑单测（不起真实网络、不跑真实流水线）。

被测的三个纯函数：
- build_report_url / 判定成功失败的逻辑
- latest_date_from_site(site_dir)：读 site/data/dates.json 的最新日
- summarize_result(rc)：把子进程退出码 + 产物日期汇总成回报 payload
"""
import json
from pathlib import Path

from refresh_agent import (
    latest_date_from_site,
    summarize_result,
    build_result_payload,
)


def test_latest_date_from_site_reads_newest_first(tmp_path):
    """dates.json 是新→旧排列，dates[0] 就是最新日。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "dates.json").write_text(
        json.dumps({"dates": ["2026-11-03", "2026-10-30"]}), encoding="utf-8"
    )
    assert latest_date_from_site(tmp_path) == "2026-11-03"


def test_latest_date_from_site_missing_file_returns_none(tmp_path):
    """没有产物时返回 None（不是抛异常 —— agent 不能因为读不到就崩）。"""
    assert latest_date_from_site(tmp_path) is None


def test_latest_date_from_site_broken_json_returns_none(tmp_path):
    """JSON 坏了也返回 None，不能抛。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "dates.json").write_text("{not json", encoding="utf-8")
    assert latest_date_from_site(tmp_path) is None


def test_summarize_result_success():
    """退出码 0 + 有产物日期 → success。"""
    out = summarize_result(returncode=0, latest_date="2026-11-03", log_tail="all good")
    assert out["status"] == "success"
    assert out["latest_date"] == "2026-11-03"


def test_summarize_result_nonzero_exit_is_failed():
    """退出码非 0 → failed，且要带上原因。"""
    out = summarize_result(returncode=1, latest_date=None, log_tail="boom")
    assert out["status"] == "failed"
    assert "boom" in out["message"]


def test_summarize_result_zero_exit_but_no_date_is_failed():
    """退出码 0 但没有产物日期 → failed。

    这条很关键：cmd_site 的单调性守卫失败时 return 1，但如果将来某条路径
    意外 return 0 又没产物，仍必须当失败 —— 页面不能显示"成功"却什么都没更新。
    """
    out = summarize_result(returncode=0, latest_date=None, log_tail="no output")
    assert out["status"] == "failed"


def test_build_result_payload_shape():
    """回报 payload 必须含 Worker 约定的字段名。"""
    payload = build_result_payload(
        job_id="j-1",
        status="success",
        latest_date="2026-11-03",
        message="done",
    )
    assert payload == {
        "job_id": "j-1",
        "status": "success",
        "latest_date": "2026-11-03",
        "message": "done",
    }


def test_http_sends_custom_user_agent():
    """请求必须带自定义 User-Agent。

    2026-11-03 实测：Cloudflare 直接 403 掉 urllib 默认的 Python-urllib/3.11，
    请求根本到不了 Worker。表现是「secret 配对了却一直 403」，极难排查。
    """
    import refresh_agent as ra

    assert ra.USER_AGENT and not ra.USER_AGENT.lower().startswith("python-urllib")

    captured = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"job_id": null}'

    def fake_urlopen(req, timeout=0):
        captured["ua"] = req.get_header("User-agent")
        return _Resp()

    orig = ra.urllib.request.urlopen
    ra.urllib.request.urlopen = fake_urlopen
    try:
        ra._http("https://example.invalid/api/refresh/next")
    finally:
        ra.urllib.request.urlopen = orig

    assert captured.get("ua") == ra.USER_AGENT
