import re
from typing import Dict, List, Optional, Set

import akshare as ak

from db_repository import FundamentalsRow, PriceRow, StockMeta
from utils import retry_call


def fetch_daily_kline_akshare(code: str, start_date: str, end_date: str) -> List[PriceRow]:
    def _fetch() -> List[PriceRow]:
        symbol = code
        if code.startswith(("60", "688")):
            symbol = f"sh{code}"
        else:
            symbol = f"sz{code}"

        df = ak.stock_zh_a_hist(
            symbol=symbol,
            start_date=start_date.replace("-", ""),
            end_date=end_date.replace("-", ""),
            adjust="qfq",
        )
        if df is None or df.empty:
            return []

        rows = []
        for _, entry in df.iterrows():
            rows.append(
                PriceRow(
                    code=code,
                    trade_date=str(entry["日期"]),
                    open_price=float(entry["开盘"]),
                    close_price=float(entry["收盘"]),
                    high_price=float(entry["最高"]),
                    low_price=float(entry["最低"]),
                    volume=float(entry["成交量"]),
                    amount=float(entry["成交额"]),
                    amplitude=float(entry["振幅"]) if "振幅" in entry else 0.0,
                    pct_change=float(entry["涨跌幅"]) if "涨跌幅" in entry else 0.0,
                    change=float(entry["涨跌额"]) if "涨跌额" in entry else 0.0,
                    turnover=float(entry["换手率"]) if "换手率" in entry else 0.0,
                )
            )
        return rows

    return retry_call(_fetch)


def fetch_stock_meta_akshare(code: str) -> Optional[StockMeta]:
    def _fetch() -> Optional[StockMeta]:
        try:
            df = ak.stock_info_a_code_name()
            if df is not None and not df.empty:
                matched = df[df["code"] == code]
                if not matched.empty:
                    name = str(matched.iloc[0]["name"])
                    return StockMeta(code=code, name=name, industry="", region="")
        except Exception:
            pass

        try:
            df = ak.stock_individual_info_em(symbol=code)
            if df is not None and not df.empty:
                name = ""
                region = ""
                for _, row in df.iterrows():
                    if row["item"] == "股票简称":
                        name = str(row["value"])
                    elif row["item"] == "区域":
                        region = str(row["value"])
                return StockMeta(code=code, name=name or code, industry="", region=region)
        except Exception:
            pass

        return StockMeta(code=code, name=code, industry="", region="")

    return retry_call(_fetch)


def _abstract_metric(df, name: str, col: Optional[str] = None) -> Optional[List[float]]:
    """取 stock_financial_abstract 中指定指标行的全部（或指定列）报告期数值。"""
    matched = df[df["指标"] == name]
    if matched.empty:
        return None
    if col is not None:
        values = []
        for _, row in matched.iterrows():
            try:
                values.append(float(row[col]))
            except (TypeError, ValueError):
                values.append(float("nan"))
        return values
    values = []
    for c in df.columns[2:]:
        for _, row in matched.iterrows():
            try:
                values.append(float(row[c]))
            except (TypeError, ValueError):
                values.append(float("nan"))
    return values


def _annual_metrics(df) -> Dict[int, dict]:
    """从 stock_financial_abstract DataFrame 抽年报关键指标。列名格式：YYYYMMDD。"""
    out: Dict[int, dict] = {}
    if df is None or df.empty:
        return out
    annual_cols = [c for c in df.columns[2:] if re.match(r"^\d{4}1231$", str(c))]
    for col in annual_cols:
        year = int(str(col)[:4])
        out[year] = {
            "gross_margin": _first_valid(_abstract_metric(df, "毛利率", col=col) or []),
            "roe_excl": _first_valid(_abstract_metric(df, "净资产收益率(扣非)", col=col) or []),
        }
    return out


def _first_valid(values: Optional[List[float]]) -> float:
    if not values:
        return 0.0
    for v in values:
        if v == v:  # not NaN
            return v
    return 0.0


def _cagr_3y(df, name: str) -> float:
    """用年报列（YYYY1231）计算 3 年 CAGR，数据不足或基期非正返回 0"""
    matched = df[df["指标"] == name]
    if matched.empty:
        return 0.0
    row = matched.iloc[0]
    annual = {c: row[c] for c in df.columns[2:] if str(c).endswith("1231")}
    years = sorted(annual.keys(), reverse=True)
    for latest in years:
        try:
            v1 = float(annual[latest])
        except (TypeError, ValueError):
            continue
        base_year = str(int(str(latest)[:4]) - 3) + "1231"
        if base_year not in annual:
            continue
        try:
            v0 = float(annual[base_year])
        except (TypeError, ValueError):
            continue
        if v0 > 0 and v1 > 0:
            return (v1 / v0) ** (1 / 3) - 1
        return 0.0
    return 0.0


