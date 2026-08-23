import random
from typing import Dict, List

from data_providers import fetch_hs300_constituents
from db_repository import get_fundamentals, open_db, upsert_constituents

from domain_models import Stock


def _volatility(prices: List[float], window: int = 120) -> float:
    seg = prices[-(window + 1):]
    if len(seg) < 3:
        return 0.0
    rets = [seg[i] / seg[i - 1] - 1 for i in range(1, len(seg)) if seg[i - 1] > 0]
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    return (sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)) ** 0.5


def _max_drawdown(prices: List[float], window: int = 250) -> float:
    seg = prices[-window:]
    peak = 0.0
    mdd = 0.0
    for p in seg:
        peak = max(peak, p)
        if peak > 0:
            mdd = max(mdd, (peak - p) / peak)
    return mdd


def _momentum_12m_1m(prices: List[float]) -> float:
    # 12m-1m 动量：剔除最近 22 个交易日；数据不足时用可用窗口
    if len(prices) < 45:
        return 0.0
    end = prices[-22]
    start = prices[-250] if len(prices) >= 250 else prices[0]
    return end / start - 1 if start > 0 else 0.0


def generate_series(seed: int, start: float, trend: float, volatility: float, days: int) -> List[float]:
    rng = random.Random(seed)
    prices = [start]
    for _ in range(1, days):
        drift = trend / days
        shock = rng.uniform(-volatility, volatility)
        price = max(1.0, prices[-1] * (1 + drift + shock))
        prices.append(round(price, 2))
    return prices


def generate_volumes(seed: int, start: int, volatility: float, days: int) -> List[int]:
    rng = random.Random(seed)
    volumes = [start]
    for _ in range(1, days):
        change = rng.uniform(-volatility, volatility)
        volume = max(1000, int(volumes[-1] * (1 + change)))
        volumes.append(volume)
    return volumes


def build_market() -> List[Stock]:
    configs = [
        ("000001", "平安银行", 8.2, 0.9, 0.7, 0.12, 0.09, 0.13, 0.10, 22.0, 0.16, 0.03, 120),
        ("000333", "美的集团", 14.5, 2.8, 1.1, 0.18, 0.16, 0.19, 0.15, 58.0, 0.12, 0.025, 130),
        ("000858", "五粮液", 21.2, 4.8, 1.4, 0.17, 0.14, 0.23, 0.19, 190.0, 0.10, 0.02, 140),
        ("001979", "招商蛇口", 9.6, 1.3, 0.9, 0.11, 0.08, 0.12, 0.09, 16.0, 0.14, 0.035, 150),
        ("300750", "宁德时代", 29.8, 6.1, 1.7, 0.35, 0.30, 0.21, 0.18, 165.0, 0.20, 0.05, 160),
        ("600036", "招商银行", 7.9, 0.8, 0.6, 0.10, 0.08, 0.15, 0.12, 35.0, 0.15, 0.03, 170),
        ("600519", "贵州茅台", 27.4, 9.2, 1.6, 0.14, 0.12, 0.28, 0.24, 1650.0, 0.08, 0.018, 180),
        ("600887", "伊利股份", 18.3, 3.2, 1.2, 0.13, 0.11, 0.17, 0.14, 28.0, 0.11, 0.02, 190),
        ("601318", "中国平安", 10.2, 1.2, 0.9, 0.12, 0.10, 0.14, 0.11, 48.0, 0.13, 0.03, 200),
        ("601888", "中国中免", 24.6, 5.0, 1.5, 0.21, 0.19, 0.18, 0.16, 78.0, 0.17, 0.04, 210),
        ("603259", "药明康德", 23.1, 4.1, 1.3, 0.26, 0.22, 0.20, 0.17, 72.0, 0.18, 0.045, 220),
        ("688981", "中芯国际", 33.5, 4.9, 1.9, 0.28, 0.24, 0.09, 0.07, 42.0, 0.25, 0.06, 230),
    ]
    market = []
    for code, name, pe, pb, peg, rev, prof, roe, cash, start, trend, vol, seed in configs:
        prices = generate_series(seed, start, trend, vol, 240)
        volumes = generate_volumes(seed + 7, 300000, 0.25, 240)
        market.append(
            Stock(
                code=code,
                name=name,
                pe=pe,
                pb=pb,
                peg=peg,
                revenue_growth=rev,
                profit_growth=prof,
                roe=roe,
                cashflow=cash,
                prices=prices,
                volumes=volumes,
                turnover=[0.0] * len(prices),
                amount=[0.0] * len(prices),
                pct_change=[0.0] * len(prices),
            )
        )
    return market


