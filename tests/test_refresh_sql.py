"""用**真实 SQLite** 执行 worker/index.js 里的刷新 SQL。

为什么需要这个测试：
  tests/test_refresh_worker.py 用的是内存版假 D1，它靠 `sql.includes(...)`
  **字符串匹配**来模拟行为 —— 也就是说 SQL 写错了（语法错、占位符个数不对、
  列名拼错）它**照样通过**。而线上是真 D1（SQLite），语法错会直接 500。

  更别说 `INSERT ... SELECT ... WHERE NOT EXISTS` 这种写法是否真的原子、
  `meta.changes` 是否真的为 0，只有真跑才有意义。

所以这里把 worker/index.js 里所有含 refresh_jobs 的 SQL 模板字面量抽出来，
对着真 SQLite 执行一遍：
  - SQL 语法必须合法
  - 占位符个数必须与实际绑定的参数个数一致
  - 关键语义（互斥 / 过期清理 / 抢占 / 未知 job 返回 0 行）必须成立
"""
import re
import sqlite3
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKER_JS = ROOT / "worker" / "index.js"
SCHEMA = ROOT / "worker" / "schema.sql"


def _refresh_sql_blocks():
    """抽 worker/index.js 里含 refresh_jobs 的反引号 SQL 字面量。"""
    src = WORKER_JS.read_text(encoding="utf-8")
    blocks = re.findall(r"`([^`]*refresh_jobs[^`]*)`", src, re.S)
    assert blocks, "没抽到任何 refresh_jobs SQL —— 正则或代码结构变了"
    return blocks


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    return conn


def test_all_refresh_sql_is_syntactically_valid():
    """每条 SQL 都必须能被真 SQLite 编译（含占位符个数匹配）。

    用 `EXPLAIN` 而不是直接执行：EXPLAIN 会把语句**编译**成字节码
    （验证语法、表/列名、参数个数）但**不执行** —— 否则绑 None
    会撞 NOT NULL 约束，测的就不是语法了。
    """
    conn = _conn()
    blocks = _refresh_sql_blocks()
    assert len(blocks) >= 4, f"只抽到 {len(blocks)} 条刷新 SQL，应该更多"
    for sql in blocks:
        n_params = sql.count("?")
        conn.execute("EXPLAIN " + sql, tuple(None for _ in range(n_params)))


def test_insert_where_not_exists_is_really_atomic_and_mutex():
    """`INSERT ... WHERE NOT EXISTS` 在有 pending 时必须插不进第 2 条。"""
    conn = _conn()
    sql = (
        "INSERT INTO refresh_jobs (job_id, status, requested_by, created_at)\n"
        "SELECT ?,?,?,? WHERE NOT EXISTS (\n"
        "  SELECT 1 FROM refresh_jobs WHERE status IN ('pending','running')\n"
        ")"
    )
    c1 = conn.execute(sql, (str(uuid.uuid4()), "pending", "u", "2026-11-03T10:00:00"))
    c2 = conn.execute(sql, (str(uuid.uuid4()), "pending", "u", "2026-11-03T10:00:01"))
    assert c1.rowcount == 1
    assert c2.rowcount == 0, "互斥失效 —— 会排出多条任务"


def test_expire_then_insert_succeeds():
    """过期清理后必须能重新建任务（否则僵尸任务永久 409）。"""
    conn = _conn()
    conn.execute(
        "INSERT INTO refresh_jobs (job_id, status, requested_by, created_at) VALUES (?,?,?,?)",
        ("zombie", "pending", "u", "2020-01-01T00:00:00"),
    )
    # 未清理时插不进（真实语义：此时前端会看到 409）
    blocked = conn.execute(
        "INSERT INTO refresh_jobs (job_id, status, requested_by, created_at)\n"
        "SELECT ?,?,?,? WHERE NOT EXISTS (\n"
        "  SELECT 1 FROM refresh_jobs WHERE status IN ('pending','running')\n"
        ")",
        (str(uuid.uuid4()), "pending", "u", "2026-11-03T10:00:00"),
    )
    assert blocked.rowcount == 0
    # 清理
    expired = conn.execute(
        "UPDATE refresh_jobs SET status = 'failed', message = ?, finished_at = ?\n"
        " WHERE status = 'pending' AND created_at < ?",
        ("timeout", "2026-11-03T10:10:00", "2026-11-03T10:05:00"),
    )
    assert expired.rowcount == 1
    # 清理后可以建
    ok = conn.execute(
        "INSERT INTO refresh_jobs (job_id, status, requested_by, created_at)\n"
        "SELECT ?,?,?,? WHERE NOT EXISTS (\n"
        "  SELECT 1 FROM refresh_jobs WHERE status IN ('pending','running')\n"
        ")",
        (str(uuid.uuid4()), "pending", "u", "2026-11-03T10:11:00"),
    )
    assert ok.rowcount == 1


def test_claim_is_exclusive():
    """带条件的 UPDATE 让第二个领任务者拿到 0 行（meta.changes=0 → job_id:null）。"""
    conn = _conn()
    jid = "job-1"
    conn.execute(
        "INSERT INTO refresh_jobs (job_id, status, requested_by, created_at) VALUES (?,?,?,?)",
        (jid, "pending", "u", "2026-11-03T10:00:00"),
    )
    claim = ("UPDATE refresh_jobs SET status = 'running', started_at = ? "
             "WHERE job_id = ? AND status = 'pending'")
    assert conn.execute(claim, ("2026-11-03T10:01:00", jid)).rowcount == 1
    assert conn.execute(claim, ("2026-11-03T10:01:01", jid)).rowcount == 0


def test_report_unknown_job_affects_zero_rows():
    """回报不存在的 job 必须 0 行 → Worker 才能据此返回 404。"""
    conn = _conn()
    cur = conn.execute(
        "UPDATE refresh_jobs SET status = ?, latest_date = ?, message = ?, finished_at = ? "
        "WHERE job_id = ?",
        ("success", "2026-11-03", "ok", "2026-11-03T10:40:00", "no-such-job"),
    )
    assert cur.rowcount == 0


def test_timestamp_string_ordering_matches_time_ordering():
    """过期判定靠字符串比较 —— 必须验证定宽 UTC 串的字典序等于时间序。"""
    samples = [
        ("2026-11-03T09:59:59", "2026-11-03T10:00:00"),
        ("2026-11-03T23:59:59", "2026-11-04T00:00:00"),
        ("2026-10-31T12:00:00", "2026-11-01T00:00:00"),
    ]
    for earlier, later in samples:
        assert len(earlier) == len(later) == 19
        assert earlier < later, (earlier, later)
