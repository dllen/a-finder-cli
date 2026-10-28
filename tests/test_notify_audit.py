"""巡检通知格式化单测。

任务给的 6 个测试原样保留；额外覆盖：
  - 通知绝不打真实 ntfy（没配 topic 时不碰 urlopen；配了则 mock）
  - 标题带 emoji 时 urllib 头编码不能炸（本地回环收一次请求验证，不出网）
  - CLI 主流程：pass / repaired / manual / no_db / 坏输入
  - repair 结果里的 skipped（超窗未修）必须算「需人工」，不能报「已修复」
  - 复检语义：剩余只认报告 gaps，ok outcome 不能抹掉复检仍缺的日期；
    原/复检双报告（--audit-pre）的差集才叫「已修复」
  - 坏类型（reason 是数字、ok 是字符串）只降级，不能炸掉通知
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
    gap = {"date": "2026-10-26", "layer": "L2", "detail": "无选股数据"}
    title, body = nf.format_message(
        status="repaired", today="2026-10-27", gaps=[gap],
        repaired=[{"layer": "L2", "dates": ["2026-10-26"], "reason": "补算 1 天的选股"}],
        remaining=[], fixed=[gap])
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
    assert nf.decide_status(remaining=[], fixed=[], no_db=True) == "manual"
    title, body = nf.format_message(
        status="manual", today="2026-10-27", gaps=[], repaired=[],
        remaining=[], no_db=True)
    assert "🚨" in title
    assert "数据库" in body


def test_status_is_decided_by_evidence_only():
    """状态只由证据决定：差集非空才叫已修复，有剩余缺口一律需人工。"""
    gap = {"date": "d", "layer": "L2", "detail": ""}
    assert nf.decide_status(remaining=[], fixed=[]) == "pass"
    assert nf.decide_status(remaining=[], fixed=[gap]) == "repaired"
    assert nf.decide_status(remaining=[gap], fixed=[]) == "manual"
    # 有剩余时就算差集非空（部分修好）也只能报需人工
    assert nf.decide_status(remaining=[gap], fixed=[gap]) == "manual"


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


def test_main_recheck_clean_with_successful_repair_is_repaired(tmp_path, monkeypatch):
    """原报告有缺口、复检报告干净、修复动作成功 → 🛠（Task 6 健康路径）。"""
    pre = _write_json(tmp_path, "audit.json", {
        "today": "2026-10-27",
        "gaps": [{"date": "2026-10-26", "layer": "L2", "detail": "无选股"}],
    })
    post = _write_json(tmp_path, "audit2.json", {
        "today": "2026-10-27",
        "expected_dates": ["2026-10-26"],
        "gaps": [],
    })
    repair = _write_json(tmp_path, "repair.json", {
        "today": "2026-10-27",
        "skipped": [],
        "actions": [{"layer": "L2", "dates": ["2026-10-26"], "reason": "补算 1 天的选股"}],
        "outcomes": [{"layer": "L2", "dates": ["2026-10-26"], "ok": True,
                      "reason": "补算 1 天的选股"}],
    })
    sent = _capture_post(monkeypatch)

    assert nf.main(["--audit", str(post), "--audit-pre", str(pre),
                    "--repair", str(repair), "--topic", "t"]) == 0
    assert sent["title"].startswith("🛠")
    assert "2026-10-26" in sent["body"] and "L2" in sent["body"]
    assert "复检确认" in sent["body"]


def test_main_pre_gaps_outside_recheck_window_stay_in_remaining(
        tmp_path, monkeypatch, capsys):
    """复检窗口没覆盖原缺口：「没检查」不能算修好，更不能让缺口从通知消失。

    超窗（>7 天）缺口正是 repair_gaps 永远不修、最需要人看的一类，
    必须并入「剩余」报警（🚨），而不是被一句 ✅ 掩盖。
    """
    pre = _write_json(tmp_path, "audit.json", {
        "today": "2026-10-27",
        "gaps": [{"date": "2026-10-01", "layer": "L1", "detail": "无行情"}],
    })
    post = _write_json(tmp_path, "audit2.json", {
        "today": "2026-10-27",
        "expected_dates": ["2026-10-27"],
        "gaps": [],
    })
    sent = _capture_post(monkeypatch)

    assert nf.main(["--audit", str(post), "--audit-pre", str(pre),
                    "--topic", "t"]) == 0
    assert sent["title"].startswith("🚨")
    assert "剩余：L1 2026-10-01" in sent["body"]
    assert "复检未覆盖该日期" in sent["body"]
    assert "已修复：L1" not in sent["body"]     # 没检查过就不能叫已修复
    assert "未覆盖" in capsys.readouterr().err


def test_main_clean_recheck_with_ok_outcome_alone_is_pass(tmp_path, monkeypatch):
    """只有复检报告 + ok outcome 没有「已修复」的证据：ok 只等于退出码 0 → ✅。"""
    post = _write_json(tmp_path, "audit2.json",
                       {"today": "2026-10-27", "gaps": []})
    repair = _write_json(tmp_path, "repair.json", {
        "today": "2026-10-27",
        "skipped": [],
        "outcomes": [{"layer": "L2", "dates": ["2026-10-26"], "ok": True,
                      "reason": "补算 1 天的选股"}],
    })
    sent = _capture_post(monkeypatch)

    assert nf.main(["--audit", str(post), "--repair", str(repair),
                    "--topic", "t"]) == 0
    assert sent["title"].startswith("✅")
    # 不能拿 ok outcome 的 dates 凭空造出「发现」
    assert "发现：" not in sent["body"]
    assert "L2 2026-10-26" not in sent["body"]


def test_main_recheck_diff_distinguishes_repaired_from_pass(tmp_path, monkeypatch):
    """原报告有缺口 + 复检干净 = 差集证明已修复 → 🛠；单份干净 = ✅。"""
    pre = _write_json(tmp_path, "audit.json", {
        "today": "2026-10-27",
        "gaps": [{"date": "2026-10-26", "layer": "L2", "detail": "无选股"}],
    })
    post = _write_json(tmp_path, "audit2.json", {
        "today": "2026-10-27",
        "expected_dates": ["2026-10-26"],
        "gaps": [],
    })
    sent = _capture_post(monkeypatch)

    assert nf.main(["--audit", str(post), "--audit-pre", str(pre),
                    "--topic", "t"]) == 0
    assert sent["title"].startswith("🛠")
    assert "发现：L2 2026-10-26" in sent["body"]

    # 同一份洁净复检不带原报告时没有差集，只能报通过
    assert nf.main(["--audit", str(post), "--topic", "t"]) == 0
    assert sent["title"].startswith("✅")


# ---------------------------------------------------------------- 六场景回归

_X = {"date": "2026-10-26", "layer": "L2", "detail": "无选股"}
_OK_REPAIR = {"layer": "L2", "dates": ["2026-10-26"], "ok": True,
              "reason": "补算 1 天的选股"}


def _scenario_args(tmp_path, case):
    """按评审给出的 a/b/c/d/e/i 造输入，返回 (argv, 期望标题, 必含, 必不含)。"""
    pre = post = repair = None
    if case in ("a", "b", "d", "e"):
        pre = {"today": "2026-10-27", "gaps": [dict(_X)]}
    elif case == "i":
        pre = {"today": "2026-10-27", "gaps": []}

    if case == "b":
        post = {"today": "2026-10-27", "expected_dates": ["2026-10-26"],
                "gaps": [dict(_X)]}
    else:
        post = {"today": "2026-10-27", "expected_dates": ["2026-10-26"],
                "gaps": []}

    if case in ("a", "b", "c"):
        repair = {"today": "2026-10-27", "skipped": [],
                  "outcomes": [dict(_OK_REPAIR)]}
    elif case == "e":
        # 该缺口被 repair skipped：没有成功 outcome，但复检确认它已消失
        repair = {"today": "2026-10-27",
                  "skipped": [dict(_X, reason="超窗未自动修")],
                  "outcomes": []}

    argv: list[str] = []
    if pre is not None:
        argv += ["--audit-pre", str(_write_json(tmp_path, "pre.json", pre))]
    if post is not None:
        argv += ["--audit", str(_write_json(tmp_path, "post.json", post))]
    if repair is not None:
        argv += ["--repair", str(_write_json(tmp_path, "repair.json", repair))]

    expectations = {
        # a: 原缺口 X + 复检干净 + ok → 🛠（差集是修复证据）
        "a": ("🛠", ["发现：L2 2026-10-26", "复检确认这些缺口已消失"], []),
        # b: 复检仍有 X + ok → 🚨 且剩余含 X
        "b": ("🚨", ["剩余：L2 2026-10-26"], ["已修复：L2"]),
        # c: 只有一份干净复检 + ok → ✅，不能拿 ok 的 dates 造「发现」
        "c": ("✅", ["巡检通过"], ["发现：", "L2 2026-10-26"]),
        # d: 原缺口 + 复检确认消失（无修复信息）→ 🛠，不编「已自动补齐」
        "d": ("🛠", ["发现：L2 2026-10-26", "修复记录缺失，复检确认缺口已消失"],
              ["已自动补齐"]),
        # e: 原缺口被 repair skipped + 复检确认消失 → 🛠，同样不编动作
        "e": ("🛠", ["发现：L2 2026-10-26", "修复记录缺失，复检确认缺口已消失"],
              ["已自动补齐"]),
        # i: 原/复检全干净、没有修复 → ✅
        "i": ("✅", ["巡检通过"], ["发现：", "修复："]),
    }
    expected, must, must_not = expectations[case]
    return argv, expected, must, must_not


@pytest.mark.parametrize("case", ["a", "b", "c", "d", "e", "i"])
def test_review_scenarios_a_b_c_d_e_i(case, tmp_path, monkeypatch):
    argv, expected, must, must_not = _scenario_args(tmp_path, case)
    sent = _capture_post(monkeypatch)

    assert nf.main(argv + ["--topic", "t"]) == 0
    assert sent["title"].startswith(expected), (case, sent["title"])
    for needle in must:
        assert needle in sent["body"], (case, sent["body"])
    for needle in must_not:
        assert needle not in sent["body"], (case, sent["body"])


def test_main_unrelated_ok_outcome_is_not_repair_evidence(
        tmp_path, monkeypatch):
    """ok outcome 的日期不在差集里 → 不能把它的 reason 贴成这次修复的动作。"""
    pre = _write_json(tmp_path, "pre.json", {
        "today": "2026-10-27",
        "gaps": [dict(_X)],
    })
    post = _write_json(tmp_path, "post.json", {
        "today": "2026-10-27",
        "expected_dates": ["2026-10-26"],
        "gaps": [],
    })
    repair = _write_json(tmp_path, "repair.json", {
        "today": "2026-10-27", "skipped": [],
        "outcomes": [{"layer": "L3", "dates": ["2026-10-25"], "ok": True,
                      "reason": "补算 L3 计划"}],
    })
    sent = _capture_post(monkeypatch)

    assert nf.main(["--audit", str(post), "--audit-pre", str(pre),
                    "--repair", str(repair), "--topic", "t"]) == 0
    assert sent["title"].startswith("🛠")
    assert "修复记录缺失，复检确认缺口已消失" in sent["body"]
    assert "补算 L3 计划" not in sent["body"]


def test_main_outside_gap_survives_ok_outcome(tmp_path, monkeypatch):
    """复检没覆盖的缺口，即使修复命令 ok 也不能从「剩余」里消失。"""
    pre = _write_json(tmp_path, "pre.json", {
        "today": "2026-10-27",
        "gaps": [{"date": "2026-10-01", "layer": "L1", "detail": "无行情"}],
    })
    post = _write_json(tmp_path, "post.json", {
        "today": "2026-10-27",
        "expected_dates": ["2026-10-27"],
        "gaps": [],
    })
    repair = _write_json(tmp_path, "repair.json", {
        "today": "2026-10-27", "skipped": [],
        "outcomes": [{"layer": "L1", "dates": ["2026-10-01"], "ok": True,
                      "reason": "补抓行情退出码 0"}],
    })
    sent = _capture_post(monkeypatch)

    assert nf.main(["--audit", str(post), "--audit-pre", str(pre),
                    "--repair", str(repair), "--topic", "t"]) == 0
    assert sent["title"].startswith("🚨")
    assert "剩余：L1 2026-10-01" in sent["body"]


def test_main_empty_expected_dates_covers_nothing(tmp_path, monkeypatch):
    """`expected_dates: []` 是「没检查任何日期」，不能当成「不设限」。"""
    pre = _write_json(tmp_path, "pre.json", {
        "today": "2026-10-27",
        "gaps": [{"date": "2026-10-01", "layer": "L1", "detail": "无行情"}],
    })
    post = _write_json(tmp_path, "post.json", {
        "today": "2026-10-27", "expected_dates": [], "gaps": [],
    })
    sent = _capture_post(monkeypatch)

    assert nf.main(["--audit", str(post), "--audit-pre", str(pre),
                    "--topic", "t"]) == 0
    assert sent["title"].startswith("🚨")
    assert "剩余：L1 2026-10-01" in sent["body"]


def test_main_audit_pre_alone_warns_and_is_single_report(
        tmp_path, monkeypatch, capsys):
    """只写 --audit-pre 是参数写串：按单份报告判定，并明确 warning。"""
    report = _write_json(tmp_path, "pre.json", {
        "today": "2026-10-27", "gaps": [],
    })
    repair = _write_json(tmp_path, "repair.json", {
        "today": "2026-10-27", "skipped": [],
        "outcomes": [dict(_OK_REPAIR)],
    })
    sent = _capture_post(monkeypatch)

    assert nf.main(["--audit-pre", str(report), "--repair", str(repair),
                    "--topic", "t"]) == 0
    assert sent["title"].startswith("✅")
    assert "只传了 --audit-pre" in capsys.readouterr().err


def test_main_ok_outcome_with_recheck_residual_gap_is_manual(tmp_path, monkeypatch):
    """回归：ok 只等于命令退出码为 0，不能把复检仍缺的日期抹成「已修复」。"""
    pre = _write_json(tmp_path, "audit.json", {
        "today": "2026-10-27",
        "gaps": [{"date": "2026-10-26", "layer": "L2", "detail": "无选股"}],
    })
    post = _write_json(tmp_path, "audit2.json", {
        "today": "2026-10-27",
        "gaps": [{"date": "2026-10-27", "layer": "L2", "detail": "无选股"}],
    })
    repair = _write_json(tmp_path, "repair.json", {
        "today": "2026-10-27",
        "skipped": [],
        "outcomes": [{"layer": "L2", "dates": ["2026-10-26", "2026-10-27"],
                      "ok": True, "reason": "补算选股（退出码 0）"}],
    })
    sent = _capture_post(monkeypatch)

    assert nf.main(["--audit", str(post), "--audit-pre", str(pre),
                    "--repair", str(repair), "--topic", "t"]) == 0
    assert sent["title"].startswith("🚨")
    assert "2026-10-27" in sent["body"]           # 复检仍缺的必须在「剩余」里
    assert "已修复：L2 2026-10-26" in sent["body"]  # 真的消失的才叫已修复


def test_main_ok_outcome_without_recheck_cannot_claim_repaired(tmp_path, monkeypatch):
    """只有一份报告且仍有缺口时，ok outcome 不能作为「已修复」的证据。"""
    audit = _write_json(tmp_path, "audit.json", {
        "today": "2026-10-27",
        "gaps": [{"date": "2026-10-26", "layer": "L2", "detail": "无选股"}],
    })
    repair = _write_json(tmp_path, "repair.json", {
        "today": "2026-10-27",
        "skipped": [],
        "outcomes": [{"layer": "L2", "dates": ["2026-10-26"], "ok": True,
                      "reason": "补算 1 天的选股"}],
    })
    sent = _capture_post(monkeypatch)

    assert nf.main(["--audit", str(audit), "--repair", str(repair),
                    "--topic", "t"]) == 0
    assert sent["title"].startswith("🚨")
    assert "剩余：L2 2026-10-26" in sent["body"]


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


def test_main_malformed_gaps_alarms_and_returns_2(tmp_path, monkeypatch, capsys):
    audit = _write_json(tmp_path, "audit.json", {
        "today": "2026-10-27",
        "gaps": [{"date": "2026-10-26"}],          # 缺 layer
    })
    sent = _capture_post(monkeypatch)
    assert nf.main(["--audit", str(audit), "--topic", "t"]) == 2
    assert sent["title"].startswith("🚨")
    assert "体检报告不可用" in sent["title"]       # 报告存在但形状坏，不是「没有报告」
    assert "date/layer" in sent["body"]
    # 具体错误 + 通用告警不能各打一条红注释
    assert capsys.readouterr().err.count("::error::") == 1


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
    """reason 为空时不能渲染出「修复：；」，也不能编一个「已自动补齐」。"""
    gap = {"date": "2026-10-26", "layer": "L2", "detail": "无选股"}
    title, body = nf.format_message(
        status="repaired", today="2026-10-27", gaps=[gap],
        repaired=[{"layer": "L2", "dates": ["2026-10-26"], "reason": ""}],
        remaining=[], fixed=[gap])
    assert "修复：；" not in body
    assert "修复：修复动作成功（修复记录未说明具体动作）" in body
    assert "已自动补齐" not in body


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


# ---------------------------------------------------------------- 坏类型收敛


def test_reasons_text_normalizes_non_string_reason():
    """`reason: 123` 直接进 '；'.join 会 TypeError，炸掉整条通知。"""
    assert nf._reasons_text([{"reason": 123}], "回退") == "123"
    assert nf._reasons_text([{"reason": None}], "回退") == "回退"


def test_outcome_ok_must_be_real_bool():
    """`ok: "false"` 是真值字符串，但绝不能算修复成功。"""
    gaps = [{"date": "2026-10-26", "layer": "L2", "detail": "无选股"}]
    outcomes = [{"layer": "L2", "dates": ["2026-10-26"], "ok": "false",
                 "reason": 123}]
    remaining = nf._remaining_gaps(gaps, outcomes, [])
    assert [(g["layer"], g["date"]) for g in remaining] == [("L2", "2026-10-26")]
    assert remaining[0]["reason"] == "123"


def test_remaining_gaps_ignores_unhashable_layer():
    """坏 layer（list）不能当 dict key 炸掉通知，也不能配上原因。"""
    gaps = [{"date": "2026-10-26", "layer": "L2", "detail": "无选股"}]
    outcomes = [{"layer": ["L2"], "dates": ["2026-10-26"], "ok": False}]
    remaining = nf._remaining_gaps(gaps, outcomes, [])
    assert remaining == gaps


def test_main_bad_reason_type_does_not_break_notification(tmp_path, monkeypatch):
    """修复信息里 reason 是数字时，通知仍要发出来（只降级，不 rc=1）。"""
    pre = _write_json(tmp_path, "audit.json", {
        "today": "2026-10-27",
        "gaps": [{"date": "2026-10-26", "layer": "L2", "detail": "无选股"}],
    })
    post = _write_json(tmp_path, "audit2.json",
                       {"today": "2026-10-27", "gaps": []})
    repair = _write_json(tmp_path, "repair.json", {
        "today": "2026-10-27",
        "skipped": [],
        "outcomes": [{"layer": "L2", "dates": ["2026-10-26"], "ok": True,
                      "reason": 123}],
    })
    sent = _capture_post(monkeypatch)

    assert nf.main(["--audit", str(post), "--audit-pre", str(pre),
                    "--repair", str(repair), "--topic", "t"]) == 0
    assert sent["title"].startswith("🛠")
    assert "123" in sent["body"]


# ---------------------------------------------------------------- 其余 Minor


def test_main_ntfy_failure_still_rc0_and_writes_summary(tmp_path, monkeypatch):
    """推送失败不能拖垮巡检：Summary 已写、退出码仍为 0。"""
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(nf, "post_ntfy", lambda *a, **k: False)
    audit = _write_json(tmp_path, "audit.json",
                        {"today": "2026-10-27", "gaps": []})

    assert nf.main(["--audit", str(audit), "--topic", "t"]) == 0
    assert "✅" in summary.read_text(encoding="utf-8")


def test_main_warns_when_report_today_missing(tmp_path, monkeypatch, capsys):
    audit = _write_json(tmp_path, "audit.json", {"gaps": []})
    _capture_post(monkeypatch)
    assert nf.main(["--audit", str(audit), "--topic", "t"]) == 0
    assert "缺少合法 today" in capsys.readouterr().err


def test_main_warns_on_non_positive_window_days(tmp_path, monkeypatch, capsys):
    audit = _write_json(tmp_path, "audit.json",
                        {"today": "2026-10-27", "window_days": 60, "gaps": []})
    sent = _capture_post(monkeypatch)
    assert nf.main(["--audit", str(audit), "--window-days", "0",
                    "--topic", "t"]) == 0
    assert "正整数" in capsys.readouterr().err
    assert "近 60 天" in sent["body"]       # 退回报告自带值，不是静默用 30


def test_main_bad_pre_report_is_fatal(tmp_path, monkeypatch, capsys):
    """原报告读不到 = 致命错误（I-1）：绝不静默降级成「没有对照」。

    2026-10-28 评审复现的失败形态：体检有缺口但报告没落盘 → 修复步拿不到报告 →
    复检干净 → 通知步传 `--audit audit2.json --audit-pre audit.json`，原报告读不到
    就静默降级成「没有对照」→ discovered=[] → 「✅ 巡检通过」。
    显式传了 --audit-pre 却读不到，说明体检那一步没落盘，必须按致命错误处理。
    """
    post = _write_json(tmp_path, "audit2.json",
                       {"today": "2026-10-27", "gaps": []})
    sent = _capture_post(monkeypatch)
    assert nf.main(["--audit", str(post),
                    "--audit-pre", str(tmp_path / "nope.json"),
                    "--topic", "t"]) == 2
    assert sent["title"].startswith("🚨")
    assert "✅" not in sent["body"]
    assert "巡检通过" not in sent["body"]
    assert "读取" in capsys.readouterr().err


# ---------------------------------------------------------------- I-1 回归


def test_main_missing_audit_pre_never_reports_pass(tmp_path, monkeypatch):
    """I-1 核心回归：体检有缺口但报告没落盘 → 通知不许报通过。

    场景照抄评审复现：体检 rc=1（有缺口）但报告没写出来；复检报告干净；
    通知拿到「原报告路径」却读不到 → 绝不能把 discovered 降级成空列表然后发 ✅。
    """
    post = _write_json(tmp_path, "audit2.json", {
        "today": "2026-10-27",
        "expected_dates": ["2026-10-26"],
        "gaps": [],
    })
    missing_pre = tmp_path / "audit.json"          # 体检没落盘
    sent = _capture_post(monkeypatch)

    rc = nf.main(["--audit", str(post), "--audit-pre", str(missing_pre),
                  "--today", "2026-10-27", "--topic", "t"])

    assert rc == 2
    assert sent["title"].startswith("🚨")
    assert "✅" not in sent["body"]
    assert "巡检通过" not in sent["body"]


def test_main_malformed_audit_pre_is_fatal(tmp_path, monkeypatch, capsys):
    """原报告存在但形状坏（gaps 不是对象数组）同样不能降级 —— 对照不可信。"""
    post = _write_json(tmp_path, "audit2.json",
                       {"today": "2026-10-27", "gaps": []})
    pre = _write_json(tmp_path, "audit.json", {"today": "2026-10-27", "gaps": ""})
    sent = _capture_post(monkeypatch)

    rc = nf.main(["--audit", str(post), "--audit-pre", str(pre), "--topic", "t"])

    assert rc == 2
    assert sent["title"].startswith("🚨")
    assert "✅" not in sent["body"]
    assert capsys.readouterr().err.count("::error::") == 1


# ---------------------------------------------------------------- I-2 副作用


def test_main_mentions_side_effects_when_repair_changed_portfolio(tmp_path, monkeypatch):
    """修复改写了组合状态 → 通知正文必须说清楚（组合页数字会在夜里自己变）。"""
    pre = _write_json(tmp_path, "audit.json", {
        "today": "2026-10-27",
        "gaps": [{"date": "2026-10-26", "layer": "L2", "detail": "无选股"}],
    })
    post = _write_json(tmp_path, "audit2.json", {
        "today": "2026-10-27",
        "expected_dates": ["2026-10-26"],
        "gaps": [],
    })
    repair = _write_json(tmp_path, "repair.json", {
        "today": "2026-10-27", "skipped": [],
        "outcomes": [dict(_OK_REPAIR)],
        "side_effects": {
            "open_positions": {"before": 535, "after": 542, "delta": 7},
            "trade_events": {"before": 1048, "after": 1055, "delta": 7},
        },
    })
    sent = _capture_post(monkeypatch)

    assert nf.main(["--audit", str(post), "--audit-pre", str(pre),
                    "--repair", str(repair), "--topic", "t"]) == 0
    assert "副作用" in sent["body"]
    assert "open_positions" in sent["body"]
    assert "trade_events" in sent["body"]
    assert "+7" in sent["body"]
    assert "组合页" in sent["body"]


def test_main_no_side_effect_line_when_nothing_changed(tmp_path, monkeypatch):
    """没改写组合状态时不加副作用行（否则每条通知都多一句噪音）。"""
    pre = _write_json(tmp_path, "audit.json", {
        "today": "2026-10-27",
        "gaps": [{"date": "2026-10-26", "layer": "L2", "detail": "无选股"}],
    })
    post = _write_json(tmp_path, "audit2.json", {
        "today": "2026-10-27",
        "expected_dates": ["2026-10-26"],
        "gaps": [],
    })
    repair = _write_json(tmp_path, "repair.json", {
        "today": "2026-10-27", "skipped": [],
        "outcomes": [dict(_OK_REPAIR)],
        "side_effects": {
            "open_positions": {"before": 535, "after": 535, "delta": 0},
            "trade_events": {"before": 1048, "after": 1048, "delta": 0},
        },
    })
    sent = _capture_post(monkeypatch)

    assert nf.main(["--audit", str(post), "--audit-pre", str(pre),
                    "--repair", str(repair), "--topic", "t"]) == 0
    assert "副作用" not in sent["body"]


def test_main_bad_side_effects_shape_is_ignored_not_fatal(tmp_path, monkeypatch):
    """side_effects 形状坏了只降级（修复信息是补充），不能因此不发通知。"""
    audit = _write_json(tmp_path, "audit.json", {
        "today": "2026-10-27",
        "gaps": [{"date": "2026-10-26", "layer": "L2", "detail": "无选股"}],
    })
    repair = _write_json(tmp_path, "repair.json", {
        "today": "2026-10-27", "skipped": [],
        "outcomes": [dict(_OK_REPAIR)],
        "side_effects": {"open_positions": "很多"},      # 形状坏
    })
    sent = _capture_post(monkeypatch)

    assert nf.main(["--audit", str(audit), "--repair", str(repair),
                     "--topic", "t"]) == 0
    assert sent["title"].startswith("🚨")
    assert "副作用" not in sent["body"]


# ---------------------------------------------------------------- M-4 非交易日


def test_non_trading_day_is_annotated_in_notification_body(tmp_path, monkeypatch):
    """M-4：非交易日标注必须进通知正文（ntfy 推送），不能只写 Step Summary。

    只写 Step Summary 的话，手机上收到的推送仍会被读成「今天的数据已更新」。
    """
    audit = _write_json(tmp_path, "audit.json",
                        {"today": "2026-10-27", "gaps": []})
    sent = _capture_post(monkeypatch)

    assert nf.main(["--audit", str(audit), "--non-trading-day",
                    "--topic", "t"]) == 0
    assert "非交易日" in sent["body"]
    assert "仅巡检历史" in sent["body"]


def test_trading_day_has_no_non_trading_annotation(tmp_path, monkeypatch):
    """交易日不标「非交易日」（否则每个心跳都挂一句假注解）。"""
    audit = _write_json(tmp_path, "audit.json",
                        {"today": "2026-10-27", "gaps": []})
    sent = _capture_post(monkeypatch)

    assert nf.main(["--audit", str(audit), "--topic", "t"]) == 0
    assert "非交易日" not in sent["body"]


@pytest.mark.parametrize("status", ["pass", "repaired", "manual"])
def test_non_trading_day_annotation_covers_all_statuses(status):
    """三种结局都要带上非交易日标注（否则 🚨/🛠 时又看不出来了）。"""
    gap = {"date": "2026-10-26", "layer": "L2", "detail": "无选股"}
    title, body = nf.format_message(
        status=status, today="2026-10-27", gaps=[gap],
        repaired=[], remaining=[gap], non_trading_day=True)
    assert "非交易日，仅巡检历史" in body
