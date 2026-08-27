import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from db_repository import fetch_pick_outcomes, load_champion_config, open_db
from evolution import champion, labeling, service


def _row(date, strategy, code, score, win, pct):
    return dict(date=date, source="replay", code=code, strategy=strategy, name=strategy,
                kind="", score=score, buy=10.0, stop=9.0, target=12.0,
                exit_date=date, exit_price=12.0 if win else 9.0,
                outcome_pct=pct, win=win, labeled_at="t")


def _fake_rows(days=200):
    """B 高分全负、A 中分全胜、C 低分全胜 → 冠军榜单 2/3 胜率，挑战者满胜。"""
    rows = []
    for d in range(1, days + 1):
        date = f"d{d:03d}"
        rows += [
            _row(date, "B", f"B1-{d}", 0.95, 0, -0.10),
            _row(date, "B", f"B2-{d}", 0.90, 0, -0.10),
            _row(date, "A", f"A1-{d}", 0.50, 1, 0.20),
            _row(date, "A", f"A2-{d}", 0.45, 1, 0.20),
            _row(date, "C", f"C1-{d}", 0.30, 1, 0.05),
        ]
    return rows


@pytest.fixture
def patched(monkeypatch, tmp_path):
    db = str(tmp_path / "evo.db")
    monkeypatch.setattr(labeling, "replay_rows", lambda *a, **k: _fake_rows())
    monkeypatch.setattr(labeling, "live_rows", lambda *a, **k: [])
    monkeypatch.setattr(service, "STRATEGIES", {"A": None, "B": None, "C": None})
    monkeypatch.setattr(champion, "bootstrap_from_report",
                        lambda: {"active": ["A", "B", "C"],
                                 "ratios": {"A": 1 / 3, "B": 1 / 3, "C": 1 / 3}})
    return db


def test_evolve_promotes_challenger_and_persists(patched):
    report = service.run_evolve(patched, top=3)
    assert report["champion"] == 1  # bootstrap 版本
    assert report["decision"] == "promoted"
    assert set(report["proposal"]["active"]) == {"A", "C"}
    assert report["champion_metrics"]["n"] >= 100
    conn = open_db(patched)
    assert len(fetch_pick_outcomes(conn, source="replay", judged_only=True)) == 1000
    champ = load_champion_config(conn)
    assert champ["version"] == 2 and champ["active"] == ["A", "C"]
    assert champ["metrics"]["win_rate"] == 1.0
    conn.close()


def test_evolve_dry_run_writes_nothing(patched):
    report = service.run_evolve(patched, top=3, dry_run=True)
    assert report["decision"] == "promoted"
    conn = open_db(patched)
    assert conn.execute("SELECT COUNT(*) FROM pick_outcomes").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM strategy_config").fetchone()[0] == 0
    conn.close()


def test_second_run_is_incremental_noop(patched):
    service.run_evolve(patched, top=3)
    conn = open_db(patched)
    before = conn.execute("SELECT COUNT(*) FROM pick_outcomes").fetchone()[0]
    conn.close()
    report2 = service.run_evolve(patched, top=3)  # 水位线复用 + 幂等覆盖
    assert report2["decision"] == "NoChange"
    conn = open_db(patched)
    assert conn.execute("SELECT COUNT(*) FROM pick_outcomes").fetchone()[0] == before
    assert conn.execute("SELECT COUNT(*) FROM strategy_config").fetchone()[0] == 2
    conn.close()


def test_rollback_restores_previous_champion(patched):
    service.run_evolve(patched, top=3)
    conn = open_db(patched)
    champ = load_champion_config(conn)
    assert champ["version"] == 2
    # 模拟真实成交恶化：插入 live 行后手动走回滚判定
    bad = [dict(_row("z2026-09-01", "A", f"r{i}", 0.5, 0, -0.1), source="live") for i in range(25)]
    live_m = champion.live_window_stats(bad, champ["created_at"])
    assert champion.should_rollback(champ, live_m)
    from db_repository import mark_config_status
    with conn:
        mark_config_status(conn, champ["version"], "rolled_back", "test")
    restored = load_champion_config(conn)
    assert restored["version"] == 1 and restored["active"] == ["A", "B", "C"]
    conn.close()
