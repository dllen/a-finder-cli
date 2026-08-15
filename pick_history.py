import argparse
import datetime as dt
import os
from typing import Dict, List

from candidate_rules import ma_strategy_candidates, select_candidates_with_quota
from db_repository import open_db
from market_data import build_market_from_db
from signal_rules import detect_signals
from view_models import BUY_STRATEGY_PRIORITY

DEFAULT_TOP = 10


def latest_trade_date(conn) -> str:
    row = conn.execute("SELECT MAX(trade_date) FROM daily_prices").fetchone()
    return row[0] if row and row[0] else ""


def build_ma_picks(stocks, top: int) -> List[Dict]:
    candidates = ma_strategy_candidates(stocks)
    ranked = select_candidates_with_quota(candidates, top)
    picks = []
    for rank, item in enumerate(ranked, start=1):
        stock = item["stock"]
        buy = stock.prices[-1]
        stop = item["stop_price"]
        target = buy + 2 * (buy - stop)
        picks.append(
            {
                "rank": rank,
                "code": stock.code,
                "name": stock.name,
                "strategy": item["strategy"],
                "buy": round(buy, 2),
                "stop": round(stop, 2),
                "target": round(target, 2),
                "score": round(item["score"], 4),
            }
        )
    return picks


def build_buy_picks(stocks, top: int) -> List[Dict]:
    ranked = []
    for stock in stocks:
        buys = [s for s in detect_signals(stock) if s["action"] == "买入"]
        if not buys:
            continue
        priority = min(BUY_STRATEGY_PRIORITY.get(s["strategy"], 99) for s in buys)
        primary = min(buys, key=lambda s: BUY_STRATEGY_PRIORITY.get(s["strategy"], 99))
        momentum = (stock.prices[-1] / stock.prices[-20] - 1) * 100 if len(stock.prices) >= 20 else 0.0
        buy = stock.prices[-1]
        ma20 = sum(stock.prices[-20:]) / 20 if len(stock.prices) >= 20 else buy
        stop = min(min(stock.prices[-20:]), ma20 * 0.97)
        if stop >= buy:
            stop = buy * 0.97
        target = buy + 2 * (buy - stop)
        ranked.append((len(buys), -priority, momentum, stock, primary["strategy"], buy, stop, target))
    ranked.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
    picks = []
    for rank, (_, _, _, stock, strategy, buy, stop, target) in enumerate(ranked[:top], start=1):
        picks.append(
            {
                "rank": rank,
                "code": stock.code,
                "name": stock.name,
                "strategy": strategy,
                "buy": round(buy, 2),
                "stop": round(stop, 2),
                "target": round(target, 2),
                "score": None,
            }
        )
    return picks


def upsert_picks(conn, date: str, kind: str, picks: List[Dict]) -> int:
    rows = [
        (
            date,
            p["rank"],
            kind,
            p["code"],
            p["name"],
            p["strategy"],
            p["buy"],
            p["stop"],
            p["target"],
            p["score"],
        )
        for p in picks
    ]
    conn.executemany(
        """
        INSERT OR REPLACE INTO daily_picks (date, rank, kind, code, name, strategy, buy, stop, target, score)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def run_picks(db_path: str, top: int, do_sync: bool) -> Dict:
    if do_sync:
        from sync_service import sync_hs300, sync_hs300_range

        if not (os.path.exists(db_path) and os.path.getsize(db_path) > 0):
            today = dt.date.today()
            start = today - dt.timedelta(days=730)
            sync_hs300_range(
                db_path,
                start.strftime("%Y-%m-%d"),
                today.strftime("%Y-%m-%d"),
                None, 6, 6, 3, 0.6, False, False, False,
            )
        else:
            sync_hs300(db_path, "incremental", None)

    stocks = build_market_from_db(db_path, min_days=221, max_days=520)
    if not stocks:
        return {"date": "", "ma": 0, "buy": 0}

    conn = open_db(db_path)
    with conn:
        date = latest_trade_date(conn)
        if not date:
            return {"date": "", "ma": 0, "buy": 0}
        ma_count = upsert_picks(conn, date, "均线", build_ma_picks(stocks, top))
        buy_count = upsert_picks(conn, date, "买入信号", build_buy_picks(stocks, top))
    return {"date": date, "ma": ma_count, "buy": buy_count}


def main() -> None:
    parser = argparse.ArgumentParser(description="选股并落库到 daily_picks")
    parser.add_argument("--db", type=str, default="hs300.db", help="SQLite 文件路径")
    parser.add_argument("--top", type=int, default=DEFAULT_TOP, help="榜单数量")
    parser.add_argument("--no-sync", action="store_true", help="跳过数据同步")
    args = parser.parse_args()
    result = run_picks(args.db, args.top, not args.no_sync)
    print(f"日期: {result['date'] or '无数据'}")
    print(f"均线榜单: {result['ma']} 条")
    print(f"买入信号榜单: {result['buy']} 条")


if __name__ == "__main__":
    main()
