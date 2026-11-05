"""scripts/ 以「脚本方式」直接运行时的 import 回归测试。

**事故（2026-11-05，CI 实测）**
---------------------------
CI 用 `python3 scripts/slim_db_for_cache.py --db hs300.db` 调用脚本，
此时 `sys.path[0]` 是 **`scripts/`** ⇒ **仓库根不在导入路径上**
（CI 的 `actions/setup-python` 建的是普通 venv，没有把项目根塞进 path）。

我给 slim 加了 `from shared_lib.plan_rows import CANONICAL_ROUNDS_SQL`
（共享 canonical 规则）⇒ CI 直接 **ModuleNotFoundError** ⇒
瘦身步骤失败 ⇒ **仍存原库**（体积大得多）⇒
annotation 只写一句「瘦身失败 —— 仍存原库」，很容易当成无害告警。

**为什么本机 pytest 测不出来**
-------------------------
本机 `.venv` 是 **uv** 建的，**会把项目根加进 `sys.path`** ⇒
import 永远成功 ⇒ 全绿。只有**干净解释器**才复现。

⇒ 这个测试用**干净解释器**（找一个 `sys.path` 里没有仓库根的 python）
以 subprocess 跑脚本，才能真正覆盖。
"""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _clean_interpreter():
    """找一个 `sys.path` 里**没有仓库根**的 python（= CI 的情形）。

    uv 建的 venv 会把项目根加进 sys.path ⇒ 用它测**永远发现不了**这个 bug。
    """
    probe = ROOT / "scripts" / "_probe_syspath_tmp.py"
    probe.write_text(
        "import sys\nprint(%r in sys.path)\n" % str(ROOT),
        encoding="utf-8")
    cands = []
    for name in ("python3.11", "python3.12", "python3"):
        found = subprocess.run(["which", name], capture_output=True,
                              text=True).stdout.strip()
        if found and found not in cands:
            cands.append(found)
    cands.append(sys.executable)
    try:
        for cand in cands:
            if not cand or not os.path.exists(cand):
                continue
            env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
            r = subprocess.run([cand, str(probe)], capture_output=True,
                               text=True, cwd=str(ROOT), env=env)
            if r.returncode == 0 and r.stdout.strip() == "False":
                return cand
    finally:
        probe.unlink(missing_ok=True)
    return None


CLEAN_PY = _clean_interpreter()


def _make_db(path):
    """造一个能被 slim/clean 处理的最小库（要真的有 trade_plan 表）。"""
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE daily_prices (code TEXT, trade_date TEXT, close REAL);"
        "CREATE TABLE daily_picks (date TEXT, code TEXT);"
        "CREATE TABLE trade_plan (plan_id INTEGER PRIMARY KEY, plan_date TEXT,"
        " portfolio TEXT, code TEXT, action TEXT, plan_price REAL,"
        " size_pct REAL, stop_price REAL, tp_price REAL, rr_ratio REAL,"
        " status TEXT, reason TEXT, rationale_json TEXT, params_hash TEXT,"
        " created_at TEXT, shares INTEGER);"
    )
    conn.execute(
        "INSERT INTO trade_plan (plan_id, plan_date, portfolio, code, action,"
        " plan_price, size_pct, stop_price, tp_price, rr_ratio, status, reason,"
        " rationale_json, params_hash, created_at, shares)"
        " VALUES (1, ?, ?, ?, ?, 10.0, 0.1, 9.5, 10.8, 1.6, ?, ?, ?, ?, ?, ?)",
        ("2026-11-05", "10W", "000001", "buy", "ok", "", "{}", "h1",
         "2026-11-05T09:00:00", 100),
    )
    conn.commit()
    conn.close()


def _run_script(script, *args):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    return subprocess.run(
        [CLEAN_PY, str(ROOT / "scripts" / script)] + list(args),
        capture_output=True, text=True, cwd=str(ROOT), env=env)


class TestScriptsRunAsScripts:

    def test_clean_interpreter_available(self):
        """环境里必须能找到一台「干净」解释器，否则下面的测试是假的。"""
        assert CLEAN_PY, (
            "找不到 sys.path 里没有仓库根的解释器 —— 测不出 CI 的那个 bug")

    def test_slim_db_runs_as_a_script(self, tmp_path):
        """`python3 scripts/slim_db_for_cache.py --db X` 必须能跑通（CI 就是这么调的）。"""
        db = tmp_path / "t.db"
        _make_db(str(db))
        r = _run_script("slim_db_for_cache.py", "--db", str(db))
        assert "ModuleNotFoundError" not in (r.stdout + r.stderr), (
            "脚本方式运行炸了 —— CI 就是这么调的:\n" + r.stdout + r.stderr)
        assert r.returncode == 0, r.stdout + r.stderr

    def test_clean_trade_plan_runs_as_a_script(self, tmp_path):
        """`python3 scripts/clean_trade_plan_rounds.py --db X` 必须能跑通。"""
        db = tmp_path / "t.db"
        _make_db(str(db))
        r = _run_script("clean_trade_plan_rounds.py", "--db", str(db))
        assert "ModuleNotFoundError" not in (r.stdout + r.stderr), (
            "脚本方式运行炸了 —— CI 就是这么调的:\n" + r.stdout + r.stderr)
        assert r.returncode == 0, r.stdout + r.stderr
