"""行情数据源接入层。

**复权口径（adjustment basis）为什么必须显式记录**
------------------------------------------------
本模块接入的三个日 K 源，**口径并不一致**：

===========  ==========================  ==================
源            接口参数                    口径
===========  ==========================  ==================
eastmoney    ``push2his`` ``fqt=0``     不复权（none）
sina         ``getKLineData``           不复权（none）
tencent      ``fqkline`` ``qfq``        前复权（qfq）
===========  ==========================  ==================

2026-10-29/30 事故（复盘见 ``docs/reports/ci-cache-fix-plan.md`` 的 F2/F3 与 A5）：
东财被限流时，备源链**静默**退到腾讯 qfq，把**前复权**价格写进一张
**不复权**的 ``daily_prices`` 表。除权除息日这一天，两套口径的价差会在序列里
留下一个**假跳空**，进而污染 MA / 动量 / 回测窗口以及当天的交易计划。
``scripts/repair_gaps.py`` 之所以把自动修复窗卡死在 7 天，正是因为它担心
「更早的复权口径可能已变，填历史会污染序列」。

所以本模块的公开接口遵循两条原则：

1. **口径是数据的一部分**，不是数据源的私有属性 —— 通过
   :data:`SOURCE_BASIS` 暴露给调用方，随行情一起落到库里。
2. **优先用不复权源** —— :data:`PREFERRED_BASIS` 是目标口径；只有在**完全没有**
   不复权源可用时，才允许退到其他口径，并且**必须把用到的口径回报给调用方**。

.. note::
   另有一个事实必须记住：**2026-10-29 只有东财有**（腾讯 qfqday 与新浪当时都只到
   10-28）。「退到腾讯」并不是对最近交易日的补救，它只会**静默少一天**。

**公开名字（供 ``sync_service.py`` 与测试引用）**
------------------------------------------------
- :data:`SOURCE_BASIS` —— 源名 → 复权口径的登记表。
- :data:`PREFERRED_BASIS` —— 目标口径常量（当前为 ``"none"``，即不复权）。
- :data:`BASIS_NONE` / :data:`BASIS_QFQ` —— 口径字面量常量，避免各处硬编码字符串。
- :func:`source_basis` —— 查某个源的口径（未知源返回 ``None``）。
- :func:`fetch_daily_kline_smart` —— 口径感知的备源链，返回 ``(rows, name, source, basis)``。

.. warning::
   ``akshare_data_provider.fetch_daily_kline_akshare`` 用的是 ``adjust="qfq"``，
   **也是前复权**，但它不在本模块的备源链里、目前也**没有任何调用方**。
   如果哪天要把它接进链路，**必须**给它的 ``PriceRow`` 显式标上
   ``adj_basis="qfq"``（``PriceRow`` 的默认值是 ``"none"``，直接用默认值会
   把前复权数据**错标**成不复权 —— 那比不标记更坏）。
"""

import json
import re
import threading
import urllib.parse
import urllib.request
from typing import Dict, List, Optional

from db_repository import PriceRow, StockMeta
from errors import FetchError
from utils import retry_call

# ---------------------------------------------------------------------------
# 复权口径注册表
# ---------------------------------------------------------------------------

#: 口径字面量：不复权（原始成交价，除权日自然跳空）。
BASIS_NONE = "none"
#: 口径字面量：前复权（以最新价为基准向前调整历史价）。
BASIS_QFQ = "qfq"

#: 目标口径 —— 不复权。理由见模块 docstring 的「复权口径」一节。
PREFERRED_BASIS = BASIS_NONE

#: 源名 → 复权口径。**这是口径的唯一真相源**，新增源时必须同步登记，
#: 否则 :func:`source_basis` 返回 ``None``，:func:`fetch_daily_kline_smart`
#: 会把它当作「非首选口径」处理 —— 宁可漏抓也不错写。
#:
#: 注意 ``akshare``（``stock_zh_a_hist(adjust="qfq")``）**没有登记在这里** ——
#: 它是前复权，但不在本模块的备源链里。哪天要接进来，必须同时登记成
#: :data:`BASIS_QFQ`（见模块 docstring 的 warning）。
SOURCE_BASIS: Dict[str, str] = {
    "eastmoney": BASIS_NONE,   # push2his fqt=0
    "sina": BASIS_NONE,        # getKLineData
    "tencent": BASIS_QFQ,      # fqkline qfq
}


def source_basis(source: str) -> Optional[str]:
    """查某个源的复权口径。

    未登记的源返回 ``None`` —— 调用方必须把它当成「**非首选口径**」处理。
    宁可少抓一天，也不要把口径不明的价格混进不复权的序列里。

    :param source: 源名，如 ``"eastmoney"`` / ``"sina"`` / ``"tencent"``
    :return: 口径字面量（``"none"`` / ``"qfq"``），未登记时为 ``None``
    """
    return SOURCE_BASIS.get(source)


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
        with urllib.request.urlopen(req, timeout=10) as resp:
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


