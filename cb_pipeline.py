"""可转债每日榜单 pipeline 编排。

流程：全市场快照 → 落库 → 粗筛 → 逐只补全 → 因子派生 → 硬剔除 → 打分 → 落库榜单。
主源失败时读当日 cb_universe 缓存兜底；全失败则返回空结果并告警。
"""
from __future__ import annotations

import datetime as dt
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Tuple

from cb_data_provider import (
    fetch_bond_detail,
    fetch_redeem_status_map,
    fetch_universe_snapshot,
)
from cb_repository import (
    get_cb_universe,
    replace_cb_picks,
    upsert_cb_factors_rows,
    upsert_cb_universe_rows,
)
from config import CB_FILTERS
from db_repository import open_db
from strategies.convertible import (
    apply_hard_exclusions,
    build_factor_map,
    build_pick_rows,
    prefilter,
    score_universe,
)


def _parse_weights(weights: Optional[Dict[str, float]]) -> Optional[Dict[str, float]]:
    if not weights:
        return None
    total = sum(weights.values())
    if total <= 0:
        raise ValueError("weights sum must be > 0")
    return {k: v / total for k, v in weights.items()}


def collect_universe(
    db_path: str,
    trade_date: str,
    progress: Optional[Callable[[int, str], None]] = None,
) -> Tuple[List[Dict[str, Any]], str]:
    """取全市场快照并落库。失败时回退到当日缓存。返回 (rows, source)。"""
    def _emit(pct: int, msg: str) -> None:
        if progress:
            progress(pct, msg)

    _emit(5, "拉取全市场可转债清单…")
    rows = fetch_universe_snapshot()
    if rows:
        conn = open_db(db_path)
        try:
            upsert_cb_universe_rows(conn, trade_date, rows)
        finally:
            conn.close()
        _emit(20, f"全市场 {len(rows)} 只已落库")
        return rows, "live"

    _emit(20, "主源失败，回退当日缓存快照…")
    conn = open_db(db_path)
    try:
        cached = get_cb_universe(conn, trade_date)
    finally:
        conn.close()
    if cached:
        return cached, "cache"
    return [], "none"


def enrich_details(
    rows: List[Dict[str, Any]],
    concurrency: int = 4,
    rate: float = 6.0,
    progress: Optional[Callable[[int, str], None]] = None,
) -> Dict[str, Dict[str, Any]]:
    """逐只补全条款。限速 + 并发；单只失败不中断。"""
    def _emit(pct: int, msg: str) -> None:
        if progress:
            progress(pct, msg)

    details: Dict[str, Dict[str, Any]] = {}
    if not rows:
        return details

    gap = 1.0 / max(rate, 0.1)

    def _one(r: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        time.sleep(gap)
        return r["code"], fetch_bond_detail(r["code"])

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        for i, (code, detail) in enumerate(pool.map(_one, rows), start=1):
            details[code] = detail
            if i % 20 == 0 or i == len(rows):
                _emit(20 + int(50 * i / len(rows)), f"补全 {i}/{len(rows)}")
    return details


def build_cb_picks(
    db_path: str,
    trade_date: Optional[str] = None,
    top_n: int = 20,
    capital: float = 100000.0,
    weights: Optional[Dict[str, float]] = None,
    concurrency: int = 4,
    rate: float = 6.0,
    premium_range: Optional[Tuple[float, float]] = None,
    dry_run: bool = False,
    progress: Optional[Callable[[int, str], None]] = None,
) -> Dict[str, Any]:
    """完整跑一遍转债榜单。返回统计信息。

    premium_range: 覆盖转股溢价率区间（如 (5, 30)），None 时用 config.CB_FILTERS 默认值。
    """
    trade_date = trade_date or dt.date.today().isoformat()

    def _emit(pct: int, msg: str) -> None:
        if progress:
            progress(pct, msg)

    universe, source = collect_universe(db_path, trade_date, progress=progress)
    if not universe:
        return {"ok": False, "source": source, "universe": 0, "candidates": 0,
                "picks": [], "dropped": {}, "warning": "全市场数据源不可用且无缓存"}

    pmin, pmax = premium_range or (CB_FILTERS["premium_min"], CB_FILTERS["premium_max"])
    candidates = prefilter(universe, premium_min=pmin, premium_max=pmax)
    _emit(25, f"粗筛后候选 {len(candidates)} 只")

    details = enrich_details(candidates, concurrency=concurrency, rate=rate, progress=progress)
    redeem_map = fetch_redeem_status_map()
    _emit(75, f"强赎状态 {len(redeem_map)} 只")

    # 合并强赎信息到补全结果
    for code, r in redeem_map.items():
        d = details.get(code)
        if d is None:
            continue
        d["redeem_status"] = r.get("redeem_status") or d.get("redeem_status")
        if d.get("redeem_trig_price") is None:
            d["redeem_trig_price"] = r.get("redeem_price")

    factors = build_factor_map(candidates, details, today=trade_date)
    kept, dropped = apply_hard_exclusions(candidates, factors)
    _emit(85, f"硬剔除 {len(dropped)} 只")

    scored = score_universe(kept, factors, weights=_parse_weights(weights))
    picks = build_pick_rows(scored, top_n=top_n, capital=capital)
    _emit(95, f"榜单 {len(picks)} 只")

    if not dry_run:
        conn = open_db(db_path)
        try:
            upsert_cb_factors_rows(conn, trade_date, [
                {"code": c, **{k: v for k, v in f.items() if k in (
                    "bond_floor", "ytm", "year_left", "redeem_dist", "put_dist",
                    "adj_count", "redeem_status")}}
                for c, f in factors.items()
            ])
            replace_cb_picks(conn, trade_date, picks)
        finally:
            conn.close()

    return {
        "ok": True,
        "source": source,
        "universe": len(universe),
        "candidates": len(candidates),
        "dropped": dropped,
        "picks": picks,
        "warning": "" if source == "live" else "主源失败，本次使用缓存快照",
    }
