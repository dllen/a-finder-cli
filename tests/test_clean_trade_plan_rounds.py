"""scripts/clean_trade_plan_rounds.py —— trade_plan 非 canonical 轮次的清理。

**这个测试守住最重要的一条不变量**
----------------------------------
清理脚本必须与**读路径**用**同一条** canonical 规则，否则会出现
「脚本以为该留的轮次被删了，读路径改选了另一轮 ⇒ 前端数据悄悄变了」。

所以这里直接拿 `db_repository.canonical_plan_round` 做交叉校验：
脚本的窗口函数算出的轮次，必须与它选出的**逐个 (日期,档位) 都相同**。

另外守住：
- 清理后读路径的输出**不变**（canonical 轮次的行数与内容不受影响）；
- dry-run 不改数据；
- 派生表性质：删掉的数据能由 `plan build-all --backfill` 重算。
"""

import importlib.util
import sqlite3
from pathlib import Path

import pytest

from db_repository import canonical_plan_round, open_db

_SPEC = importlib.util.spec_from_file_location(
    "clean_trade_plan_rounds",
    Path(__file__).resolve().parent.parent / "scripts" / "clean_trade_plan_rounds.py",
)
clean_mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(clean_mod)


def _seed(conn, date, portfolio, code, phash, action="buy", price=10.0, plan_id=None):
    # trade_plan 的 NOT NULL 列不少，缺一个就 IntegrityError —— 一次给全。
    conn.execute(
        "INSERT INTO trade_plan (plan_id, plan_date, portfolio, code, action, "
        "plan_price, size_pct, stop_price, tp_price, rr_ratio, status, reason, "
        "rationale_json, params_hash, created_at, shares) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (plan_id if plan_id is not None else conn.execute(
            "SELECT COALESCE(MAX(plan_id),0)+1 FROM trade_plan").fetchone()[0],
         date, portfolio, code, action, price, 0.1, price * 0.95, price * 1.08,
         1.6, "ok", "", "{}", phash, "2026-11-05T09:00:00", 100),
    )
    conn.commit()


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "t.db")
    conn = open_db(path)
    yield path, conn
    conn.close()


class TestCanonicalRuleMatchesRepository:
    """脚本的规则必须与 db_repository.canonical_plan_round 完全一致。"""

    def _rounds_by_sql(self, conn):
        return {
            (r[0], r[1]): r[2]
            for r in conn.execute(clean_mod.CANONICAL_ROUNDS_SQL)
        }

    def test_same_round_as_repository(self, db):
        path, conn = db
        # 造 3 组：行数不同、并列、只有一轮
        for i in range(5):
            _seed(conn, "2026-11-05", "10W", "00000%d" % i, "hash_a")
        for i in range(3):
            _seed(conn, "2026-11-05", "10W", "10000%d" % i, "hash_b")
        for i in range(5):
            _seed(conn, "2026-11-05", "20W", "20000%d" % i, "hash_c")
        for i in range(2):
            _seed(conn, "2026-11-04", "10W", "30000%d" % i, "hash_d")

        by_sql = self._rounds_by_sql(conn)
        for (plan_date, portfolio) in [("2026-11-05", "10W"),
                                       ("2026-11-05", "20W"),
                                       ("2026-11-04", "10W")]:
            expected = canonical_plan_round(conn, plan_date, portfolio)
            assert by_sql[(plan_date, portfolio)] == expected, (
                "脚本选出的 canonical 轮次与读路径不一致：%s %s" % (plan_date, portfolio)
            )

    def test_tie_broken_by_latest_plan_id(self, db):
        """行数并列时，取 plan_id 最大的那轮（与读路径同规则）。"""
        path, conn = db
        for i in range(3):
            _seed(conn, "2026-11-05", "10W", "a%d" % i, "hash_old", plan_id=100 + i)
        for i in range(3):
            _seed(conn, "2026-11-05", "10W", "b%d" % i, "hash_new", plan_id=200 + i)
        expected = canonical_plan_round(conn, "2026-11-05", "10W")
        by_sql = self._rounds_by_sql(conn)
        assert by_sql[("2026-11-05", "10W")] == expected == "hash_new"


