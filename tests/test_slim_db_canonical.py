"""scripts/slim_db_for_cache.py 的 canonical 轮次收敛。

**为什么现在多了这一步（2026-11-05）**
-------------------------------------
原来 slim 只按「日期窗口」删 `trade_plan`（保留 60 天）。
但窗口**内**每个 `(日期, 档位)` 还有**多个轮次**（回补逐轮追加，每轮一个
`params_hash`）⇒ 一个交易日能到 5000 行，全在窗口里，删不掉。

实测：502.8 MB 的库，瘦身只删了窗口外的行，剩 6,332 行 —— 但其中大部分是
同一 `(日期,档位)` 的重复轮次。加上 canonical 收敛后：

    再删 185,938 行（非 canonical 轮次）
    503 MB → 31.9 MB

**规则必须与读路径一致**
---------------------
canonical 轮次的定义（行数最多、并列取 `MAX(plan_id)`）来自
`db_repository.canonical_plan_round`。如果 slim 用另一套规则，
就会「删掉读路径本来要选的那一轮」⇒ 前端数据悄悄变了。
所以规则统一放在 `shared_lib.plan_rows.CANONICAL_ROUNDS_SQL`，
由 `tests/test_clean_trade_plan_rounds.py` 交叉校验。
"""

import importlib.util
import sqlite3
from pathlib import Path

import pytest

from db_repository import canonical_plan_round, open_db
from shared_lib.plan_rows import CANONICAL_ROUNDS_SQL

_SPEC = importlib.util.spec_from_file_location(
    "slim_db",
    Path(__file__).resolve().parent.parent / "scripts" / "slim_db_for_cache.py",
)
slim_mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(slim_mod)


def _seed(conn, date, portfolio, code, phash, plan_id=None):
    conn.execute(
        "INSERT INTO trade_plan (plan_id, plan_date, portfolio, code, action, "
        "plan_price, size_pct, stop_price, tp_price, rr_ratio, status, reason, "
        "rationale_json, params_hash, created_at, shares) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (plan_id if plan_id is not None else conn.execute(
            "SELECT COALESCE(MAX(plan_id),0)+1 FROM trade_plan").fetchone()[0],
         date, portfolio, code, "buy", 10.0, 0.1, 9.5, 10.8, 1.6, "ok", "",
         "{}", phash, "2026-11-05T09:00:00", 100),
    )
    conn.commit()


class TestSlimKeepsCanonicalRounds:

    def test_non_canonical_rounds_removed(self, tmp_path):
        """窗口内的重复轮次也要删掉（原来只删窗口外）。"""
        src = str(tmp_path / "src.db")
        conn = open_db(src)
        try:
            # 同一 (日期,档位)：hash_a 5 行（canonical），hash_b 2 行
            for i in range(5):
                _seed(conn, "2026-11-05", "10W", "c%d" % i, "hash_a")
            for i in range(2):
                _seed(conn, "2026-11-05", "10W", "d%d" % i, "hash_b")
        finally:
            conn.close()

        dst = str(tmp_path / "slim.db")
        stats = slim_mod.slim(src, dst, 60)

        assert stats["deleted_non_canonical_rounds"] == 2
        c = sqlite3.connect(dst)
        try:
            left = {r[0] for r in c.execute("SELECT DISTINCT params_hash FROM trade_plan")}
            assert left == {"hash_a"}, "非 canonical 轮次没删干净"
            # canonical 那一轮的行必须一行不少
            n = c.execute("SELECT COUNT(*) FROM trade_plan").fetchone()[0]
            assert n == 5
        finally:
            c.close()

    def test_kept_round_matches_repository_canonical(self, tmp_path):
        """留存的必须是 db_repository.canonical_plan_round 选出的那一轮。"""
        src = str(tmp_path / "src.db")
        conn = open_db(src)
        try:
            for i in range(4):
                _seed(conn, "2026-11-05", "20W", "a%d" % i, "hash_a")
            for i in range(2):
                _seed(conn, "2026-11-05", "20W", "b%d" % i, "hash_b")
            expected = canonical_plan_round(conn, "2026-11-05", "20W")
        finally:
            conn.close()

        dst = str(tmp_path / "slim.db")
        slim_mod.slim(src, dst, 60)
        c = sqlite3.connect(dst)
        try:
            left = {r[0] for r in c.execute("SELECT DISTINCT params_hash FROM trade_plan")}
            assert left == {expected}
        finally:
            c.close()

    def test_shared_constant_is_the_same_rule(self):
        """瘦身用的 SQL 常量必须就是 shared_lib 里那一份（唯一真相源）。"""
        assert slim_mod.CANONICAL_ROUNDS_SQL == CANONICAL_ROUNDS_SQL

    def test_prices_untouched(self, tmp_path):
        """瘦身**不能碰**行情 —— pick_history 按每只票 221 根 K 线过滤。"""
        src = str(tmp_path / "src.db")
        conn = open_db(src)
        try:
            for i in range(3):
                _seed(conn, "2026-11-05", "10W", "c%d" % i, "hash_a")
            conn.execute(
                "INSERT INTO daily_prices (code, trade_date, close) VALUES (?,?,?)",
                ("000001", "2026-11-05", 10.0))
            conn.commit()
            before = conn.execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0]
        finally:
            conn.close()
        dst = str(tmp_path / "slim.db")
        slim_mod.slim(src, dst, 60)
        c = sqlite3.connect(dst)
        try:
            after = c.execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0]
            assert after == before
        finally:
            c.close()
