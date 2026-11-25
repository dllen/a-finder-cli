#!/usr/bin/env python3
"""抓取「重点 ETF 资金看板」数据并落成静态 JSON。

来源站点：https://jinke.app.workbuddy.host/?sharecode=BS2PY5RXH6
  - 该站点是 WorkBuddy 发布的静态看板，服务端把全部数据渲染进
    `data.js`（`window.__ISLANDS = {...}`），再由 `app.js` 用 ECharts 画图。
  - 访问需要密码（POST /api/verify 换 `etf_auth` cookie）。

本脚本把站点的数据层**原样抓下来、拆成独立 JSON**，落到 `data/etf/`：

    data/etf/islands.json      主看板数据层（initData/cmpData/typData/
                               dailyData/histData/postHol/broadData/
                               fflowData/cumData/marginData）
    data/etf/themes.json       主题清单（第五部分）
    data/etf/cumdays.json      可对比的历史日期（分钟累计曲线）
    data/etf/theme-cum.json    主题分钟累计基准
    data/etf/leverage.json     两融看板数据层（DASH_DATA）
    data/etf/xueqiu.json       雪球监控数据层（XQ）
    data/etf/privatefund.json  私募基金数据层（PF）
    data/etf/meta.json         抓取时间 / 各数据集 asof

为什么**不**直接把站点的 app.js 抄进本项目：
  站点的 app.js 依赖它自己的 `/api/hq`、`/api/kline`、`/api/ktrends`、
  `/api/idxminute` 四个代理接口（转发东财/新浪）。本项目已有自己的
  数据源（`data_providers.py`）与 Flask 路由，所以本脚本只搬**数据**，
  前端由本项目自己实现，接口走本项目自己的 `/api/etf/*`。

用法：
    python3 scripts/fetch_etf_dashboard.py                  # 抓取并写 data/etf/
    python3 scripts/fetch_etf_dashboard.py --out data/etf   # 指定输出目录
    python3 scripts/fetch_etf_dashboard.py --password xxx    # 指定密码
    python3 scripts/fetch_etf_dashboard.py --dry-run        # 只打印不落盘

退出码：0 成功；1 登录失败；2 网络/解析失败。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

#: 看板根地址
BASE_URL = "https://jinke.app.workbuddy.host"

#: 分享码。WorkBuddy 网关要求**每个请求**都带 `?sharecode=`，否则一律 403
#: （包括 POST /api/verify）。这不是密码，是链接的一部分。
SHARECODE = "BS2PY5RXH6"

#: 默认访问密码（用户 2026-11-24 提供）。**不要**把它当成秘密 ——
#: 它是机构看板的共享口令，仓库里本来就有这个看板的公开数据；
#: 仍可用环境变量 ETF_DASH_PASSWORD 覆盖。
DEFAULT_PASSWORD = "jinke@dongcai"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


class FetchError(RuntimeError):
    """抓取过程中的可预期失败（登录失败 / HTTP 错误 / 解析失败）。"""


def _with_sharecode(url: str) -> str:
    """给 URL 拼上 `sharecode` 查询参数（网关强制要求）。"""
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}sharecode={SHARECODE}"


def _request(url: str, *, data: bytes | None = None, cookie: str = "") -> bytes:
    """发一个 HTTP 请求，返回响应体。

    ⚠️ 必须带自定义 User-Agent：CloudStudio 网关会挡 Python-urllib 的默认 UA。
    """
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if cookie:
        # cookie 参数是 etf_auth 的**值**，要补上 "etf_auth=" 前缀才是合法 Cookie 头
        headers["Cookie"] = f"etf_auth={cookie}"
    req = urllib.request.Request(_with_sharecode(url), data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise FetchError(f"HTTP {exc.code} {url}") from exc
    except urllib.error.URLError as exc:
        raise FetchError(f"网络错误 {url}: {exc.reason}") from exc


def login(password: str) -> str:
    """登录看板，返回 `etf_auth` cookie 的值。

    站点用 POST /api/verify 换 cookie（HttpOnly、Max-Age=86400）。
    """
    body = json.dumps({"password": password}).encode("utf-8")
    req = urllib.request.Request(
        _with_sharecode(f"{BASE_URL}/api/verify"),
        data=body,
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", "replace")
            cookie = ""
            # ⚠️ 网关会发**两个** Set-Cookie（__wb_rsc + etf_auth），
            # headers.get() 只返回第一个 ⇒ 必须用 get_all()。
            for header in resp.headers.get_all("Set-Cookie") or []:
                for part in header.split(";"):
                    if part.strip().startswith("etf_auth="):
                        cookie = part.strip().split("=", 1)[1]
    except urllib.error.HTTPError as exc:
        raise FetchError(f"登录失败：HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise FetchError(f"登录失败：网络错误 {exc.reason}") from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FetchError(f"登录响应不是 JSON：{raw[:120]!r}") from exc
    if not payload.get("ok"):
        raise FetchError("登录失败：密码不正确")
    if not cookie:
        # 有些网关把 cookie 放在别的头里；拿不到就退回用 Authorization 风格无法工作，
        # 直接报错更清楚。
        raise FetchError("登录成功但未拿到 etf_auth cookie")
    return cookie


def fetch_text(cookie: str, path: str) -> str:
    return _request(f"{BASE_URL}{path}", cookie=cookie).decode("utf-8", "replace")


def fetch_json(cookie: str, path: str):
    raw = _request(f"{BASE_URL}{path}", cookie=cookie)
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError as exc:
        raise FetchError(f"{path} 不是合法 JSON") from exc


def parse_js_object(text: str, var_name: str):
    """从 `window.X = {...};` 里抠出 JSON 对象。

    站点的 data.js 不是严格 JSON（键名不带引号、结尾带分号），
    但**值**都是合法 JSON，所以截出赋值号之后的部分、去掉尾部分号再 parse。
    """
    marker = f"{var_name} = "
    idx = text.find(marker)
    if idx < 0:
        raise FetchError(f"未找到 {var_name} 赋值")
    body = text[idx + len(marker):].strip()
    if body.endswith(";"):
        body = body[:-1]
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise FetchError(f"{var_name} 解析失败：{exc}") from exc


def parse_islands(text: str) -> dict:
    """解析主看板的 `window.__ISLANDS = {...}`。

    这个对象的**键不带引号**，且每个键的值各占一行、行尾带逗号，
    末尾还有一个多余的 `}`（闭掉外层对象）再跟 `;`。所以不能整体 parse，
    要逐行按「键: {JSON}」切。
    """
    out: dict = {}
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line or line.startswith("//") or line.startswith("/*"):
            continue
        line = line.rstrip(",").rstrip(";").strip()
        # 去掉闭掉 window.__ISLANDS 的那个多余右括号（只在最后一个键上出现）
        if line.startswith("}") and "}" not in line[:-1]:
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        if not key or not key.replace("_", "").isalnum():
            continue
        value = value.strip()
        # 末行可能多带一个 "}"（闭外层），逐个尝试截断
        for candidate in (value, value[:-1] if value.endswith("}") else None):
            if candidate is None:
                continue
            try:
                out[key] = json.loads(candidate)
                break
            except json.JSONDecodeError:
                continue
    if not out:
        raise FetchError("__ISLANDS 解析后为空")
    return out


def fetch_theme_minutes(cookie: str, themes: dict) -> dict:
    """抓每个主题全部 ETF 的分钟线（成交额 + 收盘价）。

    为什么需要它：第五部分「主题分时」要把主题内所有 ETF 的分钟成交额加总，
    并叠一条价格参照线。原站的做法是：
      ① 主题指数分钟线（东财 / 新浪）—— 但东财指数分钟接口在部署环境不可达，
         实测 /api/idxminute 一律返回 {"error":"upstream unavailable"}；
      ② 兜底用「主题 ETF 等权合成（基期 1000）」。

    本脚本直接抓 ② 需要的原始分钟数据（每只 ETF 的 close + amount），
    让前端自己合成 —— 这样本项目**不依赖**原站那个随时可能挂掉的代理接口。

    返回：{主题key: {代码(带市场前缀): [{day, close, amount}, ...]}}
    """
    out: dict = {}
    for theme in (themes.get("themes") or []):
        key = theme.get("key")
        codes = theme.get("codes") or []
        if not key or not codes:
            continue
        syms = ",".join(_to_symbol(c) for c in codes)
        try:
            payload = fetch_json(cookie, f"/api/ktrends?symbols={syms}&scale=1&datalen=242")
        except FetchError:
            out[key] = {}
            continue
        if not isinstance(payload, dict):
            out[key] = {}
            continue
        # 接口返回 {"sh512480": [{day, close, amount}, ...], ...}
        out[key] = {k: v for k, v in payload.items() if isinstance(v, list)}
    return out


def fetch_index_minutes(cookie: str, themes: dict) -> dict:
    """抓每个主题**指数**的分时线（价格参照线的首选来源）。

    原站的前端是三级兜底：
      ① /api/idxminute（东财指数分钟）—— 首选
      ② /api/kline + theme.sinaSym（新浪指数分钟）—— 次选
      ③ 主题 ETF 等权合成（基期 1000）—— 兜底

    本脚本把 ①② 都抓下来，**由前端按优先级选**，这样「用了哪条线」是
    可观测的（theme-minutes.json 里每条都带 source 字段）。

    ⚠️ 关于 idxminute 的两个实测事实（2026-11-25 修正早先的错误结论）：

      1. **datalen 参数被完全忽略** —— 传 4 / 100 / 242 返回的都是当日全部分时
         （09:30 → 当前时刻）。所以这里不传 datalen。
      2. **对某些 secid 稳定失败** —— 上证指数 000001 在 1./0./2. 三种市场
         前缀下都返回 {"error":"upstream unavailable"}，而 000300 / 000905 /
         399001 都正常 ⇒ 这是**按 secid 的稳定失败**，不是随机抖动。
         所以失败必须能安全回落，不能当异常。

    返回：{主题key: {"idx": {...}|None, "sina": {...}|None, "idxName": str}}
    """
    out: dict = {}
    for theme in (themes.get("themes") or []):
        key = theme.get("key")
        if not key:
            continue
        secid = theme.get("idxSecid") or ""
        sina = theme.get("sinaSym") or ""
        entry: dict = {"idx": None, "sina": None, "idxName": theme.get("idxName") or ""}

        if secid:
            try:
                payload = fetch_json(cookie, f"/api/idxminute?secid={secid}")
                if isinstance(payload, list) and len(payload) > 10:
                    entry["idx"] = payload
            except FetchError:
                entry["idx"] = None

        if sina:
            try:
                payload = fetch_json(cookie, f"/api/kline?symbol={sina}&scale=1&datalen=242")
                if isinstance(payload, list) and len(payload) > 10:
                    entry["sina"] = payload
            except FetchError:
                entry["sina"] = None

        out[key] = entry
    return out

def _to_symbol(code: str) -> str:
    """159915.SZ -> sz159915；512480.SH -> sh512480。"""
    c = str(code)
    if "." in c:
        num, _, mkt = c.partition(".")
        return ("sh" if mkt.upper() == "SH" else "sz") + num
    return c


def write_json(path: Path, payload) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    path.write_text(text, encoding="utf-8")
    return len(text)


def collect(cookie: str) -> dict:
    """抓取全部数据集。"""
    data: dict = {}

    data["islands"] = parse_islands(fetch_text(cookie, "/data.js"))
    data["themes"] = fetch_json(cookie, "/themes.json")
    data["cumdays"] = fetch_json(cookie, "/cumdays.json")
    data["theme_cum"] = fetch_json(cookie, "/themeCum.json")
    data["theme_minutes"] = fetch_theme_minutes(cookie, data["themes"])
    data["theme_indices"] = fetch_index_minutes(cookie, data["themes"])

    for key, path, var_name in (
        ("leverage", "/leverage/data.js", "DASH_DATA"),
        ("xueqiu", "/xueqiu/data.js", "XQ"),
        ("privatefund", "/privatefund/data.js", "PF"),
    ):
        data[key] = parse_js_object(fetch_text(cookie, path), var_name)

    return data


def build_meta(data: dict) -> dict:
    """汇总各数据集的 asof（数据截止日），方便前端显示新鲜度。"""
    islands = data.get("islands", {})
    latest = ((islands.get("cmpData") or {}).get("latest") or {}).get("date")
    margin = islands.get("marginData") or {}
    daily_dates = (islands.get("dailyData") or {}).get("dates") or []
    cum = islands.get("cumData") or {}
    return {
        "fetched_at": None,   # 由调用方填
        "latest": {
            "etf_flow": latest,
            "daily": daily_dates[-1] if daily_dates else None,
            "margin": (margin.get("dates") or [None])[-1],
            "leverage": ((data.get("leverage") or {}).get("index") or {}).get("d", [None])[-1]
            if isinstance((data.get("leverage") or {}).get("index"), dict) else None,
            "xueqiu": (data.get("xueqiu") or {}).get("asof"),
            "privatefund": (data.get("privatefund") or {}).get("asof"),
            "cum_prev": cum.get("prevDate"),
            "theme_index_lines": {
                k: ("idx" if (v or {}).get("idx") else
                    "sina" if (v or {}).get("sina") else "synthetic")
                for k, v in (data.get("theme_indices") or {}).items()
            },
            "margin_built_at": margin.get("builtAt"),
            "cum_built_at": cum.get("builtAt"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="抓取重点 ETF 资金看板数据")
    parser.add_argument("--out", default="data/etf", help="输出目录（默认 data/etf）")
    parser.add_argument("--password", default="", help="访问密码（默认用内置口令）")
    parser.add_argument("--dry-run", action="store_true", help="只抓取不落盘")
    args = parser.parse_args()

    password = args.password or DEFAULT_PASSWORD
    out_dir = Path(args.out)

    try:
        cookie = login(password)
        data = collect(cookie)
    except FetchError as exc:
        print(f"[etf-fetch] 失败：{exc}", file=sys.stderr)
        return 1 if "登录" in str(exc) else 2

    from datetime import datetime

    meta = build_meta(data)
    meta["fetched_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    files = {
        "islands.json": data["islands"],
        "themes.json": data["themes"],
        "cumdays.json": data["cumdays"],
        "theme-cum.json": data["theme_cum"],
        "theme-minutes.json": data["theme_minutes"],
        "theme-indices.json": data["theme_indices"],
        "leverage.json": data["leverage"],
        "xueqiu.json": data["xueqiu"],
        "privatefund.json": data["privatefund"],
        "meta.json": meta,
    }

    if args.dry_run:
        for name, payload in files.items():
            print(f"  {name}: {len(json.dumps(payload, ensure_ascii=False))} bytes")
        return 0

    for name, payload in files.items():
        size = write_json(out_dir / name, payload)
        print(f"[etf-fetch] {name}: {size} bytes")
    print(f"[etf-fetch] 已写入 {out_dir}/（最新数据日 {meta['latest'].get('etf_flow')}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
