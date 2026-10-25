"""10w 决策体系报告的分析脚本（可复现）。

计算五个榜单（均线/买入信号/信号策略/多因子/高胜率）在
不同持仓周期（5/20/60 交易日）下的「笔胜率 + 平均收益」，
从 daily_picks.buy 价出发，查 daily_prices 后续收盘价。
结果写 docs/reports/data/10w-backtest-baseline.json。

用法: python3 docs/reports/10w_backtest.py [db_path]
"""
import json
import sqlite3
import sys
from pathlib import Path

DB = sys.argv[1] if len(sys.argv) > 1 else "hs300.db"
HOLD_DAYS = [5, 20, 60]  # 短线/波段/中线
KINDS = ["均线", "买入信号", "信号策略", "多因子", "高胜率"]
OUT = Path(__file__).parent / "data" / "10w-backtest-baseline.json"


def load_prices(conn):
    """code -> 按 trade_date 升序的 [(date, close)] 列表。"""
    out = {}
    for code, d, close in conn.execute(
        "SELECT code, trade_date, close FROM daily_prices ORDER BY code, trade_date"
    ):
        out.setdefault(code, []).append((d, close))
    return out


def forward_close(prices, date, n_days):
    """给定 code 的价格序列，返回 date 之后第 n_days 个交易日的收盘价。

    找不到（上市不久 / 已停牌 / 数据不足）返回 None。
    """
    closes_after = [c for (d, c) in prices if d > date]
    if len(closes_after) < n_days:
        return None
    return closes_after[n_days - 1]


def compute(conn):
    prices = load_prices(conn)
    picks = [
        (r[0], r[1], r[2], r[3])
        for r in conn.execute(
            "SELECT kind, code, date, buy FROM daily_picks"
        )
    ]
    results = {}
    for kind in KINDS:
        results[kind] = {}
        for n in HOLD_DAYS:
            rets = []
            for k, code, date, buy in picks:
                if k != kind:
                    continue
                px = prices.get(code)
                if not px or not buy:
                    continue
                exit_close = forward_close(px, date, n)
                if exit_close is None:
                    continue
                rets.append(exit_close / buy - 1.0)
            if not rets:
                results[kind][n] = {"n": 0, "win_rate": None, "avg_ret": None}
                continue
            wins = sum(1 for r in rets if r > 0)
            results[kind][n] = {
                "n": len(rets),
                "win_rate": round(wins / len(rets) * 100, 1),
                "avg_ret": round(sum(rets) / len(rets) * 100, 2),
            }
    return results


def main():
    conn = sqlite3.connect(DB)
    try:
        baseline = compute(conn)
    finally:
        conn.close()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"写成 {OUT}")
    for kind, table in baseline.items():
        line = "  ".join(
            f"{n}日:胜率{table[n]['win_rate']}%/均{table[n]['avg_ret']}%/样本{table[n]['n']}"
            for n in HOLD_DAYS
        )
        print(f"{kind:6} {line}")


if __name__ == "__main__":
    main()
