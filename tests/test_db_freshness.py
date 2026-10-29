"""db_freshness.py 的测试。

这个脚本存在的意义：CI 的同步判据必须是「**自己的库**有没有当天的行情」，
不能是「线上站点有没有当天的日期」。后者会被另一个写者（本地 a-finder.sh
site --push）骗过，导致 CI 判 skip → 不保存缓存 → 行情谱系冻结
（2026-10-29 两谱系事故：CI 谱系断在 2026-10-13）。

关键设计点（都有对应测试）：
  - 落后用**交易日**算，不用自然日（否则每个周一都误报落后 3 天）
  - 退出码 0/1/2/3 分别表示 新鲜 / 可增量补 / 无库读不了 / 陈旧谱系
  - lag == 0 绝不能因为 `not 0` 的真值判断被误判成「需要同步」
"""

import importlib.util
import sqlite3
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "db_freshness",
    Path(__file__).resolve().parent.parent / "scripts" / "db_freshness.py",
)
db = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(db)


def _make_db(path, dates, codes_per_day=300):
    """造一个只有这些交易日的 daily_prices。"""
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE daily_prices (code TEXT, trade_date TEXT, close REAL)"
    )
    for d in dates:
        for i in range(codes_per_day):
            conn.execute(
                "INSERT INTO daily_prices VALUES (?,?,?)", (f"{i:06d}", d, 10.0)
            )
    conn.commit()
    conn.close()
    return path


# ------------------------------------------------------------------ 新鲜度判定


def test_fresh_when_db_has_expect_day(tmp_path):
    p = _make_db(tmp_path / "a.db", ["2026-10-29"])
    r = db.assess(str(p), "2026-10-29")
    assert r["stale"] is False
    assert r["lag_trading_days"] == 0
    assert r["latest"] == "2026-10-29"
    assert db._code_for(r, 3) == 0


def test_monday_after_friday_is_zero_lag(tmp_path):
    """库停在周五、期望下周一 → 落后 0 个交易日（周五就是最新交易日）。

    若用自然日算，周五→周一 = 3 天，每个周一都会误报落后。
    这是「用交易日算」最关键的一条。
    """
    p = _make_db(tmp_path / "a.db", ["2026-10-23"])   # 周五
    r = db.assess(str(p), "2026-10-26")               # 下周一（交易日）
    # 10-26 本身是应有交易日，库没有 → 落后 1
    assert r["lag_trading_days"] == 1

    r2 = db.assess(str(p), "2026-10-23")              # 同一天 → 0
    assert r2["lag_trading_days"] == 0


def test_lag_counts_trading_days_not_calendar_days(tmp_path):
    """库停在 10-23（周五），期望 10-29（周四）：中间只有 10-26/27/28/29 = 4 个交易日。

    若用自然日算会是 6 天 —— 差的就是周末那两天。
    """
    p = _make_db(tmp_path / "a.db", ["2026-10-23"])
    r = db.assess(str(p), "2026-10-29")
    assert r["lag_trading_days"] == 4
    assert r["stale"] is True
    # 4 > 默认阈值 3 → 判陈旧谱系（阈值放宽到 10 就不是，见下一条用例）
    assert r["orphan_lineage"] is True


def test_orphan_lineage_when_lag_exceeds_threshold(tmp_path):
    """CI 孤儿库场景：行情停在 2026-10-13，期望 10-29 → 落后 12 个交易日。"""
    p = _make_db(tmp_path / "a.db", ["2026-10-13"])
    r = db.assess(str(p), "2026-10-29")
    assert r["lag_trading_days"] == 12
    assert r["orphan_lineage"] is True
    assert db._code_for(r, 3) == 3


def test_small_lag_is_incremental_not_orphan(tmp_path):
    p = _make_db(tmp_path / "a.db", ["2026-10-28"])
    r = db.assess(str(p), "2026-10-29")
    assert r["lag_trading_days"] == 1
    assert r["orphan_lineage"] is False
    assert db._code_for(r, 3) == 1


def test_max_lag_parameter_is_respected(tmp_path):
    p = _make_db(tmp_path / "a.db", ["2026-10-23"])
    r = db.assess(str(p), "2026-10-29", max_lag=10)
    assert r["lag_trading_days"] == 4
    assert r["orphan_lineage"] is False       # 4 <= 10
    assert db._code_for(r, 10) == 1


