import json
import re
import urllib.parse
import urllib.request
from typing import Dict, List, Optional

from db_repository import PriceRow, StockMeta
from utils import retry_call


def fetch_url_text(url: str, params: Optional[Dict[str, str]] = None, referer: Optional[str] = None) -> str:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    def request_text() -> str:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Connection": "keep-alive",
        }
        if referer:
            headers["Referer"] = referer
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="ignore")

    return retry_call(request_text)


def fetch_url_json(url: str, params: Optional[Dict[str, str]] = None, referer: Optional[str] = None) -> Dict[str, object]:
    text = fetch_url_text(url, params, referer)
    return json.loads(text)


def fetch_hs300_constituents_from_api() -> Dict[str, str]:
    # columns=ALL：Eastmoney 已下线 SECURITY_NAME_ABBR 等具体字段，只有 ALL 能取到成分股 code
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    params = {
        "reportName": "RPT_INDEX_COMPONENT",
        "columns": "ALL",
        "filter": '(INDEX_CODE="000300")',
        "pageNumber": "1",
        "pageSize": "500",
    }
    data = fetch_url_json(url, params)
    result = data.get("result") if isinstance(data, dict) else None
    pages = result.get("pages") if isinstance(result, dict) else 0
    rows = result.get("data") if isinstance(result, dict) else None
    if not rows:
        return {}
    mapping: Dict[str, str] = {}
    for page in range(1, pages + 1):
        if page > 1:
            params["pageNumber"] = str(page)
            data = fetch_url_json(url, params)
            result = data.get("result") if isinstance(data, dict) else None
            rows = result.get("data") if isinstance(result, dict) else None
            if not rows:
                continue
        for row in rows:
            code = row.get("SECURITY_CODE")
            if code:
                mapping[str(code)] = ""
    return mapping


def fetch_hs300_constituents() -> Dict[str, str]:
    # 名称由后续元数据同步（fetch_stock_meta）或 K 线 data.name 回填，此处只取成分股 code
    return fetch_hs300_constituents_from_api()


def code_to_secid(code: str) -> str:
    if code.startswith(("60", "688")):
        return f"1.{code}"
    return f"0.{code}"


def code_to_tencent_symbol(code: str) -> str:
    prefix = "sh" if code.startswith(("60", "688")) else "sz"
    return f"{prefix}{code}"


def fetch_daily_kline_tencent(code: str, start_date: str, end_date: str) -> tuple[List[PriceRow], str]:
    # 腾讯 qfq 前复权日 K：字段 [日期, 开, 收, 高, 低, 量]，作为 Eastmoney 限流/断连时的稳定备选源
    symbol = code_to_tencent_symbol(code)
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"

    def _dash(date_str: str) -> str:
        # 腾讯要求 YYYY-MM-DD，内部调用传入的是 YYYYMMDD
        if len(date_str) == 8 and date_str.isdigit():
            return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
        return date_str

    params = {"param": f"{symbol},day,{_dash(start_date)},{_dash(end_date)},640,qfq"}
    data = fetch_url_json(url, params)
    payload = (data.get("data") or {}).get(symbol) if isinstance(data.get("data"), dict) else None
    if not isinstance(payload, dict):
        return [], ""
    qt = payload.get("qt", {}).get(symbol) or []
    name = str(qt[1]).strip() if isinstance(qt, list) and len(qt) > 1 else ""
    kline_data = payload.get("qfqday") or payload.get("day") or []
    rows = []
    for entry in kline_data:
        if len(entry) < 6:
            continue
        rows.append(
            PriceRow(
                code=code,
                trade_date=str(entry[0]),
                open_price=float(entry[1]),
                close_price=float(entry[2]),
                high_price=float(entry[3]),
                low_price=float(entry[4]),
                volume=float(entry[5]),
                amount=0.0,
                amplitude=0.0,
                pct_change=0.0,
                change=0.0,
                turnover=0.0,
            )
        )
    return rows, name


