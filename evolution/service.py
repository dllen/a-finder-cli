"""闭环编排：a-finder evolve 每周跑一次 标注→归因→分配→门禁→持久化。"""

from typing import Callable, Dict, List, Optional

from candidate_rules import DEFAULT_STRATEGY_RATIOS
from db_repository import (fetch_pick_outcomes, open_db, upsert_pick_outcomes)
from evolution import attribution, champion, labeling
from evolution.allocator import NoChange, next_config
from strategies import STRATEGIES

GATE_WINDOW_DAYS = 60  # 门禁滚动评估窗口（交易日数）


def _pct(x: Optional[float]) -> str:
    return "-" if x is None else f"{x * 100:.2f}%"


def _dedupe(rows: List[Dict]) -> List[Dict]:
    seen = {}
    for r in rows:
        seen[(r["date"], r["source"], r["code"], r["strategy"])] = r
    return list(seen.values())


def run_evolve(db_path: str, top: int = 20, backfill_days: int = 250,
               dry_run: bool = False,
               progress: Optional[Callable[[int, str], None]] = None) -> Dict:
    def say(pct: int, msg: str) -> None:
        if progress:
            progress(pct, msg)

    conn = open_db(db_path)
    try:
        champ = champion.ensure_champion(conn, dry_run=dry_run)
        if champ is None:
            say(100, "无 report.json 基线且无冠军配置，无法进化")
            return {"status": "no-baseline"}
        say(5, f"当前冠军 v{champ['version']}：active={champ['active']}")

        # 1. 标注（检测只跑一次；结果与配置无关）
        replay_new = labeling.replay_rows(db_path, backfill_days=backfill_days,
                                          progress=lambda p, m: say(5 + int(p * 0.7), m))
        live_new = labeling.live_rows(db_path)
        if not dry_run:
            with conn:
                upsert_pick_outcomes(conn, replay_new)
                upsert_pick_outcomes(conn, live_new)
            say(78, f"标注写库：重放 +{len(replay_new)}，真实 +{len(live_new)}")
        pool = fetch_pick_outcomes(conn, source="replay", judged_only=False) if not dry_run else []
        live = fetch_pick_outcomes(conn, source="live", judged_only=False) if not dry_run else []
        pool = _dedupe(pool + replay_new)
        live = _dedupe(live + live_new)
        pool_judged = [r for r in pool if r["win"] is not None]

        # 2. 回滚检查（在分配之前，回滚后以恢复的冠军为基线）
        report: Dict = {"champion": champ["version"]}
        live_m = champion.live_window_stats(live, champ.get("created_at") or "")
        if champion.should_rollback(champ, live_m):
            champion.rollback(conn, champ["version"],
                              f"真实表现 {live_m['win_rate']} < 基线 {champ['metrics']['win_rate']} - {champion.ROLLBACK_DROP}")
            restored = champion.load_champion_config(conn)
            say(80, f"触发自动回滚，恢复 v{restored['version'] if restored else '基线'}")
            champ = restored or champ
            report["rollback"] = {"to": champ["version"], "live": live_m}

        # 3. 归因
        stats = attribution.attribute(pool + live)
        say(85, "归因完成")

        # 4. 分配
        proposal = next_config(stats, champ["ratios"], list(champ["active"]), list(STRATEGIES))

        # 5. 门禁
        if isinstance(proposal, NoChange):
            say(95, f"NoChange：{proposal.reason}")
            report.update({"decision": "NoChange", "reason": proposal.reason,
                           "stats": {k: vars(v) for k, v in stats.items()}})
            return report

        dates = sorted({r["date"] for r in pool_judged})
        window = set(dates[-GATE_WINDOW_DAYS:])
        window_rows = [r for r in pool_judged if r["date"] in window]
        champ_m = champion.board_metrics(window_rows, champ["ratios"], top)
        chal_m = champion.board_metrics(window_rows, proposal.ratios, top)
        promote, reason = champion.decide(champ_m, chal_m)
        metrics = {**chal_m, "window": [window_rows[0]["date"], window_rows[-1]["date"]] if window_rows else None,
                   "beats": reason}
        if promote:
            if not dry_run:
                with conn:
                    version = champion.insert_strategy_config(
                        conn, proposal.active, proposal.ratios, "champion",
                        metrics=metrics, reason=f"promoted: {reason}")
            else:
                version = None
            say(100, f"晋升：挑战者 → v{version if version else '(dry-run)'}")
        else:
            if not dry_run:
                with conn:
                    champion.insert_strategy_config(
                        conn, proposal.active, proposal.ratios, "rejected",
                        metrics=metrics, reason=reason)
            say(100, f"拒绝：{reason}")
        report.update({
            "decision": "promoted" if promote else "rejected",
            "reason": reason,
            "champion_metrics": champ_m,
            "challenger_metrics": chal_m,
            "proposal": {"active": proposal.active, "ratios": proposal.ratios},
            "stats": {k: vars(v) for k, v in stats.items()},
        })
        return report
    finally:
        conn.close()


def format_report(report: Dict) -> str:
    lines = []
    stats = report.get("stats") or {}
    if stats:
        lines.append("策略            样本   胜率    expectancy")
        for name, s in sorted(stats.items(), key=lambda kv: -kv[1].get("expectancy", 0)):
            lines.append(f"{name:<12}  {s['n']:>5}  {_pct(s['win_rate']):>6}  {_pct(s['expectancy']):>6}")
    ratios = (report.get("proposal") or {}).get("ratios")
    if ratios:
        lines.append("建议配额（信号策略）：")
        for k, v in sorted(ratios.items(), key=lambda kv: -kv[1]):
            if k in DEFAULT_STRATEGY_RATIOS or v == 0.0 and k not in DEFAULT_STRATEGY_RATIOS:
                continue
            lines.append(f"  {k:<12} {v:.2%}")
    for key in ("champion_metrics", "challenger_metrics"):
        m = report.get(key)
        if m:
            lines.append(f"{key}: n={m['n']} 胜率={_pct(m['win_rate'])} exp={_pct(m['expectancy'])}")
    lines.append(f"决策：{report.get('decision', '?')} — {report.get('reason', '')}")
    return "\n".join(lines)
