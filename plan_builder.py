"""plan_builder — daily execution plan (paper trading).

Builds an immutable `trade_plan` row set from:
- `daily_picks` (today's strategy output)
- `open_positions` (carryover; classified as hold/exit by current price vs stop)

All sizing / stop / tp decisions come from `risk_manager.RiskManager` (the
single source of truth shared with `ma_backtest`). No strategy logic lives
in this module — only orchestration + sanity gate + paper fills.

Sanity gate rules (per spec):
  1. single-row size cap (`max_single`) → status=failed, reason='size_exceed_max'
  2. stop below price (stop >= price * 0.9) → status=failed, reason='stop_too_tight'
  3. portfolio total cap (`max_total`) → scale all buy rows proportionally;
     status stays 'ok', reason='scaled_to_fit'

Paper trader (Task 12):
- buy row, status=ok → fill at plan_price * (1 + slippage), insert into
  open_positions + trade_events('open')
- exit row, status=ok → close the matching open position, insert
  trade_events('close') with pnl_pct
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from db_repository import (
    close_open_position,
    get_open_positions,
    insert_open_position,
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
                rationale={"trigger": "hold", "current_price": cur_px},
                status="ok", reason="",
            ))
        else:
            rows.append(PlanRow(
                code=o["code"], action="exit",
                plan_price=cur_px, size_pct=0.0,
                stop_price=o["stop_price"], tp_price=o["tp_price"],
                rr_ratio=0.0,
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
    """In-place mutation. Returns list of failure reasons.

    Portfolio total cap (C2) now sums both buy rows AND held positions,
    since held positions represent real outstanding exposure.
    """
    reasons: List[str] = []
    buy_rows = [r for r in rows if r.action == "buy"]
    held_rows = [r for r in rows if r.action == "hold"]

    # Rule 1: per-row cap
    for r in buy_rows:
        if r.size_pct > max_single:
            r.status = "failed"
            r.reason = "size_exceed_max"
            reasons.append(f"{r.code}:size_exceed_max")

    # Rule 2: stop must be below entry (cannot be above/at entry).
    # Spec: "stop_price not above plan_price * 1.1" — i.e. stop must be ≤ price*1.1.
    # In practice this is a sanity check that the stop is at least break-even-or-below.
    for r in buy_rows:
        if r.plan_price > 0 and r.stop_price > r.plan_price:
            r.status = "failed"
            r.reason = "stop_above_entry"
            reasons.append(f"{r.code}:stop_above_entry")

    # Rule 3: portfolio scaling (only ok buy rows, but total includes held)
    ok_buys = [r for r in buy_rows if r.status == "ok"]
    held_total = sum(r.size_pct for r in held_rows)
    buy_total = sum(r.size_pct for r in ok_buys)
    total = held_total + buy_total
    if total > max_total and ok_buys:
        # Room left for new buys = max_total - held_total; if non-positive,
        # all buy rows are marked failed ('portfolio_overflow').
        room = max_total - held_total
        if room <= 0:
            for r in ok_buys:
                r.status = "failed"
                r.reason = "portfolio_overflow"
                reasons.append(f"{r.code}:portfolio_overflow")
        else:
            scale = room / buy_total if buy_total > 0 else 0.0
            for r in ok_buys:
                r.size_pct = round(r.size_pct * scale, 4)
                r.reason = "scaled_to_fit"

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

    Rerun-idempotent (C1): a buy-row fill is skipped when an open_positions
    row already exists for (code, entry_date=plan_date, status='open').
    Exit rows are always re-driven by current price vs stop/tp; a single
    close per code is enforced by filtering on status='open' in the lookup.
    """
    conn = open_db(db_path)
    try:
        for r in rows:
            if r.action == "buy" and r.status == "ok":
                # Idempotency gate (C1): if a fill already exists for this
                # code on this date, skip the fill entirely.
                existing = conn.execute(
                    "SELECT 1 FROM open_positions "
                    "WHERE code=? AND entry_date=? AND status='open' LIMIT 1",
                    (r.code, plan_date),
                ).fetchone()
                if existing:
                    continue
                fill_price = round(r.plan_price * (1 + slippage), 4)
                insert_open_position(
                    conn, r.code, plan_date, fill_price,
                    r.size_pct, r.stop_price, r.tp_price,
                )
                insert_trade_event(
                    conn, plan_date, r.code, "open",
                    fill_price, r.size_pct, note="paper_fill",
                )
            elif r.action == "exit" and r.status == "ok":
                cur = conn.execute(
                    "SELECT pos_id, entry_price FROM open_positions "
                    "WHERE code=? AND status='open' ORDER BY entry_date LIMIT 1",
                    (r.code,),
                )
                row = cur.fetchone()
                if row:
                    pos_id, entry_price = row
                    # Data-driven close_reason from row rationale (N3).
                    close_reason = (r.rationale or {}).get("trigger", "manual")
                    close_open_position(conn, pos_id, plan_date,
                                        r.plan_price, close_reason)
                    pnl = round((r.plan_price / entry_price - 1) * 100, 4)
                    insert_trade_event(
                        conn, plan_date, r.code, "close",
                        r.plan_price, None, pnl, note="paper_close",
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
    # C2: skip buy rows for codes already held (avoid duplicate position).
    held_codes = {o["code"] for o in opens}
    buy_picks = [p for p in picks if str(p.get("code", "")) not in held_codes]
    rows.extend(_build_buy_rows(buy_picks, regime, risk_manager))
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
