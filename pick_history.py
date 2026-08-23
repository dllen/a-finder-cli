import argparse
import datetime as dt
import os
from typing import Callable, Dict, List, Optional

from candidate_rules import ma_strategy_candidates, select_candidates_with_quota
from db_repository import open_db
from market_data import build_market_from_db
from market_regime import RegimeType, detect_regime
from signal_rules import detect_signals
from strategies import STRATEGIES
from strategies.adapter import load_passed_strategies, merge_candidates, merged_strategy_ratios
from strategies.dividend_multi_factor import DividendMultiFactorStrategy
from strategies.pharma_multi_factor import PharmaMultiFactorStrategy
from view_models import BUY_STRATEGY_PRIORITY

DEFAULT_TOP = 10


def latest_trade_date(conn) -> str:
    row = conn.execute("SELECT MAX(trade_date) FROM daily_prices").fetchone()
    return row[0] if row and row[0] else ""


def _detect_market_regime(stocks) -> RegimeType:
    index_prices = [s.prices[-1] for s in stocks]
    if len(index_prices) < 20:
        return RegimeType.SIDEWAYS
    regime = detect_regime(index_prices, {})
    return regime.regime


def build_ma_picks(stocks, top: int, passed_strategies=None, regime=None) -> List[Dict]:
    if passed_strategies:
        regime = regime if regime is not None else _detect_market_regime(stocks)
        candidates = merge_candidates(stocks, regime, set(passed_strategies))
        ratios = merged_strategy_ratios(set(passed_strategies))
        ranked = select_candidates_with_quota(candidates, top, ratios)
    else:
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
    from signal_rules import score_signal_strength
    ranked = []
    for stock in stocks:
        all_signals = detect_signals(stock)
        buys = [s for s in all_signals if s["action"] == "买入"]
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
        signals_dict = {"买入": [s["strategy"] for s in detect_signals(stock) if s["action"] == "买入"],
                        "卖出": [s["strategy"] for s in detect_signals(stock) if s["action"] == "卖出"]}
        score_total = score_signal_strength(stock, signals_dict).total
        picks.append(
            {
                "rank": rank,
                "code": stock.code,
                "name": stock.name,
                "strategy": strategy,
                "buy": round(buy, 2),
                "stop": round(stop, 2),
                "target": round(target, 2),
                "score": round(score_total, 2),
            }
        )
    return picks


def build_signal_strategy_picks(stocks, regime: RegimeType, top: int) -> List[Dict]:
    picks = []
    for name, detect in STRATEGIES.items():
        for stock in stocks:
            signals = detect(stock, regime)
            for sig in signals:
                picks.append({
                    "code": sig.code,
                    "name": stock.name,
                    "strategy": sig.strategy,
                    "buy": round(sig.entry, 2),
                    "stop": round(sig.stop, 2),
                    "target": round(sig.tp, 2),
                    "score": round(sig.score, 4),
                })
    picks.sort(key=lambda x: x["score"], reverse=True)
    return [{"rank": i + 1, **p} for i, p in enumerate(picks[:top])]


def build_multi_factor_picks(stocks, trade_date: str, top: int) -> List[Dict]:
    stock_map = {s.code: s for s in stocks}
    d = dt.date.fromisoformat(trade_date)
    strategies = [DividendMultiFactorStrategy(), PharmaMultiFactorStrategy()]
    picks = []
    for strategy in strategies:
        result = strategy.select(d, stocks)
        for pos in result.positions:
            stock = stock_map.get(pos.code)
            price = stock.prices[-1] if stock and stock.prices else 0.0
            stop = round(price * 0.95, 2) if price else 0.0
            target = round(price + 2 * (price - stop), 2) if price else 0.0
            picks.append({
                "code": pos.code,
                "name": pos.name,
                "strategy": strategy.config.name,
                "buy": round(price, 2),
                "stop": stop,
                "target": target,
                "score": round(pos.score, 4),
            })
    picks.sort(key=lambda x: x["score"], reverse=True)
    return [{"rank": i + 1, **p} for i, p in enumerate(picks[:top])]


