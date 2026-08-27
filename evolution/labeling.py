"""标注：walk-forward 重放历史 pick + 回填真实 daily_picks 的胜负。

胜负判定统一为模拟止盈止损出场（与 spec 一致）：
- 前 MAX_HOLD 个交易日内 low <= stop 先触发 → 记负（同日双触记负，保守）。
- close >= target → 记胜（按 target 价成交）。
- 10 日未触发 → 第 10 日收盘市价出场；outcome_pct > 0 记胜。
- 未来数据不足 MAX_HOLD 日 → 未判定（win=None），等增量补判。

重放按「全策略候选池」记录信号级样本：单笔候选的胜负与配额/去留配置无关，
门禁与归因都基于同一份池子重跑选择，检测只算一次。
"""

import datetime as dt
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from db_repository import open_db, outcomes_watermark
from domain_models import Stock
from market_data import build_market_from_db
from pick_history import _detect_market_regime
from strategies import STRATEGIES
from strategies.adapter import merge_candidates
from strategies.backtest import _snapshot

MAX_HOLD = 10
RR_TARGET = 2.0
MIN_HISTORY = 221   # 与 run_picks 的 build_market_from_db 参数保持一致
MAX_HISTORY = 520


@dataclass
class _Series:
    code: str
    dates: List[str]
    closes: List[float]
    lows: List[float]


def load_series_map(conn, min_days: int = MIN_HISTORY, max_days: int = MAX_HISTORY) -> Dict[str, _Series]:
    """按 build_market_from_db 同样的过滤/截尾规则加载 (date, close, low) 序列。"""
    out: Dict[str, _Series] = {}
    for code, in conn.execute("SELECT DISTINCT code FROM daily_prices ORDER BY code").fetchall():
        rows = conn.execute(
            "SELECT trade_date, close, low FROM daily_prices WHERE code = ? ORDER BY trade_date",
            (code,),
        ).fetchall()
        rows = [r for r in rows if r[1] is not None]
        if len(rows) < min_days:
            continue
        if max_days and len(rows) > max_days:
            rows = rows[-max_days:]
        out[code] = _Series(
            code=code,
            dates=[r[0] for r in rows],
            closes=[float(r[1]) for r in rows],
            lows=[float(r[2]) if r[2] is not None else float(r[1]) for r in rows],
        )
    return out


def judge(entry: float, stop: float, target: float, series: _Series, idx: int,
          max_hold: int = MAX_HOLD) -> Optional[Dict]:
    """从 idx 日起向前找止盈/止损；数据不足 max_hold 日返回 None（未判定）。"""
    if entry <= 0 or stop >= entry:
        return None
    end = idx + max_hold
    if end >= len(series.closes):
        return None
    for j in range(idx + 1, end + 1):
        if series.lows[j] <= stop:
            return {"exit_date": series.dates[j], "exit_price": stop,
                    "outcome_pct": round(stop / entry - 1, 6)}
        if series.closes[j] >= target:
            return {"exit_date": series.dates[j], "exit_price": target,
                    "outcome_pct": round(target / entry - 1, 6)}
    return {"exit_date": series.dates[end], "exit_price": series.closes[end],
            "outcome_pct": round(series.closes[end] / entry - 1, 6)}


def _row(date: str, source: str, code: str, name: str, strategy: str, kind: str,
         score: Optional[float], buy: float, stop: float, target: float,
         judged: Optional[Dict]) -> Dict:
    now = dt.datetime.now().isoformat(timespec="seconds")
    return {
        "date": date, "source": source, "code": code, "strategy": strategy,
        "name": name, "kind": kind, "score": score,
        "buy": round(buy, 4), "stop": round(stop, 4), "target": round(target, 4),
        "exit_date": judged["exit_date"] if judged else None,
        "exit_price": round(judged["exit_price"], 4) if judged else None,
        "outcome_pct": judged["outcome_pct"] if judged else None,
        "win": (1 if judged["outcome_pct"] > 0 else 0) if judged else None,
        "labeled_at": now,
    }


def replay_rows(db_path: str, backfill_days: int = 250, since: str = "",
                progress: Optional[Callable[[int, str], None]] = None) -> List[Dict]:
    """walk-forward 重放：每个历史日跑合并榜单的检测部分，为每个候选记一笔胜负。

    候选池 = 全部策略的 detect 输出（与去留/配额无关），一次标注长期复用。
    增量以已判定水位线为界；尾部不足 MAX_HOLD 日的日期留待下次补判。
    """
    conn = open_db(db_path)
    try:
        series_map = load_series_map(conn)
        stocks = build_market_from_db(db_path, min_days=MIN_HISTORY, max_days=MAX_HISTORY)
        stocks = [s for s in stocks if s.code in series_map]
        if not stocks:
            return []
        if not since:
            since = outcomes_watermark(conn, "replay")
        date_to_idx = {code: {d: i for i, d in enumerate(s.dates)} for code, s in series_map.items()}
        calendar = sorted({d for s in series_map.values() for d in s.dates})
        eligible = [d for d in calendar if d > since] if since else list(calendar)
        eligible = eligible[:-MAX_HOLD]  # 尾部未走完 10 日，判不了
        if not since:
            eligible = eligible[-backfill_days:]
        rows: List[Dict] = []
        total = max(len(eligible), 1)
        for i, day in enumerate(eligible):
            snapshots: List[Stock] = []
            own_idx: Dict[str, int] = {}
            for stock in stocks:
                oi = date_to_idx[stock.code].get(day)
                if oi is None or oi < MIN_HISTORY - 1:
                    continue
                snapshots.append(_snapshot(stock, oi))
                own_idx[stock.code] = oi
            if len(snapshots) < 50:
                continue
            regime = _detect_market_regime(snapshots)
            candidates = merge_candidates(snapshots, regime, set(STRATEGIES))
            for item in candidates:
                stock = item["stock"]
                series = series_map.get(stock.code)
                if series is None:
                    continue
                buy = stock.prices[-1]
                stop = item["stop_price"]
                if stop >= buy:
                    continue
                target = buy + RR_TARGET * (buy - stop)
                judged = judge(buy, stop, target, series, own_idx[stock.code])
                rows.append(_row(day, "replay", stock.code, stock.name,
                                 item["strategy"], "", item.get("score"),
                                 buy, stop, target, judged))
            if progress:
                progress(10 + int(70 * (i + 1) / total), f"重放标注 {day}")
        return rows
    finally:
        conn.close()


def live_rows(db_path: str) -> List[Dict]:
    """回填真实 daily_picks：用榜单存储的 buy/stop/target 向前判胜负。"""
    conn = open_db(db_path)
    try:
        series_map = load_series_map(conn)
        since = outcomes_watermark(conn, "live")
        picks = conn.execute(
            "SELECT date, kind, code, name, strategy, buy, stop, target, score FROM daily_picks "
            "WHERE kind != '高胜率' AND stop > 0 AND buy > 0 AND date > ?",
            (since,),
        ).fetchall()
        rows: List[Dict] = []
        for date, kind, code, name, strategy, buy, stop, target, score in picks:
            series = series_map.get(code)
            if series is None or date not in series.dates:
                continue
            idx = series.dates.index(date)
            judged = judge(float(buy), float(stop), float(target), series, idx)
            rows.append(_row(date, "live", code, name or code, strategy, kind,
                             score, float(buy), float(stop), float(target), judged))
        return rows
    finally:
        conn.close()