def fetch_daily_kline_sina(code: str, start_date: str, end_date: str) -> tuple[List[PriceRow], str]:
    # 新浪不复权日 K：字段 {day, open, high, low, close, volume}，成交量单位「股」→ /100 转「手」。
    # 作为东财 push2his / 腾讯 fqkline 同时限流时的备源（不复权，与东财 fqt=0 口径一致）。
    # 注意：该接口只认 datalen（最近 N 根），不认 start/end，故按 start 距今自然日估算覆盖量后过滤。
    import datetime as dt
    symbol = code_to_tencent_symbol(code)  # sh/sz 前缀，新浪同款
    url = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"

    def _parse(s: str) -> dt.date:
        if len(s) == 8 and s.isdigit():
            return dt.date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        return dt.datetime.strptime(s, "%Y-%m-%d").date()

    start = _parse(start_date)
    end = _parse(end_date)
    days_back = (dt.date.today() - start).days
    datalen = min(2000, max(30, int(days_back * 0.72) + 30))

    params = {"symbol": symbol, "scale": "240", "ma": "no", "datalen": str(datalen)}
    data = fetch_url_json(url, params, referer="https://finance.sina.com.cn")
    if not isinstance(data, list):
        return [], ""
    rows = []
    for entry in data:
        day = str(entry.get("day") or "")
        try:
            d = dt.date.fromisoformat(day)
        except ValueError:
            continue
        if start <= d <= end:
            rows.append(
                PriceRow(
                    code=code,
                    trade_date=day,
                    open_price=float(entry.get("open") or 0),
                    close_price=float(entry.get("close") or 0),
                    high_price=float(entry.get("high") or 0),
                    low_price=float(entry.get("low") or 0),
                    volume=float(entry.get("volume") or 0) / 100.0,
                    amount=0.0,
                    amplitude=0.0,
                    pct_change=0.0,
                    change=0.0,
                    turnover=0.0,
                )
            )
    return rows, ""


def _fetch_daily_kline_fallback(code: str, start_date: str, end_date: str) -> tuple[List[PriceRow], str]:
    # 东财主源失败后的备源链：新浪不复权 → 腾讯 qfq 前复权。
    for fetcher in (fetch_daily_kline_sina, fetch_daily_kline_tencent):
        try:
            rows, name = fetcher(code, start_date, end_date)
            if rows:
                return rows, name
        except Exception:
            continue
    return [], ""


def fetch_daily_kline_eastmoney(code: str, start_date: str, end_date: str) -> tuple[List[PriceRow], str]:
    """东财直连（不走 fallback）。失败时抛异常，由调用方决定降级策略。

    与 fetch_daily_kline_with_name 的区别：后者失败会静默切到新浪/腾讯，
    导致健康探测无法区分"东财可用"和"东财挂了但被备源救回"。
    """
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
    data = fetch_url_json(url, params, referer)
    payload = data.get("data") if isinstance(data, dict) else None
    kline_data = payload.get("klines") if isinstance(payload, dict) else None
    if not kline_data:
        raise FetchError(f"东财无数据: {code} {start_date}~{end_date}")
    name = str(payload.get("name") or "").strip() if payload else ""
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
        return _fetch_daily_kline_fallback(code, start_date, end_date)
    payload = data.get("data") if isinstance(data, dict) else None
    kline_data = payload.get("klines") if isinstance(payload, dict) else None
    name = str(payload.get("name") or "").strip() if payload else ""
    if not kline_data:
        return _fetch_daily_kline_fallback(code, start_date, end_date)
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
    with urllib.request.urlopen(req, timeout=10) as resp:
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


