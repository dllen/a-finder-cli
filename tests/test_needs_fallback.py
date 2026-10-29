"""本地兜底判定脚本（scripts/needs_fallback.py）的测试。"""

import importlib.util
import json
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "needs_fallback",
    Path(__file__).resolve().parent.parent / "scripts" / "needs_fallback.py",
)
nf = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(nf)


def _write(tmp_path, dates):
    p = tmp_path / "dates.json"
    p.write_text(json.dumps({"dates": dates}), encoding="utf-8")
    return str(p)


def test_up_to_date_needs_no_fallback(tmp_path):
    path = _write(tmp_path, ["2026-10-27", "2026-10-26"])
    assert nf.main(["--dates-json", path, "--deadline", "00:00", "--today", "2026-10-27"]) == 0


def test_stale_needs_fallback(tmp_path):
    path = _write(tmp_path, ["2026-10-23", "2026-10-22"])
    assert nf.main(["--dates-json", path, "--deadline", "00:00", "--today", "2026-10-27"]) == 1


def test_empty_dates_needs_fallback(tmp_path):
    path = _write(tmp_path, [])
    assert nf.main(["--dates-json", path, "--deadline", "00:00", "--today", "2026-10-27"]) == 1


def test_missing_file_returns_error_code(tmp_path):
    assert nf.main(["--dates-json", str(tmp_path / "nope.json"), "--deadline", "00:00"]) == 2


def test_weekend_expects_last_trading_day(tmp_path):
    """周日跑 → 期望值是上周五，线上周五数据即算最新。"""
    path = _write(tmp_path, ["2026-10-23"])
    # 2026-10-25 是周日
    assert nf.main(["--dates-json", path, "--deadline", "00:00", "--today", "2026-10-25"]) == 0


def test_decide_messages():
    need, reason = nf.decide("2026-10-23", "2026-10-27")
    assert need and "落后" in reason
    need, reason = nf.decide("2026-10-27", "2026-10-27")
    assert not need and "最新" in reason
    need, reason = nf.decide(None, "2026-10-27")
    assert need and "为空" in reason


# ---------------------------------------------------------------------------
# deadline 门：本地兜底只该在「CI 已经放弃了」时动手
#
# 2026-10-29 两谱系事故的另一半：CI 在 15:30–17:00 北京有 8 个班次。本地若在 CI
# 还没跑完时抢先发布，线上就有了当天日期 —— 而 CI 的（修复前）判据正是「线上有
# 没有当天」，于是 CI 判 skip、连缓存都不保存，谱系继续冻结。
# 也就是说：**本地抢跑本身就在制造分叉**。
# 所以语义必须是「CI 已经放弃了，我来救」，不是「谁先到谁发」。
# ---------------------------------------------------------------------------


def test_before_deadline_does_not_fallback_even_if_stale(tmp_path):
    """没到 deadline：即使线上落后也不动作 —— 让 CI 先跑完它的 8 个班次。"""
    path = _write(tmp_path, ["2026-10-23"])
    # 用一个必然还没到的 deadline（23:59），今天是 10-27
    assert nf.main(["--dates-json", path, "--deadline", "23:59",
                    "--today", "2026-10-27"]) == 0


def test_after_deadline_falls_back_when_stale(tmp_path):
    """过了 deadline 且线上仍落后 → 判定 CI 真的没成，本地兜底。"""
    path = _write(tmp_path, ["2026-10-23"])
    assert nf.main(["--dates-json", path, "--deadline", "00:00",
                    "--today", "2026-10-27"]) == 1


def test_before_deadline_skips_even_when_dates_missing(tmp_path):
    """deadline 门在**所有**判定之前：连「线上为空」也不该提前抢跑。

    线上为空是本地在 CI 首次发布前抢跑的典型场景 —— 这时候更该等 CI。
    """
    path = _write(tmp_path, [])
    assert nf.main(["--dates-json", path, "--deadline", "23:59",
                    "--today", "2026-10-27"]) == 0


def test_force_bypasses_deadline(tmp_path):
    """--force 用于排障/手动补跑：绕过 deadline 门。"""
    path = _write(tmp_path, ["2026-10-23"])
    assert nf.main(["--dates-json", path, "--deadline", "23:59",
                    "--today", "2026-10-27", "--force"]) == 1


def test_past_deadline_helper():
    """past_deadline 的边界：正好到点算过了。"""
    import datetime as dt
    assert nf.past_deadline(dt.datetime(2026, 10, 27, 16, 59), "17:00") is False
    assert nf.past_deadline(dt.datetime(2026, 10, 27, 17, 0), "17:00") is True
    assert nf.past_deadline(dt.datetime(2026, 10, 27, 17, 1), "17:00") is True
    assert nf.past_deadline(dt.datetime(2026, 10, 27, 9, 30), "08:00") is True


def test_malformed_deadline_is_parameter_error_not_fallback(tmp_path):
    """非法 --deadline 必须 rc=2（不兜底），绝不能 fail-open 成 1。

    退出码 1 的语义是「需要兜底」。若 `--deadline 25:00` 因 ValueError 退出成 1，
    一个笔误就绕过了整道防双写门 → 本地立刻抢跑发布。
    """
    path = _write(tmp_path, ["2026-10-23"])
    for bad in ("25:00", "abc", "", "17:99", "1:2:3"):
        assert nf.main(["--dates-json", path, "--deadline", bad,
                        "--today", "2026-10-27"]) == 2, bad


def test_valid_deadline_still_works(tmp_path):
    path = _write(tmp_path, ["2026-10-23"])
    assert nf.main(["--dates-json", path, "--deadline", "00:00",
                    "--today", "2026-10-27"]) == 1
    assert nf.main(["--dates-json", path, "--deadline", "23:59",
                    "--today", "2026-10-27"]) == 0
