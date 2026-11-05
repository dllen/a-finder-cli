"""slim_db_for_cache 的「边界形状」回归测试。

**这些都是 CI 真实遇到过的形状**（2026-11-05）：

1. **`trade_plan` 是空表** —— CI restore 回来的谱系库里 trade_plan 已被瘦身成 0 行。
   我给 slim 加 canonical 收敛时用了 `deleted_rounds` 变量，却只在 `if latest:`
   分支里赋值 ⇒ 库里没有 trade_plan 行时 **UnboundLocalError** ⇒
   「瘦身失败 —— 仍存原库」。

2. **`trade_plan` 表没有 params_hash / plan_id 列** —— 精简的老库。
   canonical 规则需要这两列 ⇒ 缺列时要**跳过**而不是崩。

3. **脚本方式运行时的 import 路径** —— 见
   `tests/test_scripts_run_as_scripts.py`。

**为什么这些测试必须有**
----------------------
瘦身失败在 CI 里**只是一句 annotation**（「瘦身失败 —— 仍存原库」），
run 依然显示 success —— 极容易被当成无害告警，实际是**谱系一直不瘦身**。
"""

import importlib.util
import sqlite3
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "slim_db",
    Path(__file__).resolve().parent.parent / "scripts" / "slim_db_for_cache.py",
)
slim_mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(slim_mod)


def _bare_db(path, with_params_hash=True):
    """造一个「精简库」：daily_prices 有数据，trade_plan 形状可定制。"""
    conn = sqlite3.connect(path)
    if with_params_hash:
        conn.executescript(
            "CREATE TABLE daily_prices (code TEXT, trade_date TEXT, close REAL);"
            "CREATE TABLE daily_picks (date TEXT, code TEXT);"
            "CREATE TABLE trade_plan (plan_id INTEGER PRIMARY KEY, plan_date TEXT,"
            " portfolio TEXT, code TEXT, action TEXT, params_hash TEXT);"
        )
    else:
        conn.executescript(
            "CREATE TABLE daily_prices (code TEXT, trade_date TEXT, close REAL);"
            "CREATE TABLE daily_picks (date TEXT, code TEXT);"
            "CREATE TABLE trade_plan (plan_date TEXT, portfolio TEXT, code TEXT);"
        )
    for code in ("000001", "600519"):
        for d in ("2026-11-03", "2026-11-04", "2026-11-05"):
            conn.execute("INSERT INTO daily_prices VALUES (?,?,?)", (code, d, 10.0))
    conn.commit()
    conn.close()


class TestSlimEdgeShapes:

    def test_empty_trade_plan_does_not_crash(self, tmp_path):
        """**CI 的真实形状**：谱系库里的 trade_plan 是空的。

        之前 `deleted_rounds` 只在 `if latest:` 分支里赋值 ⇒
        空表时 UnboundLocalError ⇒ 瘦身失败（CI 只留一句 annotation）。
        """
        src = str(tmp_path / "src.db")
        _bare_db(src)
        dst = str(tmp_path / "slim.db")
        stats = slim_mod.slim(src, dst, 60)
        assert stats["deleted_trade_plan_rows"] == 0
        assert stats["deleted_non_canonical_rounds"] == 0
        c = sqlite3.connect(dst)
        try:
            assert c.execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0] == 6
        finally:
            c.close()

    def test_missing_params_hash_column_does_not_crash(self, tmp_path):
        """老库没有 params_hash / plan_id 列 ⇒ 跳过 canonical 收敛，而不是崩。"""
        src = str(tmp_path / "src.db")
        _bare_db(src, with_params_hash=False)
        dst = str(tmp_path / "slim.db")
        stats = slim_mod.slim(src, dst, 60)
        assert stats["deleted_non_canonical_rounds"] == 0

    def test_empty_trade_plan_keeps_prices(self, tmp_path):
        """空 trade_plan 时行情必须一行不少。"""
        src = str(tmp_path / "src.db")
        _bare_db(src)
        dst = str(tmp_path / "slim.db")
        slim_mod.slim(src, dst, 60)
        c = sqlite3.connect(dst)
        try:
            dates = [r[0] for r in c.execute(
                "SELECT DISTINCT trade_date FROM daily_prices ORDER BY trade_date")]
            assert dates == ["2026-11-03", "2026-11-04", "2026-11-05"]
        finally:
            c.close()
