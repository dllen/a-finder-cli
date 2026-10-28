"""每日巡检 workflow（daily-audit.yml）的结构回归。

为什么不用 YAML 解析器：venv 里没有 PyYAML（只有系统 python3 有），
用 importorskip 会让这些用例在本地/CI 静默跳过 —— 那等于没有测试。
这里按缩进切出每个 step 的文本块，用 `bash -n` 校验脚本语法
（语法错会直接让 workflow 挂掉，比字符串断言更值钱），再用文本断言守住行为。

覆盖的评审项（2026-10-28 最终评审）：
  I-1  体检步必须检查报告是否落盘 —— 没落盘 ≠ 无缺口，绝不能发绿色心跳
"""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "daily-audit.yml"


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _step_names(text: str):
    return [m.group(1).strip() for m in re.finditer(r"^\s*- name: (.+)$", text, re.M)]


def _step_block(text: str, name: str) -> str:
    """切出某个 `- name: X` 步骤的文本块（按缩进，遇到同级条目就停）。"""
    lines = text.splitlines()
    marker = f"- name: {name}"
    start = next((i for i, line in enumerate(lines) if line.strip() == marker), None)
    assert start is not None, f"workflow 里找不到步骤: {name}"
    indent = len(lines[start]) - len(lines[start].lstrip())
    out = [lines[start]]
    for line in lines[start + 1:]:
        if line.strip() and (len(line) - len(line.lstrip())) <= indent:
            break
        out.append(line)
    return "\n".join(out)


def _run_script(block: str) -> str:
    """取出步骤里 `run: |` 块的内容（可能有多个）。"""
    lines = block.splitlines()
    scripts = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped in ("run: |", "run: |-", "run: >", "run: >-"):
            indent = len(lines[i]) - len(lines[i].lstrip())
            i += 1
            body = []
            while i < len(lines):
                nxt = lines[i]
                if nxt.strip() and (len(nxt) - len(nxt.lstrip())) <= indent:
                    break
                body.append(nxt)
                i += 1
            scripts.append("\n".join(body))
            continue
        i += 1
    return "\n\n".join(scripts)


# ---------------------------------------------------------------- 语法


def test_every_run_block_passes_bash_syntax_check():
    """每个 run 块都要过 `bash -n`：语法错会让整条 workflow 直接挂掉。"""
    text = _workflow_text()
    checked = 0
    for name in _step_names(text):
        script = _run_script(_step_block(text, name))
        if not script.strip():
            continue
        checked += 1
        proc = subprocess.run(["bash", "-n"], input=script,
                              capture_output=True, text=True)
        assert proc.returncode == 0, (name, proc.stderr)
    assert checked >= 5, "应该有不少于 5 个带脚本的步骤"


# ---------------------------------------------------------------- I-1


def test_audit_step_fails_when_report_is_not_written():
    """体检步：报告没落盘 = 致命错误，不能当成「没有缺口」继续往下走。

    2026-10-28 评审复现：写盘失败 → 报告不存在 → 修复步/复检步/通知步一路
    降级 → 最后打印「✅ 巡检通过」，run 还是绿的。
    """
    block = _step_block(_workflow_text(), "三层体检")
    assert re.search(r"!\s+-s\s+/tmp/audit\.json", block), \
        "体检步必须检查 /tmp/audit.json 是否落盘（非空）"
    assert "::error::" in block, "报告没落盘必须留下红色注释"


def test_audit_step_does_not_swallow_nonzero_exit_code():
    """体检 rc=2（无法判定）必须让步骤失败，绝不能继续。"""
    block = _step_block(_workflow_text(), "三层体检")
    assert re.search(r'if\s+\[\s*"\$rc"\s+-eq\s+2\s*\]', block)
    assert re.search(r"exit 1", block)


def test_notify_step_passes_audit_pre_so_missing_report_is_fatal():
    """通知步要把原报告传出去（--audit-pre）：读不到时 notify 才会按致命错误报警。

    少了这个参数，notify 就无从知道原报告本该存在 —— 静默降级会造出假心跳。
    """
    block = _step_block(_workflow_text(), "通知")
    assert "--audit-pre" in block
    assert "/tmp/audit.json" in block


# ---------------------------------------------------------------- M-1 ~ M-4


def test_audit_repair_recheck_steps_pass_today_explicitly():
    """M-1：体检/修复/复检三步都要显式传 --today。

    CLAUDE.md 写着「workflow 里已显式传 --today，不受影响」，实际只有 notify
    收到了 —— 手工 workflow_dispatch 跨零点时，体检会把「今天」也算进应有
    交易日，报出 `[L1] 今天 行情只有 0 只` 这种假缺口。
    """
    text = _workflow_text()
    for name in ("三层体检", "自动修复", "复检"):
        block = _step_block(text, name)
        assert "--today" in block, f"{name} 步必须显式传 --today"
        assert "steps.trade-day.outputs.today_bj" in block, name


def test_repair_rc_is_actually_used():
    """M-2：repair_rc 采集了就得用起来 —— 修复失败也要让 run 变红，别留死输出。"""
    text = _workflow_text()
    if_lines = [line for line in text.splitlines() if line.strip().startswith("if:")]
    assert any("repair_rc" in line for line in if_lines), \
        "红灯条件里必须用上 repair_rc（否则它只是个没人看的输出）"


def test_trade_day_step_uses_shared_calendar():
    """M-3：交易日判定必须走 market_calendar.trading_days。

    手写一份 `is_workday(d) and d.weekday() < 5` 就是又一份日历口径 ——
    设计文档里「口径漂移的代价已经付过一次」说的就是这种事。
    """
    block = _step_block(_workflow_text(), "判定今天日期与是否交易日")
    assert "market_calendar" in block
    assert "trading_days" in block
    # 直接 import chinese_calendar 的 is_workday = 又一份手写口径（注释里提一句不算）
    assert "import is_workday" not in block
    assert "from chinese_calendar import" not in block


def test_notify_step_marks_non_trading_day():
    """M-4：非交易日标注要进通知正文（ntfy 推送），不能只写 Step Summary。"""
    block = _step_block(_workflow_text(), "通知")
    assert "--non-trading-day" in block
    assert "is_trade_day" in block
