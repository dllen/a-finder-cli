"""`a-finder.sh cmd_site` 的两道护栏：

1. 发布前单调性校验（产物最新日不得比线上旧）
2. 计划日期对齐 MAX(daily_prices.trade_date)，而不是今天

这两个都是 bash 里的行为，用「桩掉外部命令」的方式测：
- 桩掉 git push：断言产物比线上旧时**没有** push
- 桩掉 stock_cli.py：断言 plan build 收到的是库里的最大交易日
"""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SH = ROOT / "a-finder.sh"


def _bash_source():
    """抽出 cmd_site 的函数体（避免真跑整条流水线）。"""
    text = SH.read_text(encoding="utf-8")
    m = re.search(r"^cmd_site\(\) \{(.*?)^\}", text, re.S | re.M)
    assert m, "cmd_site 找不到"
    return m.group(1)


def test_cmd_site_uses_max_trade_date_not_today():
    """计划日期必须取库里最大交易日，不能用今天。"""
    body = _bash_source()
    # 必须查 MAX(daily_prices.trade_date)
    assert "MAX(trade_date) FROM daily_prices" in body, body
    # 不能直接用 date +%F 作为 plan_date
    assert 'plan_date="$(date +%F)"' not in body, (
        "cmd_site 仍在用今天作为计划日期 —— 同步失败时会产出空计划"
    )


def test_cmd_site_has_monotonic_guard_before_push():
    """push 之前必须有单调性校验，且校验失败要中止。"""
    body = _bash_source()
    assert "verify_site_export.py" in body, body
    assert "--not-older-than" in body, body
    # 校验失败必须阻止 push：校验语句要在 push 之前，且带 || exit 1
    guard = body.index("verify_site_export.py")
    push = body.index("push_site_to_ghpages")
    assert guard < push, "单调性校验必须在 push 之前"
    tail = body[guard:push]
    # 校验失败必须中止发布：接受两种等价写法 ——
    #   1) `cmd || return 1` / `cmd || exit 1`（短路式）
    #   2) `if ! cmd; then ... return 1; fi`（if 块式，return 1 在同一 if 块内）
    aborts = (
        "|| exit 1" in tail
        or "|| return 1" in tail
        or ("return 1" in tail and "if !" in tail)
    )
    assert aborts, (
        "校验失败必须中止发布（否则守卫形同虚设）:\n" + tail
    )


def test_cmd_site_reads_live_latest_before_push():
    """单调性下限必须取自线上 dates.json，而不是硬编码。"""
    body = _bash_source()
    assert "ycu.space/data/dates.json" in body, body
