from typing import Dict, List, Optional

from domain_models import Stock
from stock_strategies import detect_signals, ma_strategy_candidates, primary_signal


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


def build_ma_picks_rows(stocks: List[Stock], top: int) -> List[List[str]]:
    candidates = ma_strategy_candidates(stocks)[:top]
    rows = []
    for item in candidates:
        stock = item["stock"]
        rows.append(
            [
                stock.code,
                stock.name,
                item["strategy"],
                f"{stock.prices[-1]:.2f}",
                f"{item['ma50']:.2f}",
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