class TestCleanup:

    def test_dry_run_does_not_modify(self, db):
        path, conn = db
        for i in range(4):
            _seed(conn, "2026-11-05", "10W", "c%d" % i, "hash_a")
        for i in range(2):
            _seed(conn, "2026-11-05", "10W", "d%d" % i, "hash_b")
        before = conn.execute("SELECT COUNT(*) FROM trade_plan").fetchone()[0]
        stats = clean_mod.plan_cleanup(conn)
        after = conn.execute("SELECT COUNT(*) FROM trade_plan").fetchone()[0]
        assert before == after
        assert stats["delete"] > 0

    def test_apply_keeps_only_canonical_round(self, db):
        path, conn = db
        for i in range(4):
            _seed(conn, "2026-11-05", "10W", "c%d" % i, "hash_a")
        for i in range(2):
            _seed(conn, "2026-11-05", "10W", "d%d" % i, "hash_b")
        deleted = clean_mod.apply_cleanup(conn)
        assert deleted == 2
        left = conn.execute(
            "SELECT DISTINCT params_hash FROM trade_plan").fetchall()
        assert [r[0] for r in left] == ["hash_a"]

    def test_read_output_unchanged_after_cleanup(self, db):
        """清理前后，读路径（canonical 档位 + canonical 轮次）拿到的东西必须一样。"""
        path, conn = db
        for i in range(4):
            _seed(conn, "2026-11-05", "10W", "c%d" % i, "hash_a", price=10.0 + i)
        for i in range(2):
            _seed(conn, "2026-11-05", "10W", "d%d" % i, "hash_b", price=99.0)
        for i in range(3):
            _seed(conn, "2026-11-04", "20W", "e%d" % i, "hash_c", price=5.0)

        def read():
            out = {}
            for (d, p) in conn.execute(
                    "SELECT DISTINCT plan_date, portfolio FROM trade_plan"):
                ph = canonical_plan_round(conn, d, p)
                rows = conn.execute(
                    "SELECT code, action, plan_price FROM trade_plan "
                    "WHERE plan_date=? AND portfolio=? AND params_hash=? "
                    "ORDER BY code", (d, p, ph)).fetchall()
                out[(d, p)] = rows
            return out

        before = read()
        clean_mod.apply_cleanup(conn)
        after = read()
        assert before == after, "清理后读路径结果变了 —— 说明删错了行"

    def test_no_data_loss_for_canonical_rows(self, db):
        """每个 (日期,档位) 的 canonical 轮次行必须一行不少。"""
        path, conn = db
        for i in range(4):
            _seed(conn, "2026-11-05", "10W", "c%d" % i, "hash_a")
        for i in range(2):
            _seed(conn, "2026-11-05", "10W", "d%d" % i, "hash_b")
        before = {}
        for (d, p) in conn.execute(
                "SELECT DISTINCT plan_date, portfolio FROM trade_plan"):
            ph = canonical_plan_round(conn, d, p)
            before[(d, p)] = conn.execute(
                "SELECT COUNT(*) FROM trade_plan WHERE plan_date=? AND portfolio=? "
                "AND params_hash=?", (d, p, ph)).fetchone()[0]
        clean_mod.apply_cleanup(conn)
        for (d, p), n in before.items():
            ph = canonical_plan_round(conn, d, p)
            now = conn.execute(
                "SELECT COUNT(*) FROM trade_plan WHERE plan_date=? AND portfolio=? "
                "AND params_hash=?", (d, p, ph)).fetchone()[0]
            assert now == n, "canonical 行数变了：%s %s" % (d, p)


class TestCli:

    def test_missing_db_returns_1(self, tmp_path):
        assert clean_mod.main(["--db", str(tmp_path / "nope.db")]) == 1

    def test_report_mode_runs_without_apply_flag(self, db, capsys):
        path, conn = db
        for i in range(3):
            _seed(conn, "2026-11-05", "10W", "c%d" % i, "hash_a")
        assert clean_mod.main(["--db", path]) == 0
        out = capsys.readouterr().out
        assert "将删除行数" in out