def fetch_daily_kline_with_name(code: str, start_date: str, end_date: str) -> tuple[List[PriceRow], str]:
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101",
        "fqt": "0",
        "secid": code_to_secid(code),
        "beg": start_date,
        "end": end_date,
    }
    referer = f"https://quote.eastmoney.com/sz{code}.html" if not code.startswith(("60", "688")) else f"https://quote.eastmoney.com/sh{code}.html"
    try:
        data = fetch_url_json(url, params, referer)
    except Exception:
        return fetch_daily_kline_tencent(code, start_date, end_date)
    payload = data.get("data") if isinstance(data, dict) else None
    kline_data = payload.get("klines") if isinstance(payload, dict) else None
    name = str(payload.get("name") or "").strip() if payload else ""
    if not kline_data:
        return fetch_daily_kline_tencent(code, start_date, end_date)
    rows = []
    for entry in kline_data:
        parts = entry.split(",")
        if len(parts) < 11:
            continue
        rows.append(
            PriceRow(
                code=code,
                trade_date=parts[0],
                open_price=float(parts[1]),
                close_price=float(parts[2]),
                high_price=float(parts[3]),
                low_price=float(parts[4]),
                volume=float(parts[5]),
                amount=float(parts[6]),
                amplitude=float(parts[7]),
                pct_change=float(parts[8]),
                change=float(parts[9]),
                turnover=float(parts[10]),
            )
        )
    return rows, name


def fetch_daily_kline(code: str, start_date: str, end_date: str) -> List[PriceRow]:
    rows, _ = fetch_daily_kline_with_name(code, start_date, end_date)
    return rows


def fetch_stock_meta(code: str) -> Optional[StockMeta]:
    name = _fetch_name_from_sina(code)
    province = _fetch_province_from_eastmoney(code)
    return StockMeta(code=code, name=name or code, industry="", region=province)


def fetch_dividends_5y() -> Dict[str, List[tuple]]:
    """东财分红送配明细，返回 {code: [(除权除息日, 每10股税前派息), ...]}，覆盖近 5 年"""
    import datetime as dt

    end = dt.date.today()
    start = end - dt.timedelta(days=365 * 5)
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    params = {
        "reportName": "RPT_SHAREBONUS_DET",
        "columns": "SECURITY_CODE,EX_DIVIDEND_DATE,PRETAX_BONUS_RMB",
        "filter": f"(EX_DIVIDEND_DATE>='{start}')(EX_DIVIDEND_DATE<='{end}')",
        "sortColumns": "EX_DIVIDEND_DATE",
        "sortTypes": "-1",
        "pageSize": "500",
    }
    result: Dict[str, List[tuple]] = {}
    page = 1
    while True:
        params["pageNumber"] = str(page)
        data = fetch_url_json(url, params)
        payload = data.get("result") if isinstance(data, dict) else None
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not rows:
            break
        for row in rows:
            code = str(row.get("SECURITY_CODE") or "")
            date = str(row.get("EX_DIVIDEND_DATE") or "")[:10]
            bonus = row.get("PRETAX_BONUS_RMB")
            if code and date and bonus:
                result.setdefault(code, []).append((date, float(bonus)))
        pages = payload.get("pages") or 1
        if page >= pages:
            break
        page += 1
    return result


def _fetch_name_from_sina(code: str) -> str:
    try:
        prefix = "sh" if code.startswith(("60", "688")) else "sz"
        url = f"https://hq.sinajs.cn/list={prefix}{code}"
        text = _fetch_sina_text(url)
        match = re.search(r'"([^"]+)"', text)
        if match:
            parts = match.group(1).split(",")
            if parts:
                return parts[0]
    except Exception:
        pass
    return ""


def _fetch_sina_text(url: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Referer": "https://finance.sina.com.cn",
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("gbk", errors="ignore")


def _fetch_province_from_eastmoney(code: str) -> str:
    try:
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        params = {
            "reportName": "RPT_F10_BASIC_ORGINFO",
            "columns": "SECURITY_CODE,PROVINCE",
            "filter": f'(SECURITY_CODE="{code}")',
        }
        data = fetch_url_json(url, params)
        result = data.get("result") if isinstance(data, dict) else None
        rows = result.get("data") if isinstance(result, dict) else None
        if rows and len(rows) > 0:
            province = rows[0].get("PROVINCE", "")
            return str(province).strip() if province else ""
    except Exception:
        pass
    return ""
