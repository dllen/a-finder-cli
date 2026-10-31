import sys

import pytest

import pick_history as ph
from db_repository import open_db


def _champion_db(path):
    conn = open_db(path)
    conn.execute(
        "INSERT INTO strategy_config (created_at, active_json, ratios_json, status) "
        "VALUES ('now', '[\"布林超卖反弹\"]', '{}', 'champion')"
    )
    conn.execute(
        "INSERT INTO daily_prices (code, trade_date, close) VALUES ('600519', '2026-08-27', 1500.0)"
    )
    conn.commit()
    conn.close()


def test_run_picks_with_champion_does_not_crash(tmp_path, monkeypatch):
    """回归：存在 champion 配置时，run_picks 必须仍构建榜单并落库（曾 UnboundLocalError + 未提交）。"""
    db = str(tmp_path / "t.db")
    _champion_db(db)

    ma_pick = [{
        "rank": 1, "code": "600519", "name": "贵州茅台", "strategy": "布林超卖反弹",
        "buy": 1500.0, "stop": 1450.0, "target": 1600.0, "score": 9.5,
    }]
    monkeypatch.setattr(ph, "build_market_from_db", lambda *a, **k: [object()])
    monkeypatch.setattr(ph, "build_ma_picks", lambda *a, **k: ma_pick)
    monkeypatch.setattr(ph, "build_buy_picks", lambda *a, **k: [])
    monkeypatch.setattr(ph, "_detect_market_regime", lambda *a, **k: ph.RegimeType.SIDEWAYS)
    monkeypatch.setattr(ph, "build_signal_strategy_picks", lambda *a, **k: [])
    monkeypatch.setattr(ph, "build_multi_factor_picks", lambda *a, **k: [])
    monkeypatch.setattr(ph, "build_top_winrate_picks", lambda *a, **k: [])

    result = ph.run_picks(db, top=10, do_sync=False)

    assert result["date"] == "2026-08-27"
    assert result["ma"] == 1 and result["buy"] == 0
    # 落库持久化（曾因 executemany 未 commit 而丢失）
    conn = open_db(db)
    try:
        row = conn.execute(
            "SELECT code, strategy FROM daily_picks WHERE date='2026-08-27' AND kind='均线'"
        ).fetchone()
    finally:
        conn.close()
    assert row == ("600519", "布林超卖反弹")


# ---------------------------------------------------------------- 退出码


def test_main_exits_nonzero_when_nothing_produced(tmp_path, monkeypatch):
    """一条榜单都没产出 ⇒ **非零退出**（2026-10-31 修）。

    为什么必须非零：修复脚本（repair_gaps）把「命令退出码 == 0」当作
    「这一步成功了」。而「无可用行情数据」（历史不足 / 指定日期没行情）
    会走 early-return：**什么都不写、退出码却是 0** ——
    修复脚本据此报告「L2 已修复」，复检却还是报 L2 缺，
    「一轮收敛」要跑第二轮才成立，而中间那一轮看起来是绿的。

    这类「什么都没做但报告成功」是最难排查的一类失败：日志里全是 ✓。
    """
    db = str(tmp_path / "empty.db")
    open_db(db).close()
    monkeypatch.setattr(ph, "run_picks",
                        lambda *a, **k: {"date": "", "ma": 0, "buy": 0,
                                         "signal": 0, "multi": 0})
    monkeypatch.setattr(sys, "argv",
                        ["pick_history.py", "--db", db, "--no-sync",
                         "--date", "2026-10-30"])
    with pytest.raises(SystemExit) as exc:
        ph.main()
    assert exc.value.code == 1


def test_main_exits_zero_when_something_produced(tmp_path, monkeypatch):
    """正常产出时退出码必须是 0（别把成功也报成失败）。"""
    db = str(tmp_path / "ok.db")
    open_db(db).close()
    monkeypatch.setattr(ph, "run_picks",
                        lambda *a, **k: {"date": "2026-10-30", "ma": 10, "buy": 20,
                                         "signal": 20, "multi": 20})
    monkeypatch.setattr(sys, "argv",
                        ["pick_history.py", "--db", db, "--no-sync",
                         "--date", "2026-10-30"])
    ph.main()
