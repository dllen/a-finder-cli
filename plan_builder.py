"""plan_builder — daily execution plan (paper trading).

Builds an immutable `trade_plan` row set from:
- `daily_picks` (today's strategy output)
- `open_positions` (carryover; classified as hold/exit by current price vs stop)

All sizing / stop / tp decisions come from `risk_manager.RiskManager` (the
single source of truth shared with `ma_backtest`). No strategy logic lives
in this module — only orchestration + sanity gate + paper fills.

Sanity gate rules (fixed-share lots):
  1. reference weight above `max_single` → warning only, reason='size_ref_warn';
     status stays 'ok' (200-share lots cannot be scaled down)
  2. stop at/above entry (`stop_price > plan_price`) → status=failed,
     reason='stop_above_entry' (the only hard failure)
  No portfolio scaling: `max_total` no longer shrinks buy rows.

Paper trader (Task 12):
- buy row, status=ok → fill at plan_price * (1 + slippage), insert into
  open_positions + trade_events('open')
- exit row, status=ok → close the matching open position, insert
  trade_events('close') with shares + pnl_amt
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from db_repository import (
    accumulate_open_position,
    close_open_position,
    get_open_positions,
    insert_trade_event,
    insert_trade_plan,
    open_db,
)
from market_regime import RegimeType
from risk_manager import RiskManager
from shared_lib.strategy import (
    PlanRow,
    compute_plan_prices,
    params_hash,
)


# Heuristic: map raw score (0..~3) to signal_strength (0..1) for RiskManager.
# 3.0 is a strong signal per the README's "key params" table.
_SCORE_MAX = 3.0


def _signal_strength(score: float) -> float:
    """Clamp score / SCORE_MAX into [0, 1]; feed RiskManager.get_config()."""
    if score is None:
        return 0.5
    return max(0.0, min(1.0, float(score) / _SCORE_MAX))


def _regime_from_str(regime: str) -> RegimeType:
    """Map user-facing regime string (params) to RegimeType enum."""
    try:
        return RegimeType(regime.lower())
    except (ValueError, AttributeError):
        return RegimeType.SIDEWAYS


@dataclass
class PlanResult:
    plan_date: str
    rows: List[PlanRow] = field(default_factory=list)
    num_picks: int = 0
    num_open_positions: int = 0
    sanity_passed: bool = True
    sanity_reasons: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_picks(conn, plan_date: str) -> List[Dict[str, Any]]:
    """Read today's daily_picks rows. Real schema: (date, rank, kind, code,
    name, strategy, buy, stop, target, score)."""
    cur = conn.execute(
        """SELECT code, score, buy, stop, target, strategy
           FROM daily_picks WHERE date = ? ORDER BY rank, code""",
        (plan_date,),
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _row_from_db(r: Dict[str, Any]) -> PlanRow:
    """Reconstruct a PlanRow from a trade_plan DB dict (cache-hit reconstruction)."""
    import json
    try:
        rationale = json.loads(r.get("rationale_json") or "{}")
    except (TypeError, ValueError):
        rationale = {}
    return PlanRow(
        code=str(r["code"]),
        action=r["action"],
        plan_price=float(r["plan_price"]),
        size_pct=float(r["size_pct"]),
        stop_price=float(r["stop_price"]),
        tp_price=float(r["tp_price"]),
        rr_ratio=float(r["rr_ratio"]),
        rationale=rationale,
        status=r["status"],
        reason=r.get("reason", "") or "",
        shares=int(r.get("shares") or 200),
    )


def _read_open_positions(conn) -> List[Dict[str, Any]]:
    """Read all open positions (carryover)."""
    return get_open_positions(conn)


def _lookup_current_prices(conn, codes: List[str]) -> Dict[str, float]:
    """Last close from daily_prices for each code. ponytail: hand-rolled since
    schema is small; switch to dedicated helper if used outside plan_builder."""
    if not codes:
        return {}
    placeholders = ",".join("?" for _ in codes)
    # SQLite supports window functions in 3.25+; DBs created by this project
    # always are. Latest close per code.
    cur = conn.execute(
        f"""SELECT code, close FROM (
            SELECT code, close, ROW_NUMBER() OVER (
                PARTITION BY code ORDER BY trade_date DESC
            ) AS rn FROM daily_prices WHERE code IN ({placeholders})
        ) WHERE rn = 1""",
        codes,
    )
    return {r[0]: r[1] for r in cur.fetchall()}


# ---------------------------------------------------------------------------
# Row builders
# ---------------------------------------------------------------------------

def _build_buy_rows(
    picks: List[Dict[str, Any]],
    regime: RegimeType,
    risk_manager: RiskManager,
    signal_strength_max: float = 1.0,
) -> List[PlanRow]:
    """Convert each daily_picks row into a PlanRow(action='buy').

    Plan price = picks.buy (the strategy-recommended entry). If absent,
    fall back to picks.target or 0.0 (sanity gate will catch bad values).
    """
    rows: List[PlanRow] = []
    for p in picks:
        plan_price = float(p.get("buy") or p.get("target") or 0.0)
        if plan_price <= 0:
            continue  # no usable price → skip; not a sanity failure
        score = float(p.get("score") or 0.0)
        strength = _signal_strength(score)
        cfg = risk_manager.get_config(regime, strength)
        stop, tp = compute_plan_prices(plan_price, cfg)
        risk = plan_price - stop
        rr = (tp - plan_price) / risk if risk > 0 else 0.0
        rows.append(PlanRow(
            code=str(p["code"]),
            action="buy",
            plan_price=plan_price,
            size_pct=cfg.position_size,
            stop_price=stop,
            tp_price=tp,
            rr_ratio=round(rr, 4),
            shares=200,
            rationale={
                "score": score,
                "regime": regime.value,
                "signal_strength": round(strength, 4),
                "stop_loss_pct": cfg.stop_loss_pct,
                "profit_target_pct": cfg.profit_target_pct,
                "strategy": p.get("strategy"),
            },
            status="ok",
            reason="",
        ))
    return rows


def _build_carryover_rows(
    opens: List[Dict[str, Any]],
    current_prices: Dict[str, float],
) -> List[PlanRow]:
    """Classify each open position as hold or exit.

    Exit trigger order (C3):
      1. cur_px >= tp_price  → exit, trigger='tp_hit'
      2. cur_px <= stop_price → exit, trigger='stop_hit'
      3. otherwise → hold
    """
    rows: List[PlanRow] = []
    for o in opens:
        cur_px = current_prices.get(o["code"], o["entry_price"])
        if cur_px >= o["tp_price"]:
            trigger = "tp_hit"
        elif cur_px <= o["stop_price"]:
            trigger = "stop_hit"
        else:
            trigger = "hold"

        if trigger == "hold":
            rows.append(PlanRow(
                code=o["code"], action="hold",
                plan_price=cur_px, size_pct=o["size_pct"],
                stop_price=o["stop_price"], tp_price=o["tp_price"],
                rr_ratio=0.0,
                shares=int(o.get("shares") or 0),
                rationale={"trigger": "hold", "current_price": cur_px},
                status="ok", reason="",
            ))
        else:
            rows.append(PlanRow(
                code=o["code"], action="exit",
                plan_price=cur_px, size_pct=0.0,
                stop_price=o["stop_price"], tp_price=o["tp_price"],
                rr_ratio=0.0,
                shares=int(o.get("shares") or 0),
                rationale={"trigger": trigger, "current_price": cur_px},
                status="ok", reason="",
            ))
    return rows


# ---------------------------------------------------------------------------
# Sanity gate
# ---------------------------------------------------------------------------

def _apply_sanity_gate(
    rows: List[PlanRow],
    max_single: float,
    max_total: float,
) -> List[str]:
    """Fixed-share sanity gate.

    Fixed 200-share lots disable portfolio-weight scaling (you cannot scale
    200 shares down to 190). `max_single` becomes a reference warning only;
    the only hard failure left is a stop placed at/above entry.
    """
    reasons: List[str] = []
    buy_rows = [r for r in rows if r.action == "buy"]

    # Rule 1 (downgraded): reference weight above max_single → warning only.
    for r in buy_rows:
        if r.size_pct > max_single:
            reasons.append(f"{r.code}:size_ref_warn")

    # Rule 2 (hard): stop must be below entry.
    for r in buy_rows:
        if r.plan_price > 0 and r.stop_price > r.plan_price:
            r.status = "failed"
            r.reason = "stop_above_entry"
            reasons.append(f"{r.code}:stop_above_entry")

    return reasons


# ---------------------------------------------------------------------------
# Paper trader
# ---------------------------------------------------------------------------

def _paper_trade(
    rows: List[PlanRow],
    plan_date: str,
    db_path: str,
    slippage: float,
) -> None:
    """Persist paper fills: open for buy rows, close for exit rows.

    Rerun-idempotent (C1): a buy-row fill is gated on an existing trade_events
    row for (code, plan_date, 'open'); same-code re-buys on different days
    ACCUMULATE (weighted-average entry) instead of being deduped. Exit rows
    are re-driven by current price vs stop/tp; a single close per code is
    enforced by filtering on status='open' in the lookup.
    """
    conn = open_db(db_path)
    try:
        for r in rows:
            if r.action == "buy" and r.status == "ok":
                # C1 幂等（改）：以 trade_events 的 (code, plan_date, 'open') 为准，
                # 允许同 code 跨日累积。
                existing = conn.execute(
                    "SELECT 1 FROM trade_events "
                    "WHERE code=? AND plan_date=? AND event_type='open' LIMIT 1",
                    (r.code, plan_date),
                ).fetchone()
                if existing:
                    continue
                fill_price = round(r.plan_price * (1 + slippage), 4)
                accumulate_open_position(
                    conn, r.code, fill_price, r.size_pct,
                    r.stop_price, r.tp_price, r.shares,
                    entry_date=plan_date,
                )
                insert_trade_event(
                    conn, plan_date, r.code, "open",
                    fill_price, r.size_pct, shares=r.shares, note="paper_fill",
                )
            elif r.action == "exit" and r.status == "ok":
                cur = conn.execute(
                    "SELECT pos_id, entry_price, shares FROM open_positions "
                    "WHERE code=? AND status='open' ORDER BY entry_date LIMIT 1",
                    (r.code,),
                )
                row = cur.fetchone()
                if row:
                    pos_id, entry_price, shares = row
                    close_reason = (r.rationale or {}).get("trigger", "manual")
                    close_open_position(conn, pos_id, plan_date,
                                        r.plan_price, close_reason)
                    pnl_amt = round((r.plan_price - entry_price) * (shares or 0), 2)
                    insert_trade_event(
                        conn, plan_date, r.code, "close",
                        r.plan_price, None, shares=shares, pnl_amt=pnl_amt,
                        note="paper_close",
                    )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def build_plan(
    plan_date: str,
    db_path: str,
    params: Optional[Dict[str, Any]] = None,
    slippage: float = 0.001,
) -> PlanResult:
    """Compose a daily plan from picks + carryover.

    params (optional):
      regime: str          — 'BULL' | 'BEAR' | 'SIDEWAYS' (default: SIDEWAYS)
      max_single: float    — single-position cap (default 0.15)
      max_total: float     — portfolio cap (default 0.95)
      min_score: float     — filter (currently unused; picks already filtered)
    """
    params = params or {}
    max_single = float(params.get("max_single", 0.15))
    max_total = float(params.get("max_total", 0.95))
    regime = _regime_from_str(params.get("regime", "SIDEWAYS"))
    risk_manager = RiskManager()
    phash = params_hash(params)

    # Cache hit: same (plan_date, params_hash) already persisted → skip rebuild.
    # INSERT OR IGNORE already prevents duplicate rows, but build_plan still
    # does the pick/open/price reads + sanity gate + paper fill on every call.
    from db_repository import get_trade_plan_by_date_and_hash
    cache_conn = open_db(db_path)
    try:
        cached_rows = get_trade_plan_by_date_and_hash(cache_conn, plan_date, phash)
    finally:
        cache_conn.close()
    if cached_rows:
        return PlanResult(
            plan_date=plan_date,
            rows=[_row_from_db(r) for r in cached_rows],
            num_picks=0,  # not stored; callers don't use it for cached path
            num_open_positions=0,
            sanity_passed=all(r["status"] == "ok" for r in cached_rows),
            sanity_reasons=[],
        )

    conn = open_db(db_path)
    try:
        picks = _read_picks(conn, plan_date)
        opens = _read_open_positions(conn)
        codes = list({o["code"] for o in opens})
        current_prices = _lookup_current_prices(conn, codes)
    finally:
        conn.close()

    rows: List[PlanRow] = []
    rows.extend(_build_buy_rows(picks, regime, risk_manager))
    rows.extend(_build_carryover_rows(opens, current_prices))

    reasons = _apply_sanity_gate(rows, max_single, max_total)

    # Persist the plan (phash already computed above)
    # Persist ALL plan rows (buy + hold + exit) before any paper fill so
    # that the trade_plan row acts as the gate for the fill (C1).
    write_conn = open_db(db_path)
    try:
        for r in rows:
            insert_trade_plan(write_conn, r, plan_date, phash)
    finally:
        write_conn.close()

    # Paper trade: insert_trade_plan inside _paper_trade returns 0 on dup,
    # which gates the corresponding fill (C1). Exit rows always re-run.
    _paper_trade(rows, plan_date, db_path, slippage)

    return PlanResult(
        plan_date=plan_date,
        rows=rows,
        num_picks=len(picks),
        num_open_positions=len(opens),
        sanity_passed=(not reasons),
        sanity_reasons=reasons,
    )
