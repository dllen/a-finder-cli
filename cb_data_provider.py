"""可转债数据源层 — 多源降级。

数据源探明结论（2026-10-24 实测）：
- `ak.bond_zh_cov()` 全市场清单（1065 只），稳定。含未上市新券与已退市券。
- `ak.bond_zh_cov_info(symbol=...)` 单只条款（72 字段）。**必须用裸 6 位代码**，
  加 sh/sz 前缀会报 TypeError。
- `ak.bond_cb_redeem_jsl()` 强赎状态（319 只）。
- `ak.bond_cov_comparison()` 东财比价表 — 本机已被封 IP，仅作备源，失败静默降级。
- `ak.bond_cb_jsl()` 只返回 30 只（双低最低），不可作宇宙源。

所有取数失败都降级而非抛错：主源失败时读缓存快照，全失败时返回空列表。
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional

import akshare as ak
import pandas as pd

from utils import retry_call


def _to_float(v: Any) -> Optional[float]:
    """容错转 float：None / '-' / '' / NaN → None。"""
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip()
        if v in ("", "-", "--", "None"):
            return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if pd.isna(f):
        return None
    return f


def _to_date_str(v: Any) -> Optional[str]:
    """日期/时间戳 → 'YYYY-MM-DD'。空值 → None。"""
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip()
        if not v or v in ("-", "--", "NaT", "None"):
            return None
        try:
            return pd.to_datetime(v).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            return None
    try:
        if pd.isna(v):
            return None
        return pd.to_datetime(v).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# 全市场快照
# ---------------------------------------------------------------------------

def fetch_universe_snapshot() -> List[Dict[str, Any]]:
    """全市场可转债清单。失败返回 []（调用方负责降级到缓存）。"""
    try:
        df = retry_call(lambda: ak.bond_zh_cov())
    except Exception:
        return []
    if df is None or df.empty:
        return []

    rows: List[Dict[str, Any]] = []
    for _, r in df.iterrows():
        code = str(r.get("债券代码") or "").strip()
        if not code:
            continue
        listed = _to_date_str(r.get("上市时间")) is not None
        rows.append({
            "code": code,
            "name": str(r.get("债券简称") or "").strip(),
            "stock_code": str(r.get("正股代码") or "").strip(),
            "stock_name": str(r.get("正股简称") or "").strip(),
            "price": _to_float(r.get("债现价")),
            "convert_price": _to_float(r.get("转股价")),
            "convert_value": _to_float(r.get("转股价值")),
            "premium_rate": _to_float(r.get("转股溢价率")),
            "stock_price": _to_float(r.get("正股价")),
            "rating": str(r.get("信用评级") or "").strip(),
            "issue_size": _to_float(r.get("发行规模")),
            "listed": listed,
        })
    return rows


# ---------------------------------------------------------------------------
# 单只条款补全
# ---------------------------------------------------------------------------

def fetch_bond_detail(code: str) -> Dict[str, Any]:
    """取单只转债的条款字段。

    `symbol` 必须是裸 6 位代码 —— akshare 的这个接口不认 sh/sz 前缀。
    任何字段缺失都返回 None（该因子不参与打分），不抛异常。
    """
    out: Dict[str, Any] = {
        "code": code,
        "name": "",
        "bond_floor": None,       # PBV_RATIO — 纯债价值（面值比，如 1.01 = 债底 101 元）
        "expire_date": None,      # EXPIRE_DATE
        "rating": "",             # RATING
        "redeem_trig_price": None,   # REDEEM_TRIG_PRICE
        "resale_trig_price": None,   # RESALE_TRIG_PRICE
        "convert_price": None,    # TRANSFER_PRICE
        "stock_price": None,      # CONVERT_STOCK_PRICE
        "convert_value": None,    # TRANSFER_VALUE
        "current_bond_price": None,  # CURRENT_BOND_PRICE
    }
    try:
        df = retry_call(lambda: ak.bond_zh_cov_info(symbol=code))
    except Exception:
        return out
    if df is None or df.empty:
        return out

    r = df.iloc[0]
    out["name"] = str(r.get("SECURITY_NAME_ABBR") or "").strip()
    out["bond_floor"] = _to_float(r.get("PBV_RATIO"))
    out["expire_date"] = _to_date_str(r.get("EXPIRE_DATE"))
    out["rating"] = str(r.get("RATING") or "").strip()
    out["redeem_trig_price"] = _to_float(r.get("REDEEM_TRIG_PRICE"))
    out["resale_trig_price"] = _to_float(r.get("RESALE_TRIG_PRICE"))
    out["convert_price"] = _to_float(r.get("TRANSFER_PRICE"))
    out["stock_price"] = _to_float(r.get("CONVERT_STOCK_PRICE"))
    out["convert_value"] = _to_float(r.get("TRANSFER_VALUE"))
    out["current_bond_price"] = _to_float(r.get("CURRENT_BOND_PRICE"))
    return out


# ---------------------------------------------------------------------------
# 强赎状态
# ---------------------------------------------------------------------------

def fetch_redeem_status_map() -> Dict[str, Dict[str, Any]]:
    """{转债代码: {redeem_status, redeem_count, last_trade_date, ...}}。失败返回 {}。"""
    try:
        df = retry_call(lambda: ak.bond_cb_redeem_jsl())
    except Exception:
        return {}
    if df is None or df.empty:
        return {}

    out: Dict[str, Dict[str, Any]] = {}
    for _, r in df.iterrows():
        code = str(r.get("代码") or "").strip()
        if not code:
            continue
        out[code] = {
            "redeem_status": str(r.get("强赎状态") or "").strip(),
            "redeem_count": _to_float(r.get("强赎天计数")),
            "redeem_price": _to_float(r.get("强赎触发价")),
            "last_trade_date": _to_date_str(r.get("最后交易日")),
            "maturity_date": _to_date_str(r.get("到期日")),
            "convert_price": _to_float(r.get("转股价")),
            "stock_price": _to_float(r.get("正股价")),
        }
    return out


def today_str() -> str:
    return dt.date.today().isoformat()
