from typing import Dict, List, Optional

from domain_models import Stock
from stock_strategies import detect_signals, ma_strategy_candidates, primary_signal, select_candidates_with_quota


def build_picks_rows(stocks: List[Stock], scores: Dict[str, float], top: int) -> List[List[str]]:
    ranked = sorted(stocks, key=lambda s: scores[s.code], reverse=True)[:top]
    rows = []
    for stock in ranked:
        signals = detect_signals(stock)
        action, strategy = primary_signal(signals)
        momentum = (stock.prices[-1] / stock.prices[-60] - 1) * 100
        rows.append(
            [
                stock.code,
                stock.name,
                f"{scores[stock.code]:.2f}",
                action,
                strategy or "-",
                f"{stock.prices[-1]:.2f}",
                f"{momentum:.2f}%",
            ]
        )
    return rows


def build_ma_picks_rows(stocks: List[Stock], top: int, strategy_ratios: Dict[str, float] | None = None) -> List[List[str]]:
    candidates = select_candidates_with_quota(ma_strategy_candidates(stocks), top, strategy_ratios)
    signal_priority = {"买入": 0, "卖出": 1, "无信号": 2}
    enriched = []
    rows = []
    for item in candidates:
        stock = item["stock"]
        signals = detect_signals(stock)
        action, signal_strategy = primary_signal(signals)
        enriched.append(
            {
                "item": item,
                "stock": stock,
                "action": action,
                "signal_strategy": signal_strategy,
            }
        )
    enriched.sort(
        key=lambda entry: (
            signal_priority.get(entry["action"], 2),
            -entry["item"]["score"],
        )
    )
    for entry in enriched:
        item = entry["item"]
        stock = entry["stock"]
        rows.append(
            [
                stock.code,
                stock.name,
                item["strategy"],
                entry["action"],
                entry["signal_strategy"] or "-",
                f"{stock.prices[-1]:.2f}",
                f"{item['ma10']:.2f}",
                f"{item['ma30']:.2f}",
                f"{item['ma50']:.2f}",
                f"{item['ma100']:.2f}",
                f"{item['ma200']:.2f}",
                f"{item['volume_ratio']:.2f}",
                f"{item['stop_price']:.2f}",
            ]
        )
    return rows


def build_signals_rows(stocks: List[Stock], code: Optional[str]) -> List[List[str]]:
    rows = []
    target = [s for s in stocks if s.code == code] if code else stocks
    for stock in target:
        signals = detect_signals(stock)
        if not signals:
            rows.append([stock.code, stock.name, "无信号", "-", f"{stock.prices[-1]:.2f}"])
            continue
        for signal in signals:
            rows.append(
                [
                    stock.code,
                    stock.name,
                    signal["action"],
                    signal["strategy"],
                    f"{stock.prices[-1]:.2f}",
                ]
            )
    return rows


BUY_STRATEGY_PRIORITY = {"均线突破": 0, "动量突破": 1, "回调买入": 2, "MACD金叉": 3, "RSI超卖": 4}


def build_buy_signals_rows(stocks: List[Stock], top: int) -> List[List[str]]:
    ranked = []
    for stock in stocks:
        buys = [s for s in detect_signals(stock) if s["action"] == "买入"]
        if not buys:
            continue
        priority = min(BUY_STRATEGY_PRIORITY.get(s["strategy"], 99) for s in buys)
        primary = min(buys, key=lambda s: BUY_STRATEGY_PRIORITY.get(s["strategy"], 99))
        momentum = (stock.prices[-1] / stock.prices[-20] - 1) * 100 if len(stock.prices) >= 20 else 0.0
        ranked.append((len(buys), -priority, momentum, stock, primary["strategy"]))
    ranked.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
    rows = []
    for count, _neg_priority, _momentum, stock, strategy in ranked[:top]:
        price = stock.prices[-1]
        ma20 = sum(stock.prices[-20:]) / 20 if len(stock.prices) >= 20 else price
        stop = min(min(stock.prices[-20:]), ma20 * 0.97)
        if stop >= price:
            stop = price * 0.97
        target = price + 2 * (price - stop)
        rows.append(
            [
                stock.code,
                stock.name,
                f"{count}",
                strategy,
                f"{price:.2f}",
                f"{stop:.2f}",
                f"{target:.2f}",
            ]
        )
    return rows


def build_overview_lines(stocks: List[Stock], scores: Dict[str, float]) -> List[str]:
    top_score = max(scores.values())
    avg_score = sum(scores.values()) / len(scores)
    avg_momentum = sum((s.prices[-1] / s.prices[-60] - 1) * 100 for s in stocks) / len(stocks)
    return [
        f"覆盖股票: {len(stocks)} 只",
        f"最高评分: {top_score:.2f}",
        f"平均评分: {avg_score:.2f}",
        f"平均60日涨幅: {avg_momentum:.2f}%",
    ]
