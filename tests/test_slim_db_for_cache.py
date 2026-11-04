"""scripts/slim_db_for_cache.py 的行为测试。

**背景（2026-11-04 查明）**
--------------------------
CI 的行情谱系靠 actions/cache 的 hs300.db 传递，配额 10 GB。
但 trade_plan（含索引）占了全库 94%（实测 469 MB / 499 MB），
一天 8 班 × 全量库必然打爆配额 ⇒ **actions/cache 存不下但不报错**
（save 步骤报 success、post 步骤 skipped）⇒ 谱系停在最后一次成功保存的那天
（实测停在 2026-10-29，之后每一班都是坏的）。

slim_db_for_cache.py 在存缓存前删掉窗口外的 trade_plan 行再 VACUUM。
trade_plan 是**派生表**（daily_picks + daily_prices 可重算），所以删它是安全的；
真正必须留在缓存里的是**行情**（pick_history 按每只票 221 根 K 线过滤）。

这些测试锁住：
1. 只删 trade_plan，**绝不碰 daily_prices / daily_picks**。
2. 窗口外的行确实被删、窗口内的行保留。
3. 原地模式下文件名不变（save/restore 的 path 必须一致，否则每班冷启动）。
4. 原地模式不留备份文件（否则下次又被算进缓存体积）。
"""

import sqlite3

import pytest

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.slim_db_for_cache import (  # noqa: E402
    DEFAULT_KEEP_DAYS,
    MIN_KEEP_DAYS,
    slim,
)