def build_market_from_db(db_path: str, min_days: int = 60, max_days: int = 240) -> List[Stock]:
    conn = open_db(db_path)
    with conn:
        cur = conn.execute("SELECT code, name FROM hs300_metadata ORDER BY code")
        rows = cur.fetchall()
        if rows:
            codes = [row[0] for row in rows]
            names: Dict[str, str] = {row[0]: row[1] if row[1] else row[0] for row in rows}
            missing = [row[0] for row in rows if not row[1]]
            if missing:
                cur = conn.execute(
                    f"SELECT code, name FROM hs300_constituents WHERE code IN ({','.join(['?'] * len(missing))})",
                    missing,
                )
                update = {row[0]: row[1] for row in cur.fetchall() if row[1]}
                if len(update) < len(missing):
                    mapping = fetch_hs300_constituents()
                    fallback = {code: mapping.get(code, "") for code in missing if mapping.get(code)}
                    if fallback:
                        upsert_constituents(conn, fallback)
                        update.update(fallback)
                if update:
                    names.update(update)
        else:
            cur = conn.execute("SELECT code, name FROM hs300_constituents ORDER BY code")
            rows = cur.fetchall()
            if rows:
                codes = [row[0] for row in rows]
                names = {row[0]: row[1] if row[1] else row[0] for row in rows}
            else:
                cur = conn.execute("SELECT DISTINCT code FROM daily_prices ORDER BY code")
                codes = [row[0] for row in cur.fetchall()]
                names = {code: code for code in codes}
        market: List[Stock] = []
        fundamentals = get_fundamentals(conn)
        for code in codes:
            cur = conn.execute(
                "SELECT close, volume, turnover, amount, pct_change FROM daily_prices "
                "WHERE code = ? ORDER BY trade_date",
                (code,),
            )
            series = [tuple(item) for item in cur.fetchall() if item[0] is not None]
            if len(series) < min_days:
                continue
            if max_days and len(series) > max_days:
                series = series[-max_days:]
            prices = [float(item[0]) for item in series]
            volumes = [int(item[1]) if item[1] is not None else 0 for item in series]
            turnover = [float(item[2]) if item[2] is not None else 0.0 for item in series]
            amount = [float(item[3]) if item[3] is not None else 0.0 for item in series]
            pct_change = [float(item[4]) if item[4] is not None else 0.0 for item in series]
            name = names.get(code) or code
            f = fundamentals.get(code)
            market.append(
                Stock(
                    code=code,
                    name=name,
                    pe=f.pe if f else 0.0,
                    pb=f.pb if f else 0.0,
                    peg=1.0,
                    revenue_growth=f.revenue_growth if f else 0.0,
                    profit_growth=f.profit_growth if f else 0.0,
                    roe=f.roe if f else 0.0,
                    cashflow=f.cashflow if f else 0.0,
                    prices=prices,
                    volumes=volumes,
                    turnover=turnover,
                    amount=amount,
                    pct_change=pct_change,
                    sector=f.sector if f else "",
                    debt_ratio=f.debt_ratio if f else 0.0,
                    gross_margin=f.gross_margin if f else 0.0,
                    gross_margin_std=f.gross_margin_std if f else 0.0,
                    revenue_cagr_3y=f.revenue_cagr_3y if f else 0.0,
                    profit_cagr_3y=f.profit_cagr_3y if f else 0.0,
                    dividend_yield=f.dividend_yield if f else 0.0,
                    dividend_stability=f.dividend_stability if f else 0.0,
                    volatility_120d=_volatility(prices),
                    max_drawdown_1y=_max_drawdown(prices),
                    momentum_12m_1m=_momentum_12m_1m(prices),
                )
            )
        # 超额动量 = 个股动量 - 全市场均值
        if market:
            avg_mom = sum(s.momentum_12m_1m for s in market) / len(market)
            for s in market:
                s.excess_momentum = s.momentum_12m_1m - avg_mom
        return market
