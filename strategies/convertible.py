"""可转债策略 — 粗筛 / 因子打分 / 硬剔除 / 仓位计算。

打分模型：三组因子（债底 40% / 股性 30% / 条款 30%），组内等权。
因子缺失时该组按剩余因子重新归一化权重（不填 0，避免「缺数据 = 差」）。
整组无因子 → 该组为 None，不参与总分；三组全空 → 不评分。
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional, Tuple

from config import CB_FILTERS, CB_RISK, CB_SCORING_WEIGHTS


# ---------------------------------------------------------------------------
# 粗筛
# ---------------------------------------------------------------------------

def _is_st(name: str) -> bool:
    """正股是否 ST / *ST。"""
    n = (name or "").upper().replace(" ", "")
    return n.startswith("ST") or n.startswith("*ST") or "ST" in n


def _rating_excluded(rating: str) -> bool:
    r = (rating or "").upper()
    return any(bad in r for bad in CB_FILTERS["exclude_ratings"])


def _redeem_announced(status: str) -> bool:
    """强赎状态是否表示「已公告强赎」。

    JSL 的强赎状态是自由文本，实测值只有三类：「」(281 只)、「公告不强赎」(32)、
    「已公告强赎」(6)。必须精确匹配，避免把「公告不强赎」「强赎条款」误判为已公告。
    """
    s = (status or "").strip()
    if not s:
        return False
    # 先排除明确的否定/说明性表述
    if "未公告" in s or "不强赎" in s:
        return False
    if "条款" in s:
        return False
    return "已公告强赎" in s or "已触发强赎" in s


def prefilter(
    rows: List[Dict[str, Any]],
    premium_min: Optional[float] = None,
    premium_max: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """本地粗筛：剔除数据不可用 / 风险明显的转债。不打网络。

    premium_min / premium_max: None 时用 config.CB_FILTERS 的默认值。
    """
    pmin = CB_FILTERS["premium_min"] if premium_min is None else premium_min
    pmax = CB_FILTERS["premium_max"] if premium_max is None else premium_max
    out: List[Dict[str, Any]] = []
    for r in rows:
        if not r.get("listed"):
            continue
        if _is_st(str(r.get("stock_name") or "")):
            continue
        if _rating_excluded(str(r.get("rating") or "")):
            continue
        price = r.get("price")
        if price is None or not (CB_FILTERS["price_min"] <= price <= CB_FILTERS["price_max"]):
            continue
        premium = r.get("premium_rate")
        if premium is None or not (pmin <= premium <= pmax):
            continue
        cv = r.get("convert_value")
        if cv is None or cv < CB_FILTERS["min_convert_value"]:
            continue
        cp = r.get("convert_price")
        sp = r.get("stock_price")
        if cp is None or cp <= 0 or sp is None or sp <= 0:
            continue
        size = r.get("issue_size")
        if size is None or size < CB_FILTERS["min_issue_size"]:
            continue
        out.append(r)
    return out


# ---------------------------------------------------------------------------
# 硬剔除
# ---------------------------------------------------------------------------

def apply_hard_exclusions(
    rows: List[Dict[str, Any]],
    factors: Dict[str, Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    """剔除需要补全数据才能判定的风险项。返回 (存活列表, {code: 剔除原因})。"""
    kept: List[Dict[str, Any]] = []
    dropped: Dict[str, str] = {}
    for r in rows:
        code = r["code"]
        f = factors.get(code) or {}
        status = str(f.get("redeem_status") or "")
        if _redeem_announced(status):
            dropped[code] = "已公告强赎"
            continue
        dist = f.get("redeem_dist")
        if dist is not None and dist < CB_FILTERS["redeem_dist_min"]:
            dropped[code] = f"强赎距离仅 {dist:.1f}%"
            continue
        if _is_st(str(r.get("stock_name") or "")):
            dropped[code] = "正股 ST"
            continue
        kept.append(r)
    return kept, dropped


# ---------------------------------------------------------------------------
# 因子打分
# ---------------------------------------------------------------------------

def _score_bond_floor(r: Dict[str, Any], f: Dict[str, Any]) -> Optional[float]:
    """纯债价值保护度。

    `bond_floor` 存的是 **PBV_RATIO**（纯债价值/面值，如 1.01 表示债底 101 元），
    不是元。纯债价值(元) = PBV_RATIO × 100。保护度 = 纯债价值(元) / 债现价。
    """
    floor_ratio = f.get("bond_floor")
    price = r.get("price")
    if floor_ratio is None or price is None or price <= 0:
        return None
    ratio = (floor_ratio * 100.0) / price
    if ratio >= 1.0:
        return 100.0
    if ratio >= 0.95:
        return 90.0
    if ratio >= 0.90:
        return 75.0
    if ratio >= 0.85:
        return 60.0
    if ratio >= 0.80:
        return 40.0
    return 20.0


def _score_price_level(r: Dict[str, Any], f: Dict[str, Any]) -> Optional[float]:
    p = r.get("price")
    if p is None:
        return None
    if p <= 90:
        return 100.0
    if p <= 100:
        return 85.0
    if p <= 110:
        return 70.0
    if p <= 120:
        return 55.0
    if p <= 130:
        return 40.0
    return 20.0


def _score_ytm(r: Dict[str, Any], f: Dict[str, Any]) -> Optional[float]:
    y = f.get("ytm")
    if y is None:
        return None
    if y >= 3:
        return 100.0
    if y >= 2:
        return 85.0
    if y >= 1:
        return 70.0
    if y >= 0:
        return 55.0
    if y > -1:
        return 35.0
    return 15.0


_RATING_SCORES = {
    "AAA": 100.0, "AA+": 95.0, "AA": 85.0, "AA-": 70.0,
    "A+": 50.0, "A": 35.0, "A-": 20.0,
}


def _score_rating(r: Dict[str, Any], f: Dict[str, Any]) -> Optional[float]:
    rating = (f.get("rating") or "").upper().strip()
    if not rating:
        return 60.0  # 中性
    if _rating_excluded(rating):
        return 0.0
    for key, score in _RATING_SCORES.items():
        if rating.startswith(key):
            return score
    return 60.0


def _score_year_left(r: Dict[str, Any], f: Dict[str, Any]) -> Optional[float]:
    y = f.get("year_left")
    if y is None:
        return None
    if y < 1:
        return 30.0
    if y < 2:
        return 60.0
    if y < 3:
        return 80.0
    if y <= 6:
        return 100.0
    return 60.0


def _score_premium(r: Dict[str, Any], f: Dict[str, Any]) -> Optional[float]:
    p = r.get("premium_rate")
    if p is None:
        return None
    if p <= 5:
        return 100.0
    if p <= 10:
        return 90.0
    if p <= 15:
        return 75.0
    if p <= 20:
        return 60.0
    if p <= 30:
        return 40.0
    if p <= 50:
        return 20.0
    return 0.0


def _score_convert_ratio(r: Dict[str, Any], f: Dict[str, Any]) -> Optional[float]:
    cv = r.get("convert_value")
    if cv is None:
        return None
    if cv >= 110:
        return 100.0
    if cv >= 100:
        return 85.0
    if cv >= 95:
        return 70.0
    if cv >= 85:
        return 50.0
    return 30.0


def _score_elasticity(r: Dict[str, Any], f: Dict[str, Any]) -> Optional[float]:
    """债涨幅/股涨幅。当日涨跌幅不可得时返回 None（不计分）。"""
    bond_pct = r.get("bond_pct_change")
    stock_pct = r.get("stock_pct_change")
    if bond_pct is None or stock_pct is None or abs(stock_pct) < 1e-6:
        return None
    ratio = bond_pct / stock_pct
    if ratio >= 1.0:
        return 100.0
    if ratio >= 0.8:
        return 75.0
    if ratio >= 0.6:
        return 50.0
    return 25.0


def _score_stock_momentum(r: Dict[str, Any], f: Dict[str, Any]) -> Optional[float]:
    """正股 20 日涨幅。第一期无正股历史 K 线 → 恒为 None（不计分）。"""
    m = f.get("stock_momentum")
    if m is None:
        return None
    if m >= 15:
        return 100.0
    if m >= 8:
        return 80.0
    if m >= 0:
        return 55.0
    if m >= -10:
        return 30.0
    return 15.0


def _score_redeem_dist(r: Dict[str, Any], f: Dict[str, Any]) -> Optional[float]:
    d = f.get("redeem_dist")
    if d is None:
        return None
    if d >= 40:
        return 100.0
    if d >= 25:
        return 85.0
    if d >= 15:
        return 70.0
    if d >= 8:
        return 50.0
    if d >= 3:
        return 30.0
    return 0.0


def _score_put_dist(r: Dict[str, Any], f: Dict[str, Any]) -> Optional[float]:
    d = f.get("put_dist")
    if d is None:
        return None
    if d >= -20:
        return 100.0
    if d >= -35:
        return 80.0
    if d >= -50:
        return 60.0
    if d >= -65:
        return 40.0
    return 20.0


def _score_adj_count(r: Dict[str, Any], f: Dict[str, Any]) -> Optional[float]:
    """下修次数与得分正相关：多次下修 = 公司促转股意愿强。第一期无可靠源 → 50 中性。"""
    n = f.get("adj_count")
    if n is None:
        return 50.0
    if n <= 0:
        return 50.0
    if n == 1:
        return 80.0
    if n == 2:
        return 100.0
    return 95.0


# 每组：(因子名, 打分函数)。所有打分函数统一签名 (r, f)。
_GROUP_A = [
    ("bond_floor", _score_bond_floor),
    ("price_level", _score_price_level),
    ("ytm", _score_ytm),
    ("rating", _score_rating),
    ("year_left", _score_year_left),
]
_GROUP_B = [
    ("premium", _score_premium),
    ("convert_ratio", _score_convert_ratio),
    ("elasticity", _score_elasticity),
    ("stock_momentum", _score_stock_momentum),
]
_GROUP_C = [
    ("redeem_dist", _score_redeem_dist),
    ("put_dist", _score_put_dist),
    ("adj_count", _score_adj_count),
]

GROUPS = {"a": _GROUP_A, "b": _GROUP_B, "c": _GROUP_C}


def _combine_group(
    group: List[Tuple[str, Any]],
    r: Dict[str, Any],
    f: Dict[str, Any],
) -> Tuple[Optional[float], Dict[str, Any], int]:
    """组内等权 + 缺失因子重新归一化。返回 (组分, 因子明细, 参与因子数)。"""
    detail: Dict[str, Any] = {}
    scores: List[float] = []
    for name, fn in group:
        try:
            s = fn(r, f)
        except Exception:
            s = None
        if s is None:
            detail[name] = None
            continue
        detail[name] = {"score": s}
        scores.append(s)
    if not scores:
        return None, detail, 0
    return sum(scores) / len(scores), detail, len(scores)


def score_bond(
    r: Dict[str, Any],
    f: Dict[str, Any],
    weights: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """给单只转债打分。返回 {total_score, a_score, b_score, c_score, factors, raw_factors, factor_counts}。"""
    weights = weights or CB_SCORING_WEIGHTS
    f = f or {}

    a_score, a_detail, a_n = _combine_group(_GROUP_A, r, f)
    b_score, b_detail, b_n = _combine_group(_GROUP_B, r, f)
    c_score, c_detail, c_n = _combine_group(_GROUP_C, r, f)

    parts = {"a": (a_score, a_n), "b": (b_score, b_n), "c": (c_score, c_n)}
    live = {k: v for k, v in parts.items() if v[0] is not None}
    if not live:
        return {
            "total_score": None, "a_score": a_score, "b_score": b_score,
            "c_score": c_score, "factors": {**a_detail, **b_detail, **c_detail},
            "raw_factors": {}, "factor_counts": {"a": a_n, "b": b_n, "c": c_n},
        }

    # 支持部分权重（如 --weights b=0.5,c=0.5）：缺失的组权重取 0，
    # 只对实际有分的组做归一化。
    wsum = sum(weights.get(k, 0.0) for k in live)
    if wsum <= 0:
        # 所有存活组的权重都是 0 → 退化为等权
        total = sum(v[0] for v in live.values()) / len(live)
    else:
        total = sum(weights.get(k, 0.0) * v[0] for k, v in live.items()) / wsum

    factors: Dict[str, Any] = {}
    for k, detail in (("a", a_detail), ("b", b_detail), ("c", c_detail)):
        for name, d in detail.items():
            factors[name] = d

    # 原始因子值（供风险提示等需要方向性数值的场景使用，如 put_dist 用距离而非打分）
    raw_factors = {
        "bond_floor": f.get("bond_floor"),
        "premium_rate": r.get("premium_rate"),
        "convert_value": r.get("convert_value"),
        "redeem_dist": f.get("redeem_dist"),
        "put_dist": f.get("put_dist"),
        "year_left": f.get("year_left"),
        "issue_size": r.get("issue_size"),
    }

    return {
        "total_score": round(total, 4),
        "a_score": None if a_score is None else round(a_score, 4),
        "b_score": None if b_score is None else round(b_score, 4),
        "c_score": None if c_score is None else round(c_score, 4),
        "factors": factors,
        "raw_factors": raw_factors,
        "factor_counts": {"a": a_n, "b": b_n, "c": c_n},
    }


def score_universe(
    rows: List[Dict[str, Any]],
    factors: Dict[str, Dict[str, Any]],
    weights: Optional[Dict[str, float]] = None,
) -> List[Dict[str, Any]]:
    """给全量转债打分并按总分降序返回。只保留 total_score 非 None 且因子数达标的。"""
    out: List[Dict[str, Any]] = []
    for r in rows:
        f = factors.get(r["code"]) or {}
        s = score_bond(r, f, weights=weights)
        if s["total_score"] is None:
            continue
        if sum(s["factor_counts"].values()) < CB_FILTERS["min_factors"]:
            continue
        out.append({**r, **s})
    out.sort(key=lambda x: x["total_score"], reverse=True)
    return out


# ---------------------------------------------------------------------------
# 因子派生（补全数据 → 打分因子）
# ---------------------------------------------------------------------------

def _years_between(today: str, expire_date: str) -> Optional[float]:
    try:
        t = dt.date.fromisoformat(today)
        e = dt.date.fromisoformat(expire_date)
    except (ValueError, TypeError):
        return None
    return (e - t).days / 365.0


def build_factor_map(
    rows: List[Dict[str, Any]],
    details: Dict[str, Dict[str, Any]],
    today: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """把补全后的条款数据转成打分因子。

    缺失一律写 None（该因子不参与打分），不抛异常。
    """
    today = today or dt.date.today().isoformat()
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        code = r["code"]
        d = details.get(code) or {}

        year_left = _years_between(today, d.get("expire_date")) if d.get("expire_date") else None

        # 强赎/回售触发距离：优先用补全数据，缺失则用转股价 ×1.3 / ×0.7 估算
        cp = d.get("convert_price") or r.get("convert_price")
        sp = d.get("stock_price") or r.get("stock_price")
        redeem_trig = d.get("redeem_trig_price")
        resale_trig = d.get("resale_trig_price")
        if redeem_trig is None and cp:
            redeem_trig = cp * 1.3
        if resale_trig is None and cp:
            resale_trig = cp * 0.7

        redeem_dist = None
        if redeem_trig and sp and sp > 0:
            redeem_dist = round((redeem_trig / sp - 1.0) * 100.0, 4)
        put_dist = None
        if resale_trig and sp and sp > 0:
            put_dist = round((resale_trig / sp - 1.0) * 100.0, 4)

        # 到期税前收益：到期赎回价默认面值 ×1.10（主流条款）
        price = r.get("price")
        ytm = None
        ytm_estimated = False
        if price and price > 0 and year_left and year_left > 0:
            redeem_at_maturity = 110.0
            ytm = round(
                ((redeem_at_maturity / price) ** (1.0 / year_left) - 1.0) * 100.0, 4
            )
            ytm_estimated = True

        out[code] = {
            "bond_floor": d.get("bond_floor"),
            "expire_date": d.get("expire_date"),
            "year_left": year_left,
            "ytm": ytm,
            "ytm_estimated": ytm_estimated,
            "redeem_dist": redeem_dist,
            "put_dist": put_dist,
            "adj_count": d.get("adj_count") if d.get("adj_count") is not None else 50,
            "redeem_status": d.get("redeem_status"),
            "rating": d.get("rating") or r.get("rating"),
        }
    return out


# ---------------------------------------------------------------------------
# 仓位计算
# ---------------------------------------------------------------------------

def size_shares_cb(capital: float, size_pct: float, price: float) -> int:
    """可买张数（10 的整数倍）。不足 1 手返回 0。"""
    if capital <= 0 or size_pct <= 0 or price <= 0:
        return 0
    lot = CB_RISK["lot_size"]
    raw = (capital * size_pct) / (price * lot)
    n = int(raw) * lot
    return n if n >= lot else 0


def _build_reason(s: Dict[str, Any]) -> str:
    """自动生成入选理由。"""
    parts: List[str] = []
    factors = s.get("factors") or {}
    bf = factors.get("bond_floor")
    if bf and bf.get("score", 0) >= 75:
        parts.append("债底保护充分")
    pm = factors.get("premium")
    if pm and pm.get("score", 0) >= 75:
        parts.append("溢价率偏低")
    rd = factors.get("redeem_dist")
    if rd and rd.get("score", 0) >= 50:
        parts.append("距强赎安全")
    if not parts:
        parts.append("综合评分居前")
    return " + ".join(parts)


def _build_risk_note(r: Dict[str, Any], s: Dict[str, Any]) -> str:
    """自动生成风险提示。

    `put_dist` 用**原始距离**（负数表示已进入回售区）判定，
    不是打分（打分为 0-100，无法表示方向）。
    """
    notes: List[str] = []
    size = r.get("issue_size")
    if size is not None and size < 1.0:
        notes.append(f"剩余规模 {size:.1f} 亿偏小")
    cv = r.get("convert_value")
    if cv is not None and cv < 90:
        notes.append(f"转股价值 {cv:.0f} 偏低")
    put_dist = (s.get("raw_factors") or {}).get("put_dist")
    if put_dist is not None and put_dist < -50:
        notes.append("已进入回售区")
    if not notes:
        return ""
    return "；".join(notes)


def build_pick_rows(
    scored: List[Dict[str, Any]],
    top_n: int = 20,
    capital: float = 100000.0,
) -> List[Dict[str, Any]]:
    """把打分结果转成榜单行（含仓位、止损、止盈、理由、风险提示）。"""
    size_pct = CB_RISK["max_single"]
    stop_pct = CB_RISK["stop_loss_pct"]
    tp_pct = CB_RISK["take_profit_pct"]

    out: List[Dict[str, Any]] = []
    for s in scored[:top_n]:
        price = s.get("price") or 0.0
        shares = size_shares_cb(capital, size_pct, price) if price > 0 else 0
        if shares <= 0:
            continue
        out.append({
            "rank": len(out) + 1,
            "code": s["code"],
            "name": s.get("name") or s["code"],
            "price": round(price, 4),
            "total_score": s["total_score"],
            "a_score": s.get("a_score"),
            "b_score": s.get("b_score"),
            "c_score": s.get("c_score"),
            "size_pct": size_pct,
            "shares": shares,
            "stop_price": round(price * (1 - stop_pct), 4),
            "tp_price": round(price * (1 + tp_pct), 4),
            "reason": _build_reason(s),
            "risk_note": _build_risk_note(s, s),
            "factor_counts": s.get("factor_counts"),
        })
    return out


# ---------------------------------------------------------------------------
# 退出预警
# ---------------------------------------------------------------------------

# 级别从高到低；同一持仓命中多条时取最高级别
ALERT_LEVELS = ("emergency", "important", "watch", "normal")

_LEVEL_LABEL = {
    "emergency": "🔴 紧急",
    "important": "🟠 重要",
    "watch": "🟡 关注",
    "normal": "⚪ 常规",
}


def evaluate_alerts(
    positions: List[Dict[str, Any]],
    quotes: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """扫描持仓生成退出预警。

    positions: [{code, name, shares, entry_price, stop_price, tp_price}, ...]
    quotes:    {code: {price, redeem_status, redeem_dist, premium_rate,
                      stock_price, convert_price}, ...}

    规则（同一持仓命中多条取最高级别）：
      emergency  已公告强赎 / 强赎距离 < 3%
      important  跌破止损 / 进入回售区（正股 < 转股价×0.7）
      watch      强赎距离 3-8% / 转股溢价率 > 50%
      normal     达到止盈目标
    """
    out: List[Dict[str, Any]] = []
    for p in positions:
        code = p.get("code")
        q = quotes.get(code) or {}
        price = q.get("price")
        status = str(q.get("redeem_status") or "")
        redeem_dist = q.get("redeem_dist")
        premium = q.get("premium_rate")
        stock_price = q.get("stock_price")
        convert_price = q.get("convert_price")

        hits: List[Tuple[str, str]] = []

        if _redeem_announced(status):
            hits.append(("emergency", "已公告强赎，立即清仓"))
        elif redeem_dist is not None and redeem_dist < CB_FILTERS["redeem_dist_min"]:
            hits.append(("emergency", f"距强赎仅 {redeem_dist:.1f}%，立即清仓"))

        if price is not None:
            stop = p.get("stop_price")
            if stop is not None and price <= stop:
                hits.append(("important", f"跌破止损价 {stop:.2f}，减仓"))
        if stock_price is not None and convert_price:
            if stock_price < convert_price * 0.7:
                hits.append(("important", "正股进入回售区，评估回售"))

        if redeem_dist is not None and 3.0 <= redeem_dist < 8.0:
            hits.append(("watch", f"距强赎 {redeem_dist:.1f}%，注意"))
        if premium is not None and premium > 50.0:
            hits.append(("watch", f"转股溢价率 {premium:.1f}%，估值透支"))

        if price is not None:
            tp = p.get("tp_price")
            if tp is not None and price >= tp:
                hits.append(("normal", f"达到止盈目标 {tp:.2f}，分批止盈"))

        if not hits:
            continue
        level = min((h[0] for h in hits), key=lambda lv: ALERT_LEVELS.index(lv))
        messages = [h[1] for h in hits if h[0] == level]
        out.append({
            "code": code,
            "name": p.get("name") or code,
            "shares": p.get("shares"),
            "level": level,
            "level_label": _LEVEL_LABEL[level],
            "price": price,
            "message": "；".join(messages),
        })
    out.sort(key=lambda a: ALERT_LEVELS.index(a["level"]))
    return out
