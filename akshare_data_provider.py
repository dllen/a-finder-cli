from typing import List, Optional

import akshare as ak

from db_repository import PriceRow, StockMeta
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
