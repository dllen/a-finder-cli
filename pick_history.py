import argparse
import datetime as dt
import os
from typing import Callable, Dict, List, Optional

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
    if picks:
        return picks
    return _build_ma_fallback(stocks, top)


def _build_ma_fallback(stocks, top: int) -> List[Dict]:
    # 严格多头无命中时兜底：按均线强度评分取前 N，标注为观察榜
    scored = []
    for stock in stocks:
        prices = stock.prices
        if len(prices) < 30:
            continue
        ma10 = sum(prices[-10:]) / 10
        ma30 = sum(prices[-30:]) / 30
        momentum20 = prices[-1] / prices[-20] - 1 if len(prices) >= 20 else 0.0
        score = (prices[-1] / ma30 - 1) * 100 + (prices[-1] / ma10 - 1) * 50 + momentum20 * 200
        buy = prices[-1]
        ma20 = sum(prices[-20:]) / 20 if len(prices) >= 20 else buy
        stop = min(min(prices[-20:]), ma20 * 0.97)
        if stop >= buy:
            stop = buy * 0.97
        target = buy + 2 * (buy - stop)
        scored.append((score, stock, buy, stop, target))
    scored.sort(key=lambda x: x[0], reverse=True)
    picks = []
    for rank, (score, stock, buy, stop, target) in enumerate(scored[:top], start=1):
        picks.append(
            {
                "rank": rank,
                "code": stock.code,
                "name": stock.name,
                "strategy": "均线观察",
                "buy": round(buy, 2),
                "stop": round(stop, 2),
                "target": round(target, 2),
                "score": round(score, 4),
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


def run_picks(db_path: str, top: int, do_sync: bool, progress: Optional[Callable[[int, str], None]] = None) -> Dict:
    def report(pct: int, msg: str) -> None:
        if progress:
            progress(pct, msg)

    if do_sync:
        from sync_service import sync_hs300, sync_hs300_range

        report(2, "开始同步行情…")

        def _sync_progress(pct: int, msg: str) -> None:
            report(int(pct * 0.83), msg)

        if not (os.path.exists(db_path) and os.path.getsize(db_path) > 0):
            today = dt.date.today()
            start = today - dt.timedelta(days=730)
            sync_hs300_range(
                db_path,
                start.strftime("%Y-%m-%d"),
                today.strftime("%Y-%m-%d"),
                None, 6, 6, 3, 0.6, False, False, False,
                _sync_progress,
            )
        else:
            sync_hs300(db_path, "incremental", None, _sync_progress)
        report(85, "行情同步完成")
    else:
        report(5, "跳过行情同步")

    report(88, "加载行情数据…")
    stocks = build_market_from_db(db_path, min_days=221, max_days=520)
    if not stocks:
        report(100, "无可用行情数据")
        return {"date": "", "ma": 0, "buy": 0}

    report(94, "计算榜单…")
    conn = open_db(db_path)
    with conn:
        date = latest_trade_date(conn)
        if not date:
            report(100, "无交易日期")
            return {"date": "", "ma": 0, "buy": 0}
        ma_count = upsert_picks(conn, date, "均线", build_ma_picks(stocks, top))
        buy_count = upsert_picks(conn, date, "买入信号", build_buy_picks(stocks, top))
    report(100, f"完成：均线 {ma_count} 条 / 买入信号 {buy_count} 条")
    return {"date": date, "ma": ma_count, "buy": buy_count}

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
