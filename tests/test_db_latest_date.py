"""scripts/db_latest_date.py：CI 取「库里行情最新日」的 robust 版。

**为什么需要这个脚本（2026-11-05 实测事故）**
--------------------------------------------------
原来的写法是一行裸 sqlite 查询：

    DATA_DATE=$(python3 -c "import sqlite3;print(sqlite3.connect('hs300.db')
      .execute('SELECT MAX(trade_date) FROM daily_prices').fetchone()[0])")

冷启动时库里**连 daily_prices 表都没有**（同步 0 行 ⇒ 从没建过表），
于是这行抛 OperationalError 并**退出 1**；它被赋值给 DATA_DATE，
而 CI 那一步是 `set -e` ⇒ **整步瞬间失败（实测 0.00 min）**。
更糟的是真正的错误（同步没抓到数据）在三步之前就被 `| tail -40 || true`
吞掉了 ⇒ run 变红，但没人知道红在哪。

本脚本把状态**分开**：

    rc=0  有最新日（打印日期）
    rc=3  库里没有行情（库不存在 / 没有表 / 表为空）
    rc=1  真出错（sqlite 异常）

这样 CI 能明确区分「库是空的」与「脚本坏了」，而不是一律一个 failure。
"""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import db_latest_date  # noqa: E402


def _db(path, with_rows=True):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE daily_prices (code TEXT, trade_date TEXT)")
    if with_rows:
        conn.execute("INSERT INTO daily_prices VALUES ('000001', '2026-11-04')")
        conn.execute("INSERT INTO daily_prices VALUES ('000002', '2026-11-03')")
    conn.commit()
    conn.close()
    return str(path)


class TestLatestTradeDate:

    def test_missing_db_is_no_data_not_error(self, tmp_path):
        """库不存在 ⇒ rc=3（明确「没有行情」），不是 rc=1（脚本坏了）。"""
        date, rc = db_latest_date.latest_trade_date(str(tmp_path / "nope.db"))
        assert rc == 3
        assert date == ""

    def test_missing_table_is_no_data(self, tmp_path):
        """连表都没建（同步 0 行）⇒ rc=3，**不能**抛异常。

        这是 2026-11-05 事故的直接触发点：旧写法在这里退出 1，
        而 CI 的那一步是 set -e ⇒ 整步瞬间失败、红在错误的地方。
        """
        path = str(tmp_path / "empty.db")
        sqlite3.connect(path).close()
        date, rc = db_latest_date.latest_trade_date(path)
        assert rc == 3
        assert date == ""

    def test_empty_table_is_no_data(self, tmp_path):
        path = _db(tmp_path / "t.db", with_rows=False)
        date, rc = db_latest_date.latest_trade_date(path)
        assert rc == 3
        assert date == ""

    def test_returns_max_date(self, tmp_path):
        path = _db(tmp_path / "ok.db")
        date, rc = db_latest_date.latest_trade_date(path)
        assert rc == 0
        assert date == "2026-11-04"


class TestCli:

    def test_exit_code_is_propagated(self, tmp_path, capsys):
        rc = db_latest_date.main(["--db", str(tmp_path / "nope.db")])
        assert rc == 3

    def test_prints_date_on_success(self, tmp_path, capsys):
        path = _db(tmp_path / "ok.db")
        rc = db_latest_date.main(["--db", path])
        assert rc == 0
        assert "2026-11-04" in capsys.readouterr().out

    def test_github_output_written(self, tmp_path, monkeypatch, capsys):
        out = tmp_path / "gh_out"
        monkeypatch.setenv("GITHUB_OUTPUT", str(out))
        path = _db(tmp_path / "ok.db")
        db_latest_date.main(["--db", path, "--github-output"])
        text = out.read_text(encoding="utf-8")
        assert "data_date=2026-11-04" in text
        assert "data_date_rc=0" in text