def upsert_picks(conn, date: str, kind: str, picks: List[Dict]) -> int:
    now = dt.datetime.now().isoformat(timespec="seconds")
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
            now,
        )
        for p in picks
    ]
    conn.executemany(
        """
        INSERT OR REPLACE INTO daily_picks (date, rank, kind, code, name, strategy, buy, stop, target, score, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def run_picks(db_path: str, top: int, do_sync: bool, trade_date: Optional[str] = None,
              progress: Optional[Callable[[int, str], None]] = None) -> Dict:
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
        return {"date": "", "ma": 0, "buy": 0, "signal": 0, "multi": 0}

    report(94, "计算榜单…")
    passed_strategies = load_passed_strategies()
    conn = open_db(db_path)
    with conn:
        if trade_date:
            # 验证指定日期有行情
            row = conn.execute("SELECT 1 FROM daily_prices WHERE trade_date = ? LIMIT 1", (trade_date,)).fetchone()
            if not row:
                report(100, f"指定日期 {trade_date} 无行情数据")
                return {"date": "", "ma": 0, "buy": 0, "signal": 0, "multi": 0}
            date = trade_date
        else:
            date = latest_trade_date(conn)
            if not date:
                report(100, "无交易日期")
                return {"date": "", "ma": 0, "buy": 0, "signal": 0, "multi": 0}
        ma_count = upsert_picks(conn, date, "均线", build_ma_picks(stocks, top, passed_strategies))
        buy_count = upsert_picks(conn, date, "买入信号", build_buy_picks(stocks, top))
        regime = _detect_market_regime(stocks)
        signal_count = upsert_picks(conn, date, "信号策略", build_signal_strategy_picks(stocks, regime, top))
        multi_count = upsert_picks(conn, date, "多因子", build_multi_factor_picks(stocks, date, top))
    report(100, f"完成：均线 {ma_count} 条 / 买入信号 {buy_count} 条 / 信号策略 {signal_count} 条 / 多因子 {multi_count} 条")
    return {"date": date, "ma": ma_count, "buy": buy_count, "signal": signal_count, "multi": multi_count}


def main() -> None:
    parser = argparse.ArgumentParser(description="选股并落库到 daily_picks")
    parser.add_argument("--db", type=str, default="hs300.db", help="SQLite 文件路径")
    parser.add_argument("--top", type=int, default=DEFAULT_TOP, help="榜单数量")
    parser.add_argument("--no-sync", action="store_true", help="跳过数据同步")
    parser.add_argument("--date", type=str, default=None, help="指定交易日（YYYY-MM-DD）")
    parser.add_argument("--batch", action="store_true", help="批量补齐最近5个交易日")
    args = parser.parse_args()

    if args.batch:
        # 批量补齐最近 5 个交易日（不含今天）
        today = dt.date.today()
        trade_dates = []
        for i in range(1, 20):
            d = today - dt.timedelta(days=i)
            if d.weekday() < 5:
                trade_dates.append(d.isoformat())
            if len(trade_dates) >= 5:
                break
        print(f"批量回填 {len(trade_dates)} 个交易日：{trade_dates}")
        # 只同步一次行情，之后逐日计算
        stocks = build_market_from_db(args.db, min_days=221, max_days=520)
        if not stocks:
            print("无可用行情数据")
            return
        passed_strategies = load_passed_strategies()
        conn = open_db(args.db)
        for td in trade_dates:
            existing = conn.execute(
                "SELECT COUNT(*) FROM daily_picks WHERE date = ?", (td,)
            ).fetchone()[0]
            if existing > 0:
                print(f"  {td}: 已存在 {existing} 条，跳过")
                continue
            row = conn.execute("SELECT 1 FROM daily_prices WHERE trade_date = ? LIMIT 1", (td,)).fetchone()
            if not row:
                print(f"  {td}: 无行情数据，跳过")
                continue
            ma_count = upsert_picks(conn, td, "均线", build_ma_picks(stocks, args.top, passed_strategies))
            buy_count = upsert_picks(conn, td, "买入信号", build_buy_picks(stocks, args.top))
            regime = _detect_market_regime(stocks)
            signal_count = upsert_picks(conn, td, "信号策略", build_signal_strategy_picks(stocks, regime, args.top))
            multi_count = upsert_picks(conn, td, "多因子", build_multi_factor_picks(stocks, td, args.top))
            print(f"  {td}: 均线 {ma_count} 条 / 买入信号 {buy_count} 条 / 信号策略 {signal_count} 条 / 多因子 {multi_count} 条")
        conn.close()
        return

    result = run_picks(args.db, args.top, not args.no_sync, trade_date=args.date)
    print(f"日期: {result['date'] or '无数据'}")
    print(f"均线榜单: {result['ma']} 条")
    print(f"买入信号榜单: {result['buy']} 条")
    print(f"信号策略榜单: {result['signal']} 条")
    print(f"多因子榜单: {result['multi']} 条")


if __name__ == "__main__":
    main()