def _std(values: List[float]) -> float:
    vals = [v for v in values if v == v]
    if len(vals) < 2:
        return 0.0
    mean = sum(vals) / len(vals)
    return (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5


def _baidu_valuation(code: str, indicator: str) -> float:
    df = ak.stock_zh_valuation_baidu(symbol=code, indicator=indicator, period="近一年")
    if df is None or df.empty:
        return 0.0
    return float(df["value"].dropna().iloc[-1])


def _dividend_stats(records: List[tuple]) -> tuple:
    """由分红记录[(日期, 每10股派息)]计算 (近12月每股分红, 近5年稳定性0-1)"""
    import datetime as dt

    if not records:
        return 0.0, 0.0
    now = dt.date.today()
    ttm_cut = (now - dt.timedelta(days=365)).isoformat()
    five_cut = (now - dt.timedelta(days=365 * 5)).isoformat()
    dps_ttm = sum(b for d, b in records if d >= ttm_cut) / 10.0
    years = len({d[:4] for d, _ in records if d >= five_cut})
    return dps_ttm, min(years / 5.0, 1.0)


def fetch_fundamentals_akshare(code: str, close_price: float, dividends: List[tuple] = None) -> FundamentalsRow:
    """聚合单只股票基本面：新浪财务摘要 + 百度估值 + 分红记录。单项失败降级为 0。"""

    def _fetch() -> FundamentalsRow:
        pe = pb = 0.0
        roe = debt_ratio = gross_margin = gross_margin_std = 0.0
        revenue_growth = profit_growth = revenue_cagr_3y = profit_cagr_3y = 0.0
        cashflow = 0.0
        dps_ttm, dividend_stability = _dividend_stats(dividends or [])
        dividend_yield = dps_ttm / close_price if close_price > 0 else 0.0

        try:
            df = ak.stock_financial_abstract(symbol=code)
            if df is not None and not df.empty:
                roe = _first_valid(_abstract_metric(df, "净资产收益率(ROE)"))
                debt_ratio = _first_valid(_abstract_metric(df, "资产负债率")) / 100.0
                gross_margin = _first_valid(_abstract_metric(df, "毛利率"))
                gm_series = _abstract_metric(df, "毛利率") or []
                gross_margin_std = _std(gm_series[:12])
                revenue_growth = _first_valid(_abstract_metric(df, "营业总收入增长率"))
                profit_growth = _first_valid(_abstract_metric(df, "归属母公司净利润增长率"))
                cashflow = _first_valid(_abstract_metric(df, "经营现金流量净额"))
                revenue_cagr_3y = _cagr_3y(df, "营业总收入")
                profit_cagr_3y = _cagr_3y(df, "归母净利润")
        except Exception:
            pass

        try:
            pe = _baidu_valuation(code, "市盈率(TTM)")
            pb = _baidu_valuation(code, "市净率")
        except Exception:
            pass

        return FundamentalsRow(
            code=code, pe=pe, pb=pb, roe=roe, debt_ratio=debt_ratio,
            gross_margin=gross_margin, gross_margin_std=gross_margin_std,
            revenue_growth=revenue_growth, profit_growth=profit_growth,
            revenue_cagr_3y=revenue_cagr_3y, profit_cagr_3y=profit_cagr_3y,
            cashflow=cashflow, dividend_yield=dividend_yield,
            dividend_stability=dividend_stability, sector="",
        )

    return retry_call(_fetch)


def fetch_sw_sector_codes(sector_code: str = "801150.SI") -> Set[str]:
    """乐咕乐股申万行业成分页，返回 6 位股票代码集合（默认医药生物）"""
    import re

    import requests

    def _fetch() -> Set[str]:
        resp = requests.get(
            "https://legulegu.com/stockdata/index-composition",
            params={"industryCode": sector_code},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=30,
        )
        resp.raise_for_status()
        return set(re.findall(r'data-stock-code="(\d{6})\.', resp.text))

    return retry_call(_fetch)
