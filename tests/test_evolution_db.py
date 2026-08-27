import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db_repository import open_db


def _fresh_db(tmp_path):
    return open_db(str(tmp_path / "t.db"))


def test_tables_created(tmp_path):
    conn = _fresh_db(tmp_path)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"pick_outcomes", "strategy_config"} <= names


def test_outcome_roundtrip_and_watermark(tmp_path):
    from db_repository import upsert_pick_outcomes, fetch_pick_outcomes, outcomes_watermark
    conn = _fresh_db(tmp_path)
    base = dict(date="2026-08-01", source="replay", code="000001", strategy="A", name="X",
                kind="", score=1.0, buy=10.0, stop=9.0, target=12.0,
                exit_date=None, exit_price=None, outcome_pct=None, win=None,
                labeled_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
    assert outcomes_watermark(conn, "replay") == ""
    upsert_pick_outcomes(conn, [dict(base)])
    conn.commit()
    assert outcomes_watermark(conn, "replay") == ""  # 未判定不推进水位线
    judged = {**base, "date": "2026-08-02", "exit_date": "2026-08-12",
              "exit_price": 12.0, "outcome_pct": 0.2, "win": 1}
    upsert_pick_outcomes(conn, [judged])
    conn.commit()
    assert outcomes_watermark(conn, "replay") == "2026-08-02"
    # 同键覆盖（INSERT OR REPLACE）
    judged["win"] = 0
    judged["outcome_pct"] = -0.05
    upsert_pick_outcomes(conn, [judged])
    conn.commit()
    rows = fetch_pick_outcomes(conn, source="replay", judged_only=True)
    assert len(rows) == 1  # 2026-08-01 未判定被过滤
    assert rows[0]["win"] == 0  # 同键 INSERT OR REPLACE 生效


def test_champion_lifecycle_and_rollback(tmp_path):
    from db_repository import insert_strategy_config, load_champion_config, mark_config_status
    conn = _fresh_db(tmp_path)
    assert load_champion_config(conn) is None
    ratios = {"A": 0.2, "B": 0.1}
    v1 = insert_strategy_config(conn, ["A", "B"], ratios, "champion", {"win_rate": 0.5}, "bootstrap")
    v2 = insert_strategy_config(conn, ["A"], ratios, "champion", {"win_rate": 0.6}, "promoted")
    champ = load_champion_config(conn)
    assert champ["version"] == v2 and champ["active"] == ["A"]
    assert champ["ratios"] == ratios and champ["metrics"]["win_rate"] == 0.6
    # 回滚当前冠军 → 上一个 champion 生效
    mark_config_status(conn, v2, "rolled_back", "live drop")
    champ = load_champion_config(conn)
    assert champ["version"] == v1 and champ["active"] == ["A", "B"]
    conn.commit()
