import json
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Optional

from domain_models import Stock
from market_regime import RegimeType
from strategies import STRATEGIES
from strategies.backtest import run_strategy_backtest


def build_report(
    stocks: List[Stock],
    lows_map: Dict[str, List[float]],
    regimes: List[RegimeType],
    max_hold: int = 10,
) -> List[dict]:
    rows = []
    for name, detect in STRATEGIES.items():
        result = run_strategy_backtest(stocks, detect, lows_map, regimes, max_hold, name)
        row = asdict(result)
        # profit_factor 为 inf（0 亏损时）会序列化成非法 JSON（Infinity），落盘前规一化为 None。
        if row["profit_factor"] in (float("inf"), float("-inf")):
            row["profit_factor"] = None
        rows.append(row)
    return rows


def write_report(report: List[dict], out: str) -> None:
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def run_all(
    stocks: List[Stock],
    lows_map: Optional[Dict[str, List[float]]] = None,
    regimes: Optional[List[RegimeType]] = None,
    max_hold: int = 10,
    out: str = "strategies/report.json",
) -> List[dict]:
    lows_map = lows_map or {}
    regimes = regimes or [RegimeType.SIDEWAYS]
    report = build_report(stocks, lows_map, regimes, max_hold)
    write_report(report, out)
    return report