def _make_db(path, plan_dates, codes=("000001", "600519"), picks_dates=("2026-11-04",)):
    """造一个最小库：daily_prices / daily_picks / trade_plan。"""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE daily_prices (
            code TEXT, trade_date TEXT, close REAL, volume REAL
        );
        CREATE TABLE daily_picks (
            date TEXT, code TEXT
        );
        CREATE TABLE trade_plan (
            plan_date TEXT, portfolio TEXT, code TEXT, action TEXT
        );
        """
    )
    for code in codes:
        # 每只票 5 根 K 线，分布在几个日期
        for d in ("2026-10-01", "2026-10-02", "2026-10-03", "2026-11-03", "2026-11-04"):
            conn.execute(
                "INSERT INTO daily_prices VALUES (?,?,?,?)", (code, d, 10.0, 1000.0)
            )
    for d in picks_dates:
        conn.execute("INSERT INTO daily_picks VALUES (?,?)", (d, codes[0]))
    for d in plan_dates:
        for pf in ("10W", "20W"):
            conn.execute(
                "INSERT INTO trade_plan VALUES (?,?,?,?)", (d, pf, codes[0], "buy")
            )
    conn.commit()
    conn.close()
    return path


class TestSlimKeepsPrices:

    def test_daily_prices_untouched(self, tmp_path):
        """行情是缓存里唯一真正必须留的东西 —— 行数必须一行不少。"""
        db = _make_db(str(tmp_path / "a.db"), ["2026-11-04", "2026-08-01"])
        before = sqlite3.connect(db).execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0]
        slim(db, db, DEFAULT_KEEP_DAYS)
        after = sqlite3.connect(db).execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0]
        assert after == before

    def test_daily_picks_untouched(self, tmp_path):
        db = _make_db(str(tmp_path / "b.db"), ["2026-11-04", "2026-08-01"])
        before = sqlite3.connect(db).execute("SELECT COUNT(*) FROM daily_picks").fetchone()[0]
        slim(db, db, DEFAULT_KEEP_DAYS)
        after = sqlite3.connect(db).execute("SELECT COUNT(*) FROM daily_picks").fetchone()[0]
        assert after == before

    def test_max_trade_date_preserved(self, tmp_path):
        """最新行情日必须还是最新 —— db_freshness 全靠它判新鲜度。"""
        db = _make_db(str(tmp_path / "c.db"), ["2026-11-04"])
        st = slim(db, db, DEFAULT_KEEP_DAYS)
        assert st["max_trade_date"] == "2026-11-04"

    def test_min_bars_per_code_preserved(self, tmp_path):
        """历史深度是 pick_history 的硬门槛（每只票 >= 221 根）—— 绝不能变短。"""
        db = _make_db(str(tmp_path / "d.db"), ["2026-11-04"])
        before = sqlite3.connect(db).execute(
            "SELECT MIN(n) FROM (SELECT COUNT(*) n FROM daily_prices GROUP BY code)"
        ).fetchone()[0]
        st = slim(db, db, DEFAULT_KEEP_DAYS)
        assert st["min_bars_per_code"] == before


class TestSlimTrimsTradePlan:

    def test_old_rows_deleted_recent_kept(self, tmp_path):
        """窗口外的 plan_date 被删，窗口内的保留。"""
        db = _make_db(
            str(tmp_path / "e.db"),
            ["2026-11-04", "2026-10-30", "2026-01-05", "2025-06-01"],
        )
        st = slim(db, db, DEFAULT_KEEP_DAYS)
        rows = sqlite3.connect(db).execute(
            "SELECT DISTINCT plan_date FROM trade_plan ORDER BY plan_date"
        ).fetchall()
        dates = [r[0] for r in rows]
        assert "2025-06-01" not in dates          # 远早于窗口
        assert "2026-01-05" not in dates          # 早于窗口
        assert "2026-10-30" in dates              # 窗口内
        assert "2026-11-04" in dates              # 最新日必须在
        assert st["deleted_trade_plan_rows"] > 0

    def test_cutoff_is_relative_to_latest_plan_date(self, tmp_path):
        """窗口以「最新的 plan_date」为基准，不是以系统当天为基准。

        为什么：CI 可能因为数据源延迟而落后几天，「当天」删掉窗口内数据
        会让审计误报计划缺口。
        """
        db = _make_db(str(tmp_path / "f.db"), ["2026-03-10", "2026-03-09"])
        st = slim(db, db, MIN_KEEP_DAYS)
        # 最新 2026-03-10 - 45 天 = 2026-01-24，两个日期都在窗口内 ⇒ 一条都不删
        assert st["deleted_trade_plan_rows"] == 0
        assert st["latest_plan_date"] == "2026-03-10"

    def test_keep_days_below_minimum_rejected(self, tmp_path):
        """窗口小于审计窗口（30 天）+ 余量时必须报错 —— 否则 L3 会误报缺口。"""
        db = _make_db(str(tmp_path / "g.db"), ["2026-11-04"])
        with pytest.raises(ValueError):
            slim(db, db, 10)

    def test_keep_days_at_minimum_works(self, tmp_path):
        db = _make_db(str(tmp_path / "h.db"), ["2026-11-04", "2026-08-01"])
        slim(db, db, MIN_KEEP_DAYS)  # 不应抛异常
        assert sqlite3.connect(db).execute(
            "SELECT COUNT(*) FROM trade_plan WHERE plan_date='2026-11-04'"
        ).fetchone()[0] > 0


class TestSlimInPlace:

    def test_in_place_keeps_filename(self, tmp_path):
        """原地瘦身后文件名不变。

        这是 save/restore 的硬要求：actions/cache 按归档里的路径还原，
        存 hs300.db.slim 而 restore 写 path: hs300.db ⇒ 还原出 hs300.db.slim
        ⇒ 下游找不到 hs300.db ⇒ 每班冷启动。
        """
        db = _make_db(str(tmp_path / "i.db"), ["2026-11-04", "2026-01-01"])
        slim(db, db, DEFAULT_KEEP_DAYS)
        assert Path(db).exists()
        assert not (tmp_path / "i.db.slim").exists()

    def test_in_place_leaves_no_backup(self, tmp_path):
        """不留 .slim.bak —— 否则它会一直躺在工作区（还可能被算进缓存）。"""
        db = _make_db(str(tmp_path / "j.db"), ["2026-11-04", "2026-01-01"])
        slim(db, db, DEFAULT_KEEP_DAYS)
        leftovers = [p.name for p in tmp_path.iterdir() if p.name != "j.db"]
        assert leftovers == []

    def test_in_place_db_still_openable(self, tmp_path):
        """VACUUM 之后库必须仍然可读（不能留下损坏的中间态）。"""
        db = _make_db(str(tmp_path / "k.db"), ["2026-11-04", "2026-01-01"])
        slim(db, db, DEFAULT_KEEP_DAYS)
        conn = sqlite3.connect(db)
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        conn.close()