class SourceHealthTracker:
    """跟踪各数据源的可用性，动态调整优先级。

    同步开始前用探针股票测试各源（probe），同步过程中持续记录
    失败次数（record_failure），超过阈值自动降级到列表末尾。
    """

    SOURCES = ["eastmoney", "sina", "tencent"]
    PROBE_CODES = ["600519"]  # 贵州茅台：流动性好、不会退市
    MAX_CONSECUTIVE_FAILURES = 3

    def __init__(self) -> None:
        self._health: Dict[str, Dict] = {
            name: {"available": True, "fail_count": 0}
            for name in self.SOURCES
        }
        self._ordered: List[str] = list(self.SOURCES)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # probe
    # ------------------------------------------------------------------

    def probe(self) -> None:
        """同步开始前，用探针股票测试各源，建立初始优先级。"""
        results: Dict[str, bool] = {}
        for source in self.SOURCES:
            try:
                rows = self._try_source(
                    source, self.PROBE_CODES[0], "20261001", "20261010"
                )
                results[source] = bool(rows)
            except Exception:
                results[source] = False

        available = [s for s in self.SOURCES if results.get(s)]
        unavailable = [s for s in self.SOURCES if not results.get(s)]

        with self._lock:
            self._ordered = available + unavailable
            for s in available:
                self._health[s]["available"] = True
                self._health[s]["fail_count"] = 0
            for s in unavailable:
                self._health[s]["available"] = False
                self._health[s]["fail_count"] = 0

    # ------------------------------------------------------------------
    # query
    # ------------------------------------------------------------------

    def get_ordered_sources(self) -> List[str]:
        """返回当前优先级排序的源列表（副本）。"""
        with self._lock:
            return self._ordered.copy()

    def is_available(self, source: str) -> bool:
        """查询某源当前是否可用。"""
        with self._lock:
            return self._health.get(source, {}).get("available", False)

    # ------------------------------------------------------------------
    # mutation
    # ------------------------------------------------------------------

    def record_success(self, source: str) -> None:
        """记录成功，重置 fail_count。"""
        with self._lock:
            if source in self._health:
                self._health[source]["fail_count"] = 0
                self._health[source]["available"] = True

    def record_failure(self, source: str) -> None:
        """记录失败；连续失败超阈值则降级到列表末尾。"""
        with self._lock:
            if source not in self._health:
                return
            self._health[source]["fail_count"] += 1
            if (
                self._health[source]["fail_count"]
                >= self.MAX_CONSECUTIVE_FAILURES
            ):
                self._health[source]["available"] = False
                if source in self._ordered:
                    self._ordered.remove(source)
                    self._ordered.append(source)

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    def _try_source(
        self, source: str, code: str, start: str, end: str
    ) -> List[PriceRow]:
        """用指定源获取数据，返回行列表（空=失败）。

        用各源的直连接口（不走 fallback），否则探测无法区分
        "东财可用"和"东财挂了但被备源救回"。
        """
        if source == "eastmoney":
            rows, _ = fetch_daily_kline_eastmoney(code, start, end)
        elif source == "sina":
            rows, _ = fetch_daily_kline_sina(code, start, end)
        elif source == "tencent":
            rows, _ = fetch_daily_kline_tencent(code, start, end)
        else:
            return []
        return rows


def fetch_daily_kline_smart(
    code: str,
    start_date: str,
    end_date: str,
    tracker: SourceHealthTracker,
) -> tuple:
    """按 tracker 优先级依次尝试各数据源，**并保证口径优先**。

    返回 ``(rows, name, source, basis)``：

    - ``rows``  —— 行情行（``List[PriceRow]``），全源失败时为空列表。
    - ``name``  —— 股票名称（腾讯/东财会带名字，新浪不带）。
    - ``source``—— 胜出的源名（``"eastmoney"`` / ``"sina"`` / ``"tencent"``），
      全源失败时为空串。
    - ``basis`` —— 胜出源使用的**复权口径**（``"none"`` / ``"qfq"``），
      全源失败时为空串。调用方**必须**把它落库（``daily_prices.adj_basis``），
      否则混口径的事故就又变成看不见的了。

    **口径策略**（2026-10-29/30 事故的修复，见模块 docstring）：

    1. 先跑完**全部首选口径源**（``PREFERRED_BASIS``，当前是东财+新浪的不复权）。
    2. 只有在**一个首选口径源都拿不到**时，才退到非首选口径源（腾讯 qfq）。
       这是「宁可少抓一天，也不混口径」与「还是要拿到数据」之间的折中 ——
       退到 qfq 时**必须**把口径回报出去，让调用方能记录、能报警。
    3. 首选口径组**内部**仍然是「第一个返回非空行的源胜出」，健康探针给出的
       优先级顺序不变（东财 > 新浪 > 腾讯）。

    ⚠️ 仍然要记住：**2026-10-29 只有东财有**（腾讯 qfqday / 新浪当时都只到
    10-28）。退到 qfq 拿到的不是最近那一天，而是一份**缺一天**的前复权序列。

    每次成功/失败都反馈给 tracker（口径策略不改变健康度记账）。
    """
    ordered = [
        s for s in tracker.get_ordered_sources() if tracker.is_available(s)
    ]

    # 先跑首选口径组；一个都拿不到才退到其它口径的源。
    preferred = [s for s in ordered if source_basis(s) == PREFERRED_BASIS]
    others = [s for s in ordered if source_basis(s) != PREFERRED_BASIS]

    for group in (preferred, others):
        for source in group:
            try:
                if source == "eastmoney":
                    rows, name = fetch_daily_kline_eastmoney(code, start_date, end_date)
                elif source == "sina":
                    rows, name = fetch_daily_kline_sina(code, start_date, end_date)
                elif source == "tencent":
                    rows, name = fetch_daily_kline_tencent(code, start_date, end_date)
                else:
                    continue

                if rows:
                    tracker.record_success(source)
                    basis = source_basis(source) or ""
                    # 把口径**盖到每一行上** —— 这一步不能省。
                    #
                    # 为什么不省：PriceRow.adj_basis 的默认值是 "none"（不复权）。
                    # 胜出源若是腾讯（前复权）而我们不覆盖这个默认值，写进库的
                    # 就是「前复权价格被标成不复权」—— 比不写这一列更坏：
                    # **看起来**检测过了（全是 none、没有混口径告警），
                    # 实际数据已经混了口径。宁可让缺列直接报错。
                    for row in rows:
                        row.adj_basis = basis
                    return rows, name, source, basis
                else:
                    tracker.record_failure(source)
            except Exception:
                tracker.record_failure(source)

    return [], "", "", ""
