#!/usr/bin/env python3
"""从 ETF islands / picks / cb 数据提取每日简报的结构化摘要。

产出 data/briefing-input.json，供 DSH 插件投递给 agent 生成 LLM 叙事，
也供前端在无 LLM 时渲染纯数据兜底版。

用法：
    python3 scripts/generate_briefing_input.py [--db hs300.db] [--out data]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def extract_intraday(islands: dict) -> dict:
    init = islands.get("initData") or {}
    times = init.get("times") or []
    bars = init.get("bars") or []
    cmp_latest = (islands.get("cmpData") or {}).get("latest") or {}

    peak_time, peak_val = "", 0.0
    for t, b in zip(times, bars):
        if b > peak_val:
            peak_val = b
            peak_time = t

    # Hourly aggregation from minute bars
    hourly: dict[str, float] = {}
    for t, b in zip(times, bars):
        hh = int(t[:2])
        hour_key = f"{hh:02d}:00-{hh+1:02d}:00"
        hourly[hour_key] = hourly.get(hour_key, 0.0) + b
    hourly_flow = [{"hour": k, "amount_yi": round(v, 2)} for k, v in sorted(hourly.items())]

    return {
        "peak_minute": {"time": peak_time, "amount_yi": round(peak_val, 2)},
        "hourly_flow": hourly_flow,
        "total_amount_yi": cmp_latest.get("amount", 0),
        "total_netflow_yi": cmp_latest.get("netflow", 0),
    }


def extract_sectors(islands: dict) -> dict:
    bd = islands.get("broadData") or {}
    dates = bd.get("dates") or []
    if not dates:
        return {"top_inflow": [], "top_outflow": []}
    last = len(dates) - 1
    cats = bd.get("cats") or []
    series = bd.get("series") or {}
    items = []
    for c in cats:
        vals = series.get(c) or []
        v = vals[last] if last < len(vals) else 0
        items.append({"name": c, "netflow_yi": round(v, 2)})
    inflow = sorted([x for x in items if x["netflow_yi"] > 0], key=lambda x: -x["netflow_yi"])[:3]
    outflow = sorted([x for x in items if x["netflow_yi"] < 0], key=lambda x: x["netflow_yi"])[:3]
    return {"top_inflow": inflow, "top_outflow": outflow}


def extract_picks(picks_path: Path) -> dict:
    if not picks_path.exists():
        return {"buy_signals": [], "high_winrate": [], "count_by_group": {}}
    try:
        data = json.loads(picks_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"buy_signals": [], "high_winrate": [], "count_by_group": {}}
    groups = data.get("groups") or {}
    buy = groups.get("买入信号") or []
    high = groups.get("高胜率") or []
    counts = {k: len(v) for k, v in groups.items()}
    fields = ("code", "name", "strategy", "day_pct")
    return {
        "buy_signals": [{k: r.get(k) for k in fields} for r in buy[:5]],
        "high_winrate": [{k: r.get(k) for k in fields} for r in high[:3]],
        "count_by_group": counts,
    }


def extract_cb(site_data: Path, date: str) -> dict:
    alerts_path = site_data / "cb-alerts.json"
    picks_path = site_data / f"cb-picks-{date}.json"
    alerts_count = 0
    if alerts_path.exists():
        try:
            alerts = json.loads(alerts_path.read_text(encoding="utf-8"))
            alerts_count = len(alerts.get("alerts") or [])
        except (json.JSONDecodeError, OSError):
            pass
    top_picks = []
    if picks_path.exists():
        try:
            data = json.loads(picks_path.read_text(encoding="utf-8"))
            rows = data.get("picks") or []
            top_picks = [{"code": r.get("code"), "name": r.get("name"), "score": r.get("score")} for r in rows[:3]]
        except (json.JSONDecodeError, OSError):
            pass
    return {"alerts_count": alerts_count, "top_picks": top_picks}


def extract_recent_events(islands: dict, n: int = 5) -> list:
    dd = islands.get("dailyData") or {}
    dates = dd.get("dates") or []
    netflows = dd.get("netflow") or []
    support_dates = {s["date"] for s in (islands.get("cmpData") or {}).get("support") or []}
    events = []
    for i in range(max(0, len(dates) - n), len(dates)):
        d = dates[i]
        nf = netflows[i] if i < len(netflows) else 0
        if d in support_dates:
            events.append({"date": d, "type": "放量日", "detail": f"净流入 {nf:.1f} 亿"})
        elif nf < -100:
            events.append({"date": d, "type": "大幅净赎回", "detail": f"净流出 {abs(nf):.1f} 亿"})
        elif nf > 100:
            events.append({"date": d, "type": "大幅净申购", "detail": f"净流入 {nf:.1f} 亿"})
    return events[-4:]


def build_briefing_input(etf_dir: Path, site_data: Path, db_path: str | None = None) -> dict:
    islands_path = etf_dir / "islands.json"
    if not islands_path.exists():
        raise FileNotFoundError(f"{islands_path} not found")
    islands = json.loads(islands_path.read_text(encoding="utf-8"))

    cmp_latest = (islands.get("cmpData") or {}).get("latest") or {}
    date = cmp_latest.get("date") or ""
    if not date:
        dd = islands.get("dailyData") or {}
        dates = dd.get("dates") or []
        date = dates[-1] if dates else ""

    is_trading_day = True
    if db_path:
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            row = conn.execute("SELECT MAX(trade_date) FROM daily_prices").fetchone()
            conn.close()
            if row and row[0]:
                is_trading_day = (date == row[0])
        except Exception:
            pass

    picks_path = site_data / f"picks-{date}.json"
    return {
        "date": date,
        "is_trading_day": is_trading_day,
        "intraday": extract_intraday(islands),
        "sectors": extract_sectors(islands),
        "picks": extract_picks(picks_path),
        "cb": extract_cb(site_data, date),
        "recent_events": extract_recent_events(islands),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="生成每日简报输入数据")
    ap.add_argument("--db", default="hs300.db")
    ap.add_argument("--out", default="data")
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent
    etf_dir = root / "data" / "etf"
    site_data = root / "site" / "data"
    out_dir = root / args.out

    try:
        result = build_briefing_input(etf_dir, site_data, db_path=str(root / args.db))
    except FileNotFoundError as e:
        print(f"[briefing] 跳过：{e}")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "briefing-input.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"[briefing] {out_path} ({result['date']}, trading={result['is_trading_day']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
