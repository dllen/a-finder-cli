"""冠军管理：bootstrap（report.json 冷启动）、门禁评估、晋升/回滚判定。"""

from types import SimpleNamespace
from typing import Dict, List, Optional, Tuple

from candidate_rules import select_candidates_with_quota
from db_repository import insert_strategy_config, load_champion_config, mark_config_status
from strategies.adapter import load_passed_strategies, merged_strategy_ratios

MIN_GATE_SAMPLES = 100   # 门禁评估最小样本量
WINRATE_MARGIN = 0.01    # 胜率超冠军 +1pp 直接晋升
TIE_BAND = 0.005         # 胜率 ±0.5pp 内视为持平，比 expectancy
ROLLBACK_MIN_LIVE = 20   # 回滚判定的真实成交最小样本
ROLLBACK_DROP = 0.05     # 真实胜率低于晋升基线 5pp → 回滚


def bootstrap_from_report() -> Optional[Dict]:
    """冷启动：把 report.json 的达标策略+默认合并比例包装成 v1 冠军配置。"""
    try:
        passed = load_passed_strategies()
    except Exception:
        return None
    if not passed:
        return None
    return {"active": sorted(passed), "ratios": merged_strategy_ratios(set(passed))}


def ensure_champion(conn, dry_run: bool = False) -> Optional[Dict]:
    champ = load_champion_config(conn)
    if champ:
        return champ
    base = bootstrap_from_report()
    if base is None:
        return None
    if not dry_run:
        with conn:
            insert_strategy_config(conn, base["active"], base["ratios"], "champion",
                                   metrics=None, reason="bootstrap:report.json")
        champ = load_champion_config(conn)
        if champ:
            return champ
    return {**base, "version": None, "created_at": "", "metrics": None}


def _agg(rets: List[float], wins: int) -> Dict:
    n = len(rets)
    return {"n": n, "win_rate": round(wins / n, 6) if n else 0.0,
            "expectancy": round(sum(rets) / n, 6) if n else 0.0}


def board_metrics(rows: List[Dict], ratios: Dict[str, float], top: int) -> Dict:
    """在同一份重放候选池上按配置重跑榜单选择，聚合胜负（门禁评估核心）。"""
    by_date: Dict[str, List[Dict]] = {}
    for r in rows:
        by_date.setdefault(r["date"], []).append(r)
    rets: List[float] = []
    wins = 0
    for _date, day in sorted(by_date.items()):
        cands = [
            {"strategy": r["strategy"], "score": r["score"] if r["score"] is not None else 0.0,
             "stock": SimpleNamespace(code=r["code"]), "_row": r}
            for r in day
        ]
        for c in select_candidates_with_quota(cands, top, ratios):
            row = c["_row"]
            if row.get("win") is None:
                continue
            wins += int(row["win"])
            rets.append(float(row["outcome_pct"]))
    return _agg(rets, wins)


def decide(champion_m: Dict, challenger_m: Dict) -> Tuple[bool, str]:
    if champion_m["n"] < MIN_GATE_SAMPLES or challenger_m["n"] < MIN_GATE_SAMPLES:
        return False, f"门禁样本不足（挑战者 {challenger_m['n']} / 冠军 {champion_m['n']} < {MIN_GATE_SAMPLES}）"
    dw = challenger_m["win_rate"] - champion_m["win_rate"]
    de = challenger_m["expectancy"] - champion_m["expectancy"]
    if challenger_m["win_rate"] >= champion_m["win_rate"] + WINRATE_MARGIN:
        return True, f"胜率 +{dw:.4f} ≥ +{WINRATE_MARGIN}"
    if abs(dw) <= TIE_BAND and de > 0:
        return True, f"胜率持平（{dw:+.4f}），expectancy +{de:.4f}"
    return False, f"未达标：胜率 {dw:+.4f}，expectancy {de:+.4f}"


def live_window_stats(live_rows: List[Dict], since: str) -> Dict:
    judged = [r for r in live_rows if r["win"] is not None and (not since or r["date"] > since)]
    return _agg([float(r["outcome_pct"]) for r in judged], sum(int(r["win"]) for r in judged))


def should_rollback(champion: Dict, live_m: Dict) -> bool:
    m = champion.get("metrics")
    if not m or m.get("win_rate") is None:
        return False
    if live_m["n"] < ROLLBACK_MIN_LIVE:
        return False
    return live_m["win_rate"] < float(m["win_rate"]) - ROLLBACK_DROP