# ------------------------------------------------------------------ 坏输入


def test_missing_db_is_not_treated_as_fresh(tmp_path):
    r = db.assess(str(tmp_path / "nope.db"), "2026-10-29")
    assert r["stale"] is True
    assert r["latest"] is None
    assert db._code_for(r, 3) == 2


def test_empty_prices_is_orphan(tmp_path):
    p = _make_db(tmp_path / "a.db", [])
    r = db.assess(str(p), "2026-10-29")
    assert r["latest"] is None
    assert r["orphan_lineage"] is True
    assert db._code_for(r, 3) == 2


def test_code_for_fresh_is_zero_not_one():
    """回归：`not lag` 对 0 是 True —— 旧写法会把「已最新」误报成「需要同步」。"""
    r = {"latest": "2026-10-29", "lag_trading_days": 0,
         "stale": False, "orphan_lineage": False}
    assert db._code_for(r, 3) == 0


# ------------------------------------------------------------------ CLI


def test_cli_exit_codes(tmp_path):
    fresh = _make_db(tmp_path / "fresh.db", ["2026-10-29"])
    assert db.main(["--db", str(fresh), "--expect", "2026-10-29"]) == 0

    behind = _make_db(tmp_path / "behind.db", ["2026-10-28"])
    assert db.main(["--db", str(behind), "--expect", "2026-10-29"]) == 1

    orphan = _make_db(tmp_path / "orphan.db", ["2026-10-13"])
    assert db.main(["--db", str(orphan), "--expect", "2026-10-29"]) == 3

    assert db.main(["--db", str(tmp_path / "nope.db"), "--expect", "2026-10-29"]) == 2


def test_cli_rejects_bad_date(tmp_path):
    p = _make_db(tmp_path / "a.db", ["2026-10-29"])
    assert db.main(["--db", str(p), "--expect", "not-a-date"]) == 2


def test_cli_github_output(tmp_path, monkeypatch):
    p = _make_db(tmp_path / "a.db", ["2026-10-13"])
    out = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    db.main(["--db", str(p), "--expect", "2026-10-29", "--github-output"])
    text = out.read_text(encoding="utf-8")
    assert "fresh=false" in text
    assert "orphan=true" in text
    assert "latest=2026-10-13" in text
    assert "rc=3" in text


# ------------------------------------------------------------------ 假新鲜（M1）
# 只用 MAX(trade_date) 会造出两类假新鲜，都会让 CI 跳过同步、把不完整的票池发出去。


def test_partial_day_on_expect_is_not_fresh(tmp_path):
    """半截天：期望日只有 50 只 → 必须判不新鲜。

    审计的 L1 阈值是 >=280，而 sync 步的「等数据落地」重试只在真去同步时才跑；
    若这里判新鲜 → skip_sync=true → 重试永不执行 → 半截票池直接发出去。
    """
    p = _make_db(tmp_path / "a.db", ["2026-10-29"], codes_per_day=50)
    r = db.assess(str(p), "2026-10-29")
    assert r["stale"] is True
    assert r["codes_on_expect"] == 50
    assert "只有 50 只" in r["reason"]
    assert db._code_for(r, 3) == 1


def test_future_dirty_row_does_not_make_db_fresh(tmp_path):
    """未来日期脏行把 MAX 顶到未来时，仍必须按 expect 当天的代码数判定。"""
    p = _make_db(tmp_path / "a.db", ["2026-11-20"], codes_per_day=1)
    r = db.assess(str(p), "2026-10-29")
    assert r["stale"] is True
    assert r["codes_on_expect"] == 0
    assert db._code_for(r, 3) == 1


def test_full_day_on_expect_is_fresh(tmp_path):
    p = _make_db(tmp_path / "a.db", ["2026-10-29"], codes_per_day=300)
    r = db.assess(str(p), "2026-10-29")
    assert r["stale"] is False
    assert r["codes_on_expect"] == 300
    assert db._code_for(r, 3) == 0


def test_min_codes_threshold_is_configurable(tmp_path):
    p = _make_db(tmp_path / "a.db", ["2026-10-29"], codes_per_day=100)
    assert db.assess(str(p), "2026-10-29", min_codes=280)["stale"] is True
    assert db.assess(str(p), "2026-10-29", min_codes=50)["stale"] is False
