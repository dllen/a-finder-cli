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
