"""巡检通知格式化单测。

任务给的 6 个测试原样保留；额外覆盖：
  - 通知绝不打真实 ntfy（没配 topic 时不碰 urlopen；配了则 mock）
  - 标题带 emoji 时 urllib 头编码不能炸（本地回环收一次请求验证，不出网）
  - CLI 主流程：pass / repaired / manual / no_db / 坏输入
  - repair 结果里的 skipped（超窗未修）必须算「需人工」，不能报「已修复」
"""

import http.server
import json
import socket
import threading
from pathlib import Path
import importlib.util
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

_SPEC = importlib.util.spec_from_file_location(
    "notify_audit", ROOT / "scripts" / "notify_audit.py")
nf = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(nf)


@pytest.fixture(autouse=True)
def no_external_network(monkeypatch):
    """本文件所有测试都不许连外网：只允许回环（emoji 头编码测试用）。

    即使将来有人删掉 mock，也只会失败而不是真的往 ntfy.sh 推消息。
    DNS 也一并拦住：只拦 connect 的话漏 mock 时会先解析域名（算一次出网）。
    """
    real_connect = socket.socket.connect
    real_getaddrinfo = socket.getaddrinfo
    loopback = ("127.0.0.1", "::1", "localhost")

    def guarded_connect(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in loopback:
            raise AssertionError(f"测试试图连接外部网络: {address!r}")
        return real_connect(self, address)

    def guarded_getaddrinfo(host, *args, **kwargs):
        if host not in loopback and host is not None:
            raise AssertionError(f"测试试图解析外部域名: {host!r}")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)


def test_network_guard_blocks_external_connect():
    """guard 自身的回归：漏 mock 时必须在这里失败，而不是真的发请求。"""
    with pytest.raises(AssertionError):
        socket.create_connection(("example.com", 80), timeout=1)


# ---------------------------------------------------------------- 任务给的测试


def test_pass_message_is_heartbeat():
    title, body = nf.format_message(
        status="pass", today="2026-10-27", gaps=[], repaired=[], remaining=[])
    assert "✅" in title
    assert "2026-10-27" in body
    assert len(body) < 300, "心跳消息要短"


def test_repaired_message_lists_what_was_fixed():
    title, body = nf.format_message(
        status="repaired", today="2026-10-27",
        gaps=[{"date": "2026-10-26", "layer": "L2", "detail": "无选股数据"}],
        repaired=[{"layer": "L2", "dates": ["2026-10-26"], "reason": "补算 1 天的选股"}],
        remaining=[])
    assert "🛠" in title
    assert "2026-10-26" in body
    assert "L2" in body


def test_manual_message_is_alarm():
    title, body = nf.format_message(
        status="manual", today="2026-10-27",
        gaps=[
            {"date": "2026-10-10", "layer": "L1", "detail": "行情只有 0 只（阈值 280）"},
        ],
        repaired=[],
        remaining=[{"date": "2026-10-10", "layer": "L1",
                    "detail": "行情只有 0 只（阈值 280）"}],
    )
    assert "🚨" in title
    assert "2026-10-10" in body
    assert "L1" in body


def test_body_lists_layers_not_flat_noise():
    title, body = nf.format_message(
        status="manual", today="2026-10-27",
        gaps=[{"date": f"2026-10-{d:02d}", "layer": "L2", "detail": "无选股"}
              for d in (20, 21, 22)],
        repaired=[],
        remaining=[{"date": f"2026-10-{d:02d}", "layer": "L2", "detail": "无选股"}
                   for d in (20, 21, 22)],
    )
    assert "L2" in body
    # 连续日期应收敛表达，不逐个铺开
    assert body.count("2026-10-20") <= 2


def test_no_db_is_manual_not_pass():
    """缓存里没有 hs300.db 时不能报「通过」——那等于宣称体检过但实际没体检。"""
    assert nf.decide_status(gaps=[], repaired=[], remaining=[], no_db=True) == "manual"
    title, body = nf.format_message(
        status="manual", today="2026-10-27", gaps=[], repaired=[],
        remaining=[], no_db=True)
    assert "🚨" in title
    assert "数据库" in body


