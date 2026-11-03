"""a-finder.sh 的 refresh-agent 子命令契约（只查结构，不真起进程）。"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SH = ROOT / "a-finder.sh"


def _text():
    return SH.read_text(encoding="utf-8")


def _fn():
    m = re.search(r"^cmd_refresh_agent\(\) \{(.*?)^\}", _text(), re.S | re.M)
    assert m, "cmd_refresh_agent 函数体找不到"
    return m.group(1)


def test_dispatcher_has_refresh_agent_branch():
    """dispatcher 必须能分派 refresh-agent。"""
    assert re.search(r"refresh-agent\)\s*shift;\s*cmd_refresh_agent", _text()), \
        "dispatcher 缺 refresh-agent 分支"


def test_cmd_refresh_agent_supports_start_stop_status():
    body = _fn()
    for action in ("start", "stop", "status"):
        assert action in body, f"缺 {action} 分支"


def test_cmd_refresh_agent_uses_pid_file():
    """用 pid 文件管理进程（与既有 cmd_web 一致，别自创一套）。"""
    body = _fn()
    assert ".refresh-agent.pid" in body, "缺 pid 文件"
    assert "nohup" in body, "缺 nohup 后台启动"


def test_cmd_refresh_agent_refuses_without_secret():
    """没有 REFRESH_AGENT_SECRET 就不能启动 —— 否则 agent 起来也是 401。"""
    body = _fn()
    assert "REFRESH_AGENT_SECRET" in body
    # 必须用 ${VAR:-} 的写法（bash 3.2 + set -u 下裸 $VAR 会 unbound variable）
    assert "REFRESH_AGENT_SECRET:-" in body, "缺 secret 兜底检查（要用花括号+:-）"


def test_help_mentions_refresh_agent():
    assert "refresh-agent" in _text()
