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


# ---------------------------------------------------------------- 买入信号榜的策略覆盖


def _stock(code: str, prices):
    """构造一只只含价格/成交量的 Stock（买信号只看这两个字段）。"""
    from domain_models import Stock

    return Stock(
        code=code, name=code, pe=10.0, pb=1.0, peg=1.0,
        revenue_growth=0.0, profit_growth=0.0, roe=0.0, cashflow=0.0,
        prices=list(prices), volumes=[1_000_000] * len(prices),
    )


def _rising(n: int = 70, start: float = 100.0, step: float = 0.1):
    """稳步上涨：触发「回调买入」（ma20>ma60 且贴近 ma20），RSI 为 100。"""
    return [round(start + step * i, 2) for i in range(n)]


def _falling(n: int = 70, start: float = 140.0, step: float = 0.5):
    """单边下跌：RSI(14) < 30，且 ma20<ma60（不会触发回调买入）。"""
    return [round(start - step * i, 2) for i in range(n)]


def test_buy_board_keeps_one_slot_per_fired_strategy():
    """回归（2026-11-12 用户报「RSI超卖 这个策略没有数据」）。

    排序键是 (信号个数, -策略优先级, 20日动量)：RSI超卖 优先级最低（4），
    且「超卖」本身意味着动量为负 ⇒ 候选 ≥ top 时被 100% 挤出买入信号榜。
    修复：每个作为主策略出现的策略至少保留 1 个名额。
    """
    callback = [_stock(f"60{i:04d}", _rising()) for i in range(25)]
    oversold = [_stock("300001", _falling())]

    picks = ph.build_buy_picks(callback + oversold, top=20)

    assert len(picks) == 20, picks
    strategies = [p["strategy"] for p in picks]
    assert "RSI超卖" in strategies, (
        "RSI超卖 有信号却被 top-20 挤没了 —— 每个触发的策略必须至少占 1 个名额"
    )
    # 覆盖不能破坏排名连续性（rank 仍是 1..20，被捞回来的按原排名落位）
    assert [p["rank"] for p in picks] == list(range(1, 21))


def test_buy_board_without_oversold_is_unchanged():
    """没有 RSI 候选时，覆盖逻辑不能改变原有 top-N 结果。"""
    stocks = [_stock(f"60{i:04d}", _rising()) for i in range(10)]
    picks = ph.build_buy_picks(stocks, top=20)
    assert len(picks) == 10
    assert {p["strategy"] for p in picks} == {"回调买入"}


def test_buy_board_coverage_respects_small_top():
    """top 比策略数还小时只取 top 条，不许越界。"""
    callback = [_stock(f"60{i:04d}", _rising()) for i in range(5)]
    oversold = [_stock("300001", _falling())]
    picks = ph.build_buy_picks(callback + oversold, top=1)
    assert len(picks) == 1