def test_status_from_outcomes():
    """全部修复成功 → repaired；有失败 → manual。"""
    assert nf.decide_status(gaps=[], repaired=[], remaining=[]) == "pass"
    assert nf.decide_status(
        gaps=[{"date": "d", "layer": "L2", "detail": ""}],
        repaired=[{"layer": "L2", "dates": ["d"], "reason": ""}],
        remaining=[]) == "repaired"
    assert nf.decide_status(
        gaps=[{"date": "d", "layer": "L1", "detail": ""}],
        repaired=[], remaining=[{"date": "d", "layer": "L1", "detail": ""}]) == "manual"


# ---------------------------------------------------------------- 连续日期收敛


def test_summarize_compresses_only_truly_contiguous_runs():
    items = [
        {"date": f"2026-10-{d:02d}", "layer": "L2", "detail": ""}
        for d in (20, 21, 22, 26)
    ]
    text = nf._summarize(items)
    assert text == "L2 2026-10-20~2026-10-22（3 天）、2026-10-26"
    # 断档的 10-26 不能被并进 10-20~10-26（那会虚报跨度）
    assert "10-20~2026-10-26" not in text


def test_summarize_groups_layers_in_layer_order():
    items = [
        {"date": "2026-10-21", "layer": "L3", "detail": ""},
        {"date": "2026-10-20", "layer": "L1", "detail": ""},
    ]
    assert nf._summarize(items).startswith("L1 ")


# ---------------------------------------------------------------- ntfy 行为


