import json
import re
import urllib.parse
import urllib.request
from typing import Dict, List, Optional

from db_repository import PriceRow, StockMeta
from utils import retry_call


def fetch_url_text(url: str, params: Optional[Dict[str, str]] = None) -> str:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    def request_text() -> str:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "*/*",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="ignore")

    return retry_call(request_text)


def fetch_url_json(url: str, params: Optional[Dict[str, str]] = None) -> Dict[str, object]:
    text = fetch_url_text(url, params)
    return json.loads(text)


def parse_hs300_constituents(text: str) -> Dict[str, str]:
    pattern = re.compile(r"(?:^|[^\d])((?:00|30|60|68)\d{4})([\u4e00-\u9fa5A-Z]{2,})")
    mapping: Dict[str, str] = {}
    for match in pattern.finditer(text):
        code = match.group(1)
        name = match.group(2)
        if code not in mapping:
            mapping[code] = name
    return mapping


def fetch_hs300_constituents_from_api() -> Dict[str, str]:
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    params = {
        "reportName": "RPT_INDEX_COMPONENT",
        "columns": "SECURITY_CODE,SECURITY_NAME_ABBR,INDEX_CODE",
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
    mapping = {}
    for row in rows:
        code = row.get("SECURITY_CODE")
        name = row.get("SECURITY_NAME_ABBR")
        if code:
            mapping[str(code)] = str(name).strip() if name else ""
    for page in range(2, pages + 1):
        params["pageNumber"] = str(page)
        data = fetch_url_json(url, params)
        result = data.get("result") if isinstance(data, dict) else None
        rows = result.get("data") if isinstance(result, dict) else None
        if not rows:
            continue
        for row in rows:
            code = row.get("SECURITY_CODE")
            name = row.get("SECURITY_NAME_ABBR")
            if code:
                mapping[str(code)] = str(name).strip() if name else ""
    return mapping


def fetch_hs300_constituents() -> Dict[str, str]:
    mapping = fetch_hs300_constituents_from_api()
    if mapping:
        url = "https://data.eastmoney.com/other/index/hs300.html"
        text = fetch_url_text(url)
        html_mapping = parse_hs300_constituents(text)
        for code, name in html_mapping.items():
            if code in mapping and not mapping[code]:
                mapping[code] = name
        return mapping
    url = "https://data.eastmoney.com/other/index/hs300.html"
    text = fetch_url_text(url)
    return parse_hs300_constituents(text)


def code_to_secid(code: str) -> str:
    if code.startswith(("60", "688")):
        return f"1.{code}"
    return f"0.{code}"


def fetch_daily_kline(code: str, start_date: str, end_date: str) -> List[PriceRow]:
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
    data = fetch_url_json(url, params)
    kline_data = data.get("data", {}).get("klines") if isinstance(data.get("data"), dict) else None
    if not kline_data:
        return []
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
    return rows


def fetch_stock_meta(code: str) -> Optional[StockMeta]:
    url = "https://push2.eastmoney.com/api/qt/stock/get"
    params = {
        "secid": code_to_secid(code),
        "fields": "f57,f58,f127,f128",
    }
    data = fetch_url_json(url, params)
    payload = data.get("data") if isinstance(data, dict) else None
    if not isinstance(payload, dict):
        return None
    name = str(payload.get("f58") or "").strip()
    industry = str(payload.get("f127") or "").strip()
    region = str(payload.get("f128") or "").strip()
    return StockMeta(code=code, name=name or code, industry=industry, region=region)
