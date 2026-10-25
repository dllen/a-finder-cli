"""10w 决策台：计划 vs 实际的对照判定。

纯函数——输入 plan/actual/config，输出偏差列表。不碰数据库，便于
静态导出复用同一份实现。规则来源：docs/reports/10w-decision-system.md
第 4 章（仓位）与第 5 章（退出）。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from config import PORTFOLIO_10W


def _num(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


def dedupe_plan_rows(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按 code 去重，同 code 保留最后一条（trade_plan 回补会产生重复行）。"""
    seen: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for r in rows:
        code = str(r.get("code") or "").strip()
        if not code:
            continue
        if code not in seen:
            order.append(code)
        seen[code] = r
    return [seen[c] for c in order]


def _dev(severity: str, kind: str, code: str, name: str,
         message: str, actual: Any = None, expected: Any = None) -> Dict[str, Any]:
    return {
        "severity": severity, "kind": kind, "code": code, "name": name or code,
        "message": message, "actual": actual, "expected": expected,
    }


def compare_plan_vs_actual(
    plan_rows: Iterable[Dict[str, Any]],
    actual_rows: Iterable[Dict[str, Any]],
    cb_rows: Iterable[Dict[str, Any]],
    latest_prices: Dict[str, float],
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """对照计划与实际持仓，返回偏差列表 + 资金占用汇总。"""
    cfg = config or PORTFOLIO_10W
    total_capital = float(cfg["total_capital"])

    plan = dedupe_plan_rows(plan_rows)
    actual = list(actual_rows or [])
    cb = list(cb_rows or [])

    plan_by_code = {str(r.get("code")): r for r in plan}
    actual_by_code = {str(r.get("code")): r for r in actual}

    deviations: List[Dict[str, Any]] = []

    # ---- 资金占用 ----
    stock_used = 0.0
    for r in actual:
        amt = (_num(r.get("entry_price")) or 0.0) * int(r.get("shares") or 0)
        stock_used += amt
    cb_used = 0.0
    for r in cb:
        amt = (_num(r.get("entry_price")) or 0.0) * int(r.get("shares") or 0)
        cb_used += amt
    cash = total_capital - stock_used - cb_used

    # ---- 漏买 / 多买 ----
    for code, p in plan_by_code.items():
        if p.get("action") == "buy" and code not in actual_by_code:
            deviations.append(_dev(
                "info", "missing_buy", code, p.get("name"),
                "计划买入但未持有",
                actual=None, expected=p.get("shares"),
            ))
    for code, a in actual_by_code.items():
        if code not in plan_by_code:
            deviations.append(_dev(
                "warn", "unplanned", code, a.get("name"),
                "持有但计划中没有（超配）",
                actual=a.get("shares"), expected=0,
            ))

    # ---- 持仓数超限 ----
    if len(actual) > int(cfg["max_positions"]):
        deviations.append(_dev(
            "danger", "too_many_positions", "", "",
            f"持仓 {len(actual)} 只，超出上限 {cfg['max_positions']} 只",
            actual=len(actual), expected=int(cfg["max_positions"]),
        ))

    # ---- 单票超上限 ----
    max_single = float(cfg["max_single_weight"]) * total_capital
    for code, a in actual_by_code.items():
        amt = (_num(a.get("entry_price")) or 0.0) * int(a.get("shares") or 0)
        if amt > max_single:
            deviations.append(_dev(
                "danger", "single_weight_over", code, a.get("name"),
                f"单票占用 ¥{amt:,.0f}，超出 {cfg['max_single_weight']:.0%} 上限",
                actual=round(amt, 2), expected=round(max_single, 2),
            ))

    # ---- 缺止损 / 跌破止损 / 达止盈 ----
    for code, a in actual_by_code.items():
        stop = _num(a.get("stop_price"))
        price = _num(latest_prices.get(code))
        if stop is None:
            deviations.append(_dev(
                "warn", "missing_stop", code, a.get("name"),
                "未设置止损价",
                actual=None, expected=None,
            ))
        elif price is not None and price <= stop:
            deviations.append(_dev(
                "danger", "stop_breached", code, a.get("name"),
                f"最新价 {price:.2f} 已跌破止损 {stop:.2f}，应执行止损",
                actual=price, expected=stop,
            ))
        p = plan_by_code.get(code)
        tp = _num(p.get("tp_price")) if p else None
        if tp is not None and price is not None and price >= tp:
            deviations.append(_dev(
                "info", "tp_hit", code, a.get("name") or code,
                f"最新价 {price:.2f} 已达止盈 {tp:.2f}，可分批止盈",
                actual=price, expected=tp,
            ))

    # ---- 计划股数不符 ----
    for code, p in plan_by_code.items():
        a = actual_by_code.get(code)
        if a is None:
            continue
        want = p.get("shares")
        have = a.get("shares")
        if want is not None and have is not None and int(want) != int(have):
            deviations.append(_dev(
                "info", "shares_mismatch", code, p.get("name"),
                f"计划 {int(want)} 股，实际 {int(have)} 股",
                actual=int(have), expected=int(want),
            ))

    # ---- 总仓位超限 ----
    max_exposure = float(cfg["max_stock_exposure"]) * total_capital
    if stock_used > max_exposure:
        deviations.append(_dev(
            "danger", "exposure_over", "", "",
            f"股票占用 ¥{stock_used:,.0f}，超出 {cfg['max_stock_exposure']:.0%} 上限",
            actual=round(stock_used, 2), expected=round(max_exposure, 2),
        ))

    # ---- 转债仓超配 ----
    cb_capital = float(cfg["cb_capital"])
    if cb_used > cb_capital:
        deviations.append(_dev(
            "warn", "cb_over", "", "",
            f"转债占用 ¥{cb_used:,.0f}，超出 ¥{cb_capital:,.0f} 上限",
            actual=round(cb_used, 2), expected=cb_capital,
        ))
    if len(cb) > int(cfg["max_cb_positions"]):
        deviations.append(_dev(
            "warn", "cb_too_many", "", "",
            f"转债持仓 {len(cb)} 只，超出上限 {cfg['max_cb_positions']} 只",
            actual=len(cb), expected=int(cfg["max_cb_positions"]),
        ))

    sev_order = {"danger": 0, "warn": 1, "info": 2}
    deviations.sort(key=lambda x: sev_order.get(x["severity"], 3))

    return {
        "deviations": deviations,
        "stock_used": round(stock_used, 2),
        "cb_used": round(cb_used, 2),
        "cash": round(cash, 2),
        "total_capital": total_capital,
        "plan_count": len(plan),
        "actual_count": len(actual),
        "cb_count": len(cb),
        "danger_count": sum(1 for x in deviations if x["severity"] == "danger"),
        "warn_count": sum(1 for x in deviations if x["severity"] == "warn"),
        "info_count": sum(1 for x in deviations if x["severity"] == "info"),
    }