def test_post_ntfy_without_topic_never_touches_network(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("没配 topic 时不该发任何请求")

    monkeypatch.setattr(nf.urllib.request, "urlopen", boom)
    assert nf.post_ntfy("", "标题", "正文") is False


def test_post_ntfy_request_shape(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(req, timeout=None):
        captured["req"] = req
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(nf.urllib.request, "urlopen", fake_urlopen)
    assert nf.post_ntfy("mytopic", "🚨 需人工", "正文", priority="high") is True

    req = captured["req"]
    assert req.full_url == "https://ntfy.sh/mytopic"
    assert req.method == "POST"
    assert req.data == "正文".encode("utf-8")
    assert req.get_header("Title") == "🚨 需人工".encode("utf-8")
    assert req.get_header("Priority") == "high"


def test_post_ntfy_failure_returns_false(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(nf.urllib.request, "urlopen", fake_urlopen)
    assert nf.post_ntfy("mytopic", "标题", "正文") is False


def test_post_ntfy_emoji_title_survives_http_header_encoding(monkeypatch):
    """回归：urllib 对 str 头用 latin-1 编码，emoji 标题会抛 UnicodeEncodeError，
    推送静默失败。标题必须以 UTF-8 字节上行。这里只连本地回环，不出网。"""
    seen = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            seen["title"] = self.headers.get("Title")
            seen["body"] = self.rfile.read(length)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):  # 静音测试输出
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(
        nf, "NTFY_BASE", f"http://127.0.0.1:{server.server_address[1]}")
    try:
        assert nf.post_ntfy("t", "✅ 数据巡检通过", "正文") is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    # http.server 按 latin-1 解头，再按 utf-8 还原即原始标题
    assert seen["title"].encode("latin-1").decode("utf-8") == "✅ 数据巡检通过"
    assert seen["body"] == "正文".encode("utf-8")


# ---------------------------------------------------------------- CLI 主流程


def _write_json(tmp_path: Path, name: str, payload) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _forbid_post(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("这条用例不该发生推送")

    monkeypatch.setattr(nf, "post_ntfy", boom)


def _capture_post(monkeypatch):
    sent = {}

    def fake_post(topic, title, body, *, priority="default"):
        sent.update(topic=topic, title=title, body=body, priority=priority)
        return True

    monkeypatch.setattr(nf, "post_ntfy", fake_post)
    return sent


def test_main_pass_heartbeat_writes_summary_and_posts(tmp_path, monkeypatch, capsys):
    audit = _write_json(tmp_path, "audit.json", {"today": "2026-10-27", "gaps": []})
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    sent = _capture_post(monkeypatch)

    assert nf.main(["--audit", str(audit), "--topic", "t"]) == 0

    assert sent["title"].startswith("✅")
    assert "2026-10-27" in sent["body"]
    assert len(sent["body"]) < 300
    text = summary.read_text(encoding="utf-8")
    assert "✅" in text and "2026-10-27" in text
    assert "2026-10-27" in capsys.readouterr().out


def test_main_all_outcomes_ok_is_repaired(tmp_path, monkeypatch):
    audit = _write_json(tmp_path, "audit.json", {
        "today": "2026-10-27",
        "gaps": [{"date": "2026-10-26", "layer": "L2", "detail": "无选股"}],
    })
    repair = _write_json(tmp_path, "repair.json", {
        "today": "2026-10-27",
        "skipped": [],
        "actions": [{"layer": "L2", "dates": ["2026-10-26"], "reason": "补算 1 天的选股"}],
        "outcomes": [{"layer": "L2", "dates": ["2026-10-26"], "ok": True,
                      "reason": "补算 1 天的选股"}],
    })
    sent = _capture_post(monkeypatch)

    assert nf.main(["--audit", str(audit), "--repair", str(repair),
                    "--topic", "t"]) == 0
    assert sent["title"].startswith("🛠")
    assert "2026-10-26" in sent["body"] and "L2" in sent["body"]


def test_main_failed_outcome_is_manual(tmp_path, monkeypatch):
    audit = _write_json(tmp_path, "audit.json", {
        "today": "2026-10-27",
        "gaps": [{"date": "2026-10-26", "layer": "L1", "detail": "无行情"}],
    })
    repair = _write_json(tmp_path, "repair.json", {
        "today": "2026-10-27",
        "skipped": [],
        "outcomes": [{"layer": "L1", "dates": ["2026-10-26"], "ok": False,
                      "reason": "补抓 2026-10-26~2026-10-26 的行情缺口"}],
    })
    sent = _capture_post(monkeypatch)

    assert nf.main(["--audit", str(audit), "--repair", str(repair),
                    "--topic", "t"]) == 0
    assert sent["title"].startswith("🚨")
    assert sent["priority"] == "high"
    assert "L1" in sent["body"]


def test_main_skipped_gaps_stay_manual(tmp_path, monkeypatch):
    """repair 明确 skipped 的缺口没修，绝不能因为 outcomes 全 ok 就报「已修复」。"""
    gap = {"date": "2026-10-01", "layer": "L1", "detail": "行情只有 0 只（阈值 280）"}
    audit = _write_json(tmp_path, "audit.json",
                        {"today": "2026-10-27", "gaps": [gap]})
    repair = _write_json(tmp_path, "repair.json", {
        "today": "2026-10-27",
        "skipped": [dict(gap, reason="行情缺口超出 7 天自动修复窗（复权口径风险），只报警")],
        "outcomes": [],
    })
    sent = _capture_post(monkeypatch)

    assert nf.main(["--audit", str(audit), "--repair", str(repair),
                    "--topic", "t"]) == 0
    assert sent["title"].startswith("🚨")
    assert "L1" in sent["body"] and "2026-10-01" in sent["body"]


def test_main_repair_without_outcomes_does_not_claim_repaired(tmp_path, monkeypatch):
    audit = _write_json(tmp_path, "audit.json", {
        "today": "2026-10-27",
        "gaps": [{"date": "2026-10-26", "layer": "L2", "detail": "无选股"}],
    })
    repair = _write_json(tmp_path, "repair.json",
                         {"today": "2026-10-27", "skipped": [], "outcomes": []})
    sent = _capture_post(monkeypatch)

    assert nf.main(["--audit", str(audit), "--repair", str(repair),
                    "--topic", "t"]) == 0
    assert sent["title"].startswith("🚨")


def test_main_no_db_is_manual_alarm(tmp_path, monkeypatch):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    sent = _capture_post(monkeypatch)

    assert nf.main(["--no-db", "--today", "2026-10-27", "--topic", "t"]) == 0
    assert sent["title"].startswith("🚨")
    assert "数据库" in sent["body"]
    assert "🚨" in summary.read_text(encoding="utf-8")


def test_main_topic_from_env(tmp_path, monkeypatch):
    audit = _write_json(tmp_path, "audit.json", {"today": "2026-10-27", "gaps": []})
    monkeypatch.setenv("NTFY_TOPIC", "from-env")
    sent = _capture_post(monkeypatch)

    assert nf.main(["--audit", str(audit)]) == 0
    assert sent["topic"] == "from-env"


def test_main_without_topic_skips_push(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    audit = _write_json(tmp_path, "audit.json", {"today": "2026-10-27", "gaps": []})
    monkeypatch.setenv("NTFY_TOPIC", "")

    def boom(*args, **kwargs):
        raise AssertionError("没配 NTFY_TOPIC 时不能发请求")

    monkeypatch.setattr(nf, "post_ntfy", boom)

    assert nf.main(["--audit", str(audit)]) == 0
    assert "未配置 NTFY_TOPIC" in capsys.readouterr().out


def test_main_without_inputs_alarms_instead_of_false_pass(tmp_path, monkeypatch):
    """没有体检输入 ≠ 体检通过：不发心跳，但要发「巡检无法执行」的告警。

    Task 6 在体检 rc=2（日历不可用/库读取失败）时不会传 --audit，
    这条路径必须在通知层可见，不能只剩 Actions 变红。
    """
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    sent = _capture_post(monkeypatch)

    assert nf.main(["--topic", "t"]) == 2
    assert sent["title"].startswith("🚨")
    assert "体检报告" in sent["body"]
    assert "体检报告" in summary.read_text(encoding="utf-8")


def test_main_missing_repair_file_still_alarms(tmp_path, monkeypatch):
    """修复结果缺失只是补充信息丢了，不能因此不发告警。"""
    audit = _write_json(tmp_path, "audit.json", {
        "today": "2026-10-27",
        "gaps": [{"date": "2026-10-26", "layer": "L2", "detail": "无选股"}],
    })
    sent = _capture_post(monkeypatch)

    assert nf.main(["--audit", str(audit),
                    "--repair", str(tmp_path / "nope.json"),
                    "--topic", "t"]) == 0
    assert sent["title"].startswith("🚨")
    assert "L2" in sent["body"]


def test_main_bad_repair_json_still_alarms(tmp_path, monkeypatch):
    audit = _write_json(tmp_path, "audit.json", {
        "today": "2026-10-27",
        "gaps": [{"date": "2026-10-26", "layer": "L2", "detail": "无选股"}],
    })
    repair = tmp_path / "repair.json"
    repair.write_text("{ broken", encoding="utf-8")
    sent = _capture_post(monkeypatch)

    assert nf.main(["--audit", str(audit), "--repair", str(repair),
                    "--topic", "t"]) == 0
    assert sent["title"].startswith("🚨")


def test_main_no_db_ignores_missing_audit_path(tmp_path, monkeypatch):
    """缓存丢了的场景里 audit 文件通常也没写出来，告警不能被文件缺失吞掉。"""
    sent = _capture_post(monkeypatch)

    assert nf.main(["--no-db", "--today", "2026-10-27",
                    "--audit", str(tmp_path / "missing.json"),
                    "--topic", "t"]) == 0
    assert sent["title"].startswith("🚨")
    assert "数据库" in sent["body"]


def test_main_malformed_gaps_alarms_and_returns_2(tmp_path, monkeypatch):
    audit = _write_json(tmp_path, "audit.json", {
        "today": "2026-10-27",
        "gaps": [{"date": "2026-10-26"}],          # 缺 layer
    })
    sent = _capture_post(monkeypatch)
    assert nf.main(["--audit", str(audit), "--topic", "t"]) == 2
    assert sent["title"].startswith("🚨")
    assert "date/layer" in sent["body"]


def test_main_malformed_skipped_makes_repair_info_untrusted(tmp_path, monkeypatch):
    """修复结果形状坏了就整份不采信：全部缺口按「未修」报警，不能假装修好。"""
    audit = _write_json(tmp_path, "audit.json", {
        "today": "2026-10-27",
        "gaps": [{"date": "2026-10-26", "layer": "L2", "detail": "无选股"}],
    })
    repair = _write_json(tmp_path, "repair.json", {
        "today": "2026-10-27",
        "skipped": [{"layer": "L2"}],              # 缺 date
        "outcomes": [{"layer": "L2", "dates": ["2026-10-26"], "ok": True,
                      "reason": "补算 1 天的选股"}],
    })
    sent = _capture_post(monkeypatch)

    assert nf.main(["--audit", str(audit), "--repair", str(repair),
                    "--topic", "t"]) == 0
    assert sent["title"].startswith("🚨")


def test_main_missing_audit_file_alarms_and_returns_2(tmp_path, monkeypatch):
    sent = _capture_post(monkeypatch)
    assert nf.main(["--audit", str(tmp_path / "nope.json"), "--topic", "t"]) == 2
    assert sent["title"].startswith("🚨")
    assert "体检报告不可读" in sent["body"]


def test_main_bad_json_alarms_and_returns_2(tmp_path, monkeypatch):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    sent = _capture_post(monkeypatch)
    assert nf.main(["--audit", str(bad), "--topic", "t"]) == 2
    assert sent["title"].startswith("🚨")


def test_main_bad_today_returns_2(monkeypatch):
    _forbid_post(monkeypatch)
    assert nf.main(["--no-db", "--today", "10/27/2026"]) == 2


# ---------------------------------------------------------------- 复检报告语义


def test_main_recheck_gaps_do_not_resurrect_fixed_dates(tmp_path, monkeypatch):
    """复检后只剩 10-27；失败 action 的 dates 含已修好的 10-26，不能报成剩余。"""
    audit = _write_json(tmp_path, "audit2.json", {
        "today": "2026-10-27",
        "gaps": [{"date": "2026-10-27", "layer": "L2", "detail": "无选股"}],
    })
    repair = _write_json(tmp_path, "repair.json", {
        "today": "2026-10-27",
        "skipped": [],
        "outcomes": [{"layer": "L2", "dates": ["2026-10-26", "2026-10-27"],
                      "ok": False, "reason": "补算选股失败"}],
    })
    sent = _capture_post(monkeypatch)

    assert nf.main(["--audit", str(audit), "--repair", str(repair),
                    "--topic", "t"]) == 0
    assert sent["title"].startswith("🚨")
    assert "2026-10-27" in sent["body"]
    assert "2026-10-26" not in sent["body"]
    assert "（2 天）" not in sent["body"]


def test_main_clean_recheck_with_failed_outcome_is_pass(tmp_path, monkeypatch):
    """复检无缺口 + 修复有条 failed outcome → 以复检为准报通过（不能自相矛盾）。"""
    audit = _write_json(tmp_path, "audit2.json",
                        {"today": "2026-10-27", "gaps": []})
    repair = _write_json(tmp_path, "repair.json", {
        "today": "2026-10-27",
        "skipped": [],
        "outcomes": [{"layer": "L2", "dates": ["2026-10-26"],
                      "ok": False, "reason": "补算选股失败"}],
    })
    sent = _capture_post(monkeypatch)

    assert nf.main(["--audit", str(audit), "--repair", str(repair),
                    "--topic", "t"]) == 0
    assert sent["title"].startswith("✅")


def test_remaining_gaps_only_uses_audit_gaps():
    gaps = [{"date": "2026-10-27", "layer": "L2", "detail": "无选股"}]
    outcomes = [{"layer": "L2", "dates": ["2026-10-26", "2026-10-27"],
                 "ok": False, "reason": "失败"}]
    skipped = [{"date": "2026-10-20", "layer": "L1", "reason": "超窗"}]
    remaining = nf._remaining_gaps(gaps, outcomes, skipped)
    assert [(g["layer"], g["date"]) for g in remaining] == [("L2", "2026-10-27")]
    assert remaining[0]["reason"] == "失败"


def test_remaining_gaps_skipped_never_counts_as_fixed():
    """同 key 混进 ok outcome 也不能把 skipped 抹掉（宁可误报不可假通过）。"""
    gaps = [{"date": "2026-10-01", "layer": "L1", "detail": "超窗"}]
    skipped = [{"date": "2026-10-01", "layer": "L1", "reason": "超窗未修"}]
    outcomes = [{"layer": "L1", "dates": ["2026-10-01"], "ok": True,
                 "reason": "不可能"}]
    remaining = nf._remaining_gaps(gaps, outcomes, skipped)
    assert remaining and remaining[0]["reason"] == "超窗未修"


# ---------------------------------------------------------------- 形状与文案细节


def test_items_rejects_falsy_non_list():
    """`""`/`0`/`false` 不能被塌成空数组绕过后面的形状校验。"""
    for bad in ("", 0, False, {}):
        assert nf._items({"gaps": bad}, "gaps", "audit.json") is None
    assert nf._items({"gaps": None}, "gaps", "audit.json") == []
    assert nf._items({}, "gaps", "audit.json") == []


def test_main_falsy_gaps_is_not_a_silent_pass(tmp_path, monkeypatch):
    audit = _write_json(tmp_path, "audit.json",
                        {"today": "2026-10-27", "gaps": ""})
    sent = _capture_post(monkeypatch)
    assert nf.main(["--audit", str(audit), "--topic", "t"]) == 2
    assert sent["title"].startswith("🚨")


def test_pass_heartbeat_does_not_claim_site_published():
    """pass 路径不发布任何东西，心跳只能宣称体检真正覆盖到的内容。"""
    title, body = nf.format_message(
        status="pass", today="2026-10-27", gaps=[], repaired=[], remaining=[])
    assert "三层无缺口" in body
    assert "已发布" not in body


def test_manual_message_falls_back_to_gap_detail():
    gap = {"date": "2026-10-10", "layer": "L1", "detail": "行情只有 0 只（阈值 280）"}
    title, body = nf.format_message(
        status="manual", today="2026-10-27", gaps=[gap], repaired=[],
        remaining=[dict(gap)])
    assert "L1 2026-10-10：行情只有 0 只（阈值 280）" in body


def test_empty_reasons_are_not_rendered_as_separator():
    title, body = nf.format_message(
        status="repaired", today="2026-10-27",
        gaps=[{"date": "2026-10-26", "layer": "L2", "detail": "无选股"}],
        repaired=[{"layer": "L2", "dates": ["2026-10-26"], "reason": ""}],
        remaining=[])
    assert "修复：已自动补齐" in body


def test_window_days_comes_from_audit_report(tmp_path, monkeypatch):
    audit = _write_json(tmp_path, "audit.json", {
        "today": "2026-10-27", "window_days": 60, "gaps": [],
    })
    sent = _capture_post(monkeypatch)
    assert nf.main(["--audit", str(audit), "--topic", "t"]) == 0
    assert "近 60 天" in sent["body"]


def test_window_days_cli_overrides_report(tmp_path, monkeypatch):
    audit = _write_json(tmp_path, "audit.json", {
        "today": "2026-10-27", "window_days": 60, "gaps": [],
    })
    sent = _capture_post(monkeypatch)
    assert nf.main(["--audit", str(audit), "--window-days", "7",
                    "--topic", "t"]) == 0
    assert "近 7 天" in sent["body"]


def test_main_today_is_normalized(tmp_path, monkeypatch):
    """fromisoformat 接受 20261027，但展示要回写成 YYYY-MM-DD。"""
    sent = _capture_post(monkeypatch)
    assert nf.main(["--no-db", "--today", "20261027", "--topic", "t"]) == 0
    assert "2026-10-27" in sent["body"]
    assert "20261027" not in sent["body"]


def test_main_warns_when_today_disagrees_with_audit(tmp_path, monkeypatch, capsys):
    audit = _write_json(tmp_path, "audit.json",
                        {"today": "2026-10-26", "gaps": []})
    _capture_post(monkeypatch)
    assert nf.main(["--audit", str(audit), "--today", "2026-10-27",
                    "--topic", "t"]) == 0
    assert "不一致" in capsys.readouterr().err


@pytest.mark.parametrize("status", ["pass", "repaired", "manual"])
def test_message_always_has_body(status):
    title, body = nf.format_message(
        status=status, today="2026-10-27",
        gaps=[{"date": "2026-10-26", "layer": "L2", "detail": "无选股"}],
        repaired=[{"layer": "L2", "dates": ["2026-10-26"], "reason": "补算 1 天的选股"}],
        remaining=[{"date": "2026-10-26", "layer": "L2", "detail": "无选股"}])
    assert title and body
