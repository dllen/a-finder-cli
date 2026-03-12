import argparse
import os
from dataclasses import replace
from typing import Dict, List

from candidate_rules import DEFAULT_STRATEGY_RATIOS, ma_strategy_candidates, select_candidates_with_quota
from db_repository import open_db
from domain_models import Stock
from formatter import format_table
from market_data import build_market, build_market_from_db
from view_models import build_ma_picks_rows


def load_stocks(db_path: str) -> List[Stock]:
    if db_path and os.path.exists(db_path):
        stocks = build_market_from_db(db_path, min_days=221, max_days=520)
        if stocks:
            return stocks
    return build_market()


def truncate_stocks(stocks: List[Stock], length: int) -> List[Stock]:
    return [replace(stock, prices=stock.prices[-length:], volumes=stock.volumes[-length:]) for stock in stocks]


def load_daily_lows(stocks: List[Stock], db_path: str) -> Dict[str, List[float]]:
    fallback = {stock.code: [price * 0.992 for price in stock.prices] for stock in stocks}
    if not db_path or not os.path.exists(db_path):
        return fallback
    lows_map: Dict[str, List[float]] = {}
    conn = open_db(db_path)
    with conn:
        for stock in stocks:
            cur = conn.execute(
                "SELECT close, low FROM daily_prices WHERE code = ? ORDER BY trade_date",
                (stock.code,),
            )
            rows = [(item[0], item[1]) for item in cur.fetchall() if item[0] is not None]
            if not rows:
                lows_map[stock.code] = fallback[stock.code]
                continue
            if len(rows) > len(stock.prices):
                rows = rows[-len(stock.prices) :]
            series = [float(low) if low is not None else float(close) for close, low in rows]
            if len(series) < len(stock.prices):
                gap = len(stock.prices) - len(series)
                series = fallback[stock.code][:gap] + series
            lows_map[stock.code] = series
    return lows_map


def default_backtest_config() -> Dict[str, float]:
    return {
        "regime_base": 0.75,
        "regime_breadth_weight": 0.2,
        "regime_floor": 0.7,
        "weak_cap": 0.92,
        "ma30_stop_multiplier": 0.985,
        "price_floor_multiplier": 0.97,
        "stop_cap_multiplier": 0.995,
        "close_confirm_buffer": 1.005,
    }


def parse_quota_ratios(raw: str) -> Dict[str, float]:
    parts = [part.strip() for part in raw.split(",")]
    if len(parts) != 3:
        raise ValueError("quota 需要三个逗号分隔值，例如 4,3,3")
    values = []
    for part in parts:
        if not part:
            raise ValueError("quota 存在空值")
        value = float(part)
        if value <= 0:
            raise ValueError("quota 的每一项必须大于 0")
        values.append(value)
    total = sum(values)
    return {
        "多均线突破": values[0] / total,
        "多均线回踩": values[1] / total,
        "多均线趋势": values[2] / total,
    }


def empty_result() -> Dict[str, float]:
    return {
        "backtest_days": 0.0,
        "position_days": 0.0,
        "position_ratio": 0.0,
        "total_return": 0.0,
        "annualized_return": 0.0,
        "benchmark_return": 0.0,
        "excess_return": 0.0,
        "win_rate": 0.0,
        "avg_daily_return": 0.0,
        "avg_daily_picks": 0.0,
        "avg_exposure": 0.0,
        "stop_hits": 0.0,
        "market_weak_days": 0.0,
    }


def market_regime_factor(snapshot: List[Stock], config: Dict[str, float]) -> tuple[float, bool]:
    breadth_count = 0
    short_momentum_sum = 0.0
    long_trend_sum = 0.0
    for stock in snapshot:
        price = stock.prices[-1]
        ma100 = sum(stock.prices[-100:]) / 100
        ma30 = sum(stock.prices[-30:]) / 30
        if price > ma100:
            breadth_count += 1
        short_momentum_sum += price / stock.prices[-5] - 1
        long_trend_sum += ma30 / ma100 - 1
    total = len(snapshot)
    breadth = breadth_count / total
    short_momentum = short_momentum_sum / total
    long_trend = long_trend_sum / total
    short_term_component = max(-0.02, min(0.02, short_momentum)) * 5
    long_term_component = max(-0.03, min(0.03, long_trend)) * 3
    regime = config["regime_base"] + breadth * config["regime_breadth_weight"] + short_term_component + long_term_component
    weak_market = breadth < 0.42 or (short_momentum < -0.004 and long_trend < 0)
    if weak_market:
        regime = min(regime, config["weak_cap"])
    return max(config["regime_floor"], min(1.0, regime)), weak_market


def candidate_return_with_stop(
    current_price: float,
    next_price: float,
    next_low_price: float,
    stop_price: float,
    ma30: float,
    config: Dict[str, float],
) -> tuple[float, bool]:
    layered_stop = max(stop_price, ma30 * config["ma30_stop_multiplier"], current_price * config["price_floor_multiplier"])
    layered_stop = min(layered_stop, current_price * config["stop_cap_multiplier"])
    raw_return = next_price / current_price - 1
    stop_return = layered_stop / current_price - 1
    stop_confirmed = next_low_price <= layered_stop and next_price <= layered_stop * config["close_confirm_buffer"]
    if stop_confirmed and stop_return < raw_return:
        return stop_return, True
    return raw_return, False


def run_backtest(
    stocks: List[Stock],
    lows_map: Dict[str, List[float]],
    top: int,
    backtest_days: int,
    config: Dict[str, float] | None = None,
    end_shift_days: int = 0,
    strategy_ratios: Dict[str, float] | None = None,
) -> Dict[str, float]:
    config = config or default_backtest_config()
    valid_stocks = [stock for stock in stocks if len(stock.prices) >= 221 and len(stock.prices) == len(stock.volumes)]
    if not valid_stocks:
        return empty_result()
    aligned_len = min(len(stock.prices) for stock in valid_stocks)
    aligned_stocks = truncate_stocks(valid_stocks, aligned_len)
    available_days = aligned_len - 221 + 1
    actual_days = min(backtest_days, available_days - end_shift_days)
    if actual_days <= 0 or end_shift_days < 0:
        return empty_result()
    start_idx = aligned_len - end_shift_days - actual_days - 1
    end_upper = aligned_len - end_shift_days - 1
    if start_idx < 219 or end_upper <= start_idx:
        return empty_result()
    strategy_equity = 1.0
    benchmark_equity = 1.0
    position_days = 0
    win_days = 0
    total_daily_return = 0.0
    total_daily_picks = 0
    total_exposure = 0.0
    stop_hits = 0
    weak_market_days = 0
    top_size = max(1, top)
    code_to_stock = {stock.code: stock for stock in aligned_stocks}
    for end_idx in range(start_idx, end_upper):
        snapshot = [replace(stock, prices=stock.prices[: end_idx + 1], volumes=stock.volumes[: end_idx + 1]) for stock in aligned_stocks]
        regime_factor, weak_market = market_regime_factor(snapshot, config)
        if weak_market:
            weak_market_days += 1
        candidates = select_candidates_with_quota(ma_strategy_candidates(snapshot), top_size, strategy_ratios)
        daily_picks = len(candidates)
        total_daily_picks += daily_picks
        daily_return = 0.0
        exposure = 0.0
        if daily_picks:
            position_days += 1
            weighted_scores = [max(item["score"], 0.0) + 1.0 for item in candidates]
            total_score = sum(weighted_scores)
            weighted_return = 0.0
            distance_sum = 0.0
            for item in candidates:
                code = item["stock"].code
                stock = code_to_stock[code]
                current_price = stock.prices[end_idx]
                next_price = stock.prices[end_idx + 1]
                lows = lows_map.get(code)
                next_low_price = lows[end_idx + 1] if lows and len(lows) > end_idx + 1 else next_price
                candidate_ret, hit_stop = candidate_return_with_stop(
                    current_price,
                    next_price,
                    next_low_price,
                    item["stop_price"],
                    item["ma30"],
                    config,
                )
                if hit_stop:
                    stop_hits += 1
                score_weight = (max(item["score"], 0.0) + 1.0) / total_score
                equal_weight = 1 / daily_picks
                weight = equal_weight * 0.7 + score_weight * 0.3
                weighted_return += weight * candidate_ret
                distance_sum += current_price / item["ma200"] - 1
            coverage = daily_picks / top_size
            avg_distance = distance_sum / daily_picks
            trend_strength = min(1.0, max(0.35, 0.55 + avg_distance * 2.5))
            exposure = min(1.0, coverage * 0.6 + trend_strength * 0.4) * regime_factor
            daily_return = weighted_return * exposure
            if daily_return > 0:
                win_days += 1
        strategy_equity *= 1 + daily_return
        total_daily_return += daily_return
        total_exposure += exposure
        benchmark_daily_return = sum(stock.prices[end_idx + 1] / stock.prices[end_idx] - 1 for stock in aligned_stocks) / len(aligned_stocks)
        benchmark_equity *= 1 + benchmark_daily_return
    total_return = strategy_equity - 1
    benchmark_return = benchmark_equity - 1
    annualized_return = (strategy_equity ** (240 / actual_days) - 1) if actual_days > 0 and strategy_equity > 0 else 0.0
    return {
        "backtest_days": float(actual_days),
        "position_days": float(position_days),
        "position_ratio": position_days / actual_days,
        "total_return": total_return,
        "annualized_return": annualized_return,
        "benchmark_return": benchmark_return,
        "excess_return": total_return - benchmark_return,
        "win_rate": (win_days / position_days) if position_days else 0.0,
        "avg_daily_return": (total_daily_return / position_days) if position_days else 0.0,
        "avg_daily_picks": total_daily_picks / actual_days,
        "avg_exposure": total_exposure / actual_days,
        "stop_hits": float(stop_hits),
        "market_weak_days": float(weak_market_days),
    }


def result_rank(result: Dict[str, float]) -> tuple[float, float, float]:
    return (
        result["excess_return"],
        result["total_return"],
        result["win_rate"],
    )


def around(value: float, delta: float, minimum: float, maximum: float, precision: int = 3) -> List[float]:
    values = {round(value, precision), round(value - delta, precision), round(value + delta, precision)}
    clamped = [min(maximum, max(minimum, item)) for item in values]
    return sorted(set(round(item, precision) for item in clamped))


def optimize_backtest_params(
    stocks: List[Stock],
    lows_map: Dict[str, List[float]],
    top: int,
    backtest_days: int,
    end_shift_days: int = 0,
    strategy_ratios: Dict[str, float] | None = None,
) -> tuple[Dict[str, float], Dict[str, float]]:
    best_config = default_backtest_config()
    best_result = run_backtest(stocks, lows_map, top, backtest_days, best_config, end_shift_days, strategy_ratios)
    regime_base_list = [0.72, 0.75, 0.78]
    weak_cap_list = [0.9, 0.92, 0.95]
    regime_floor_list = [0.65, 0.7]
    ma30_stop_list = [0.982, 0.985, 0.988]
    price_floor_list = [0.968, 0.97, 0.972]
    close_confirm_list = [1.003, 1.005]
    for regime_base in regime_base_list:
        for weak_cap in weak_cap_list:
            for regime_floor in regime_floor_list:
                for ma30_stop in ma30_stop_list:
                    for price_floor in price_floor_list:
                        for close_confirm in close_confirm_list:
                            config = default_backtest_config()
                            config["regime_base"] = regime_base
                            config["weak_cap"] = weak_cap
                            config["regime_floor"] = regime_floor
                            config["ma30_stop_multiplier"] = ma30_stop
                            config["price_floor_multiplier"] = price_floor
                            config["close_confirm_buffer"] = close_confirm
                            result = run_backtest(stocks, lows_map, top, backtest_days, config, end_shift_days, strategy_ratios)
                            if result_rank(result) > result_rank(best_result):
                                best_result = result
                                best_config = config
    fine_regime_base = around(best_config["regime_base"], 0.005, 0.70, 0.82)
    fine_weak_cap = around(best_config["weak_cap"], 0.01, 0.86, 0.98)
    fine_regime_floor = around(best_config["regime_floor"], 0.01, 0.60, 0.78)
    fine_ma30_stop = around(best_config["ma30_stop_multiplier"], 0.001, 0.978, 0.99)
    fine_price_floor = around(best_config["price_floor_multiplier"], 0.001, 0.965, 0.978)
    fine_close_confirm = around(best_config["close_confirm_buffer"], 0.001, 1.001, 1.007)
    for regime_base in fine_regime_base:
        for weak_cap in fine_weak_cap:
            for regime_floor in fine_regime_floor:
                for ma30_stop in fine_ma30_stop:
                    for price_floor in fine_price_floor:
                        for close_confirm in fine_close_confirm:
                            config = default_backtest_config()
                            config["regime_base"] = regime_base
                            config["weak_cap"] = weak_cap
                            config["regime_floor"] = regime_floor
                            config["ma30_stop_multiplier"] = ma30_stop
                            config["price_floor_multiplier"] = price_floor
                            config["close_confirm_buffer"] = close_confirm
                            result = run_backtest(stocks, lows_map, top, backtest_days, config, end_shift_days, strategy_ratios)
                            if result_rank(result) > result_rank(best_result):
                                best_result = result
                                best_config = config
    return best_config, best_result


def available_backtest_days(stocks: List[Stock]) -> int:
    valid_lengths = [len(stock.prices) for stock in stocks if len(stock.prices) >= 221 and len(stock.prices) == len(stock.volumes)]
    if not valid_lengths:
        return 0
    return min(valid_lengths) - 221 + 1


def walk_forward_tune(
    stocks: List[Stock],
    lows_map: Dict[str, List[float]],
    top: int,
    backtest_days: int,
    train_ratio: float = 0.6,
    strategy_ratios: Dict[str, float] | None = None,
) -> Dict[str, object]:
    total_days = min(backtest_days, available_backtest_days(stocks))
    if total_days <= 40:
        config = default_backtest_config()
        baseline = run_backtest(stocks, lows_map, top, backtest_days, config, 0, strategy_ratios)
        return {
            "config": config,
            "train_days": float(total_days),
            "validation_days": 0.0,
            "train_result": baseline,
            "validation_result": empty_result(),
            "combined_result": baseline,
        }
    train_days = int(total_days * train_ratio)
    train_days = max(30, min(train_days, total_days - 20))
    validation_days = total_days - train_days
    best_config, train_result = optimize_backtest_params(stocks, lows_map, top, train_days, validation_days, strategy_ratios)
    validation_result = run_backtest(stocks, lows_map, top, validation_days, best_config, 0, strategy_ratios)
    combined_result = run_backtest(stocks, lows_map, top, total_days, best_config, 0, strategy_ratios)
    return {
        "config": best_config,
        "train_days": float(train_days),
        "validation_days": float(validation_days),
        "train_result": train_result,
        "validation_result": validation_result,
        "combined_result": combined_result,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="自动运行均线选股并回测一年收益")
    parser.add_argument("--db", type=str, default="hs300.db", help="SQLite 文件路径")
    parser.add_argument("--top", type=int, default=10, help="每日最多持仓股票数")
    parser.add_argument("--days", type=int, default=240, help="回测交易日数量")
    parser.add_argument("--quota", type=str, default="4,3,3", help="形态配额，格式例如 4,3,3")
    parser.add_argument("--tune", action="store_true", help="自动搜索更优回测参数")
    parser.add_argument("--walk-forward", action="store_true", help="滚动窗口训练/验证寻优")
    args = parser.parse_args()
    strategy_ratios = parse_quota_ratios(args.quota)
    stocks = load_stocks(args.db)
    lows_map = load_daily_lows(stocks, args.db)
    picks_rows = build_ma_picks_rows(stocks, args.top, strategy_ratios)
    print(f"均线选股结果 Top {args.top}")
    if picks_rows:
        headers = ["代码", "名称", "形态", "信号", "策略", "最新价", "MA20", "MA30", "MA50", "MA100", "MA200", "量比", "止损价"]
        print(format_table(headers, picks_rows))
    else:
        print("无符合条件的标的")
    config = default_backtest_config()
    result = run_backtest(stocks, lows_map, args.top, args.days, config, 0, strategy_ratios)
    if args.tune:
        config, result = optimize_backtest_params(stocks, lows_map, args.top, args.days, 0, strategy_ratios)
        config_rows = [
            ["regime_base", f"{config['regime_base']:.3f}"],
            ["weak_cap", f"{config['weak_cap']:.3f}"],
            ["regime_floor", f"{config['regime_floor']:.3f}"],
            ["ma30_stop", f"{config['ma30_stop_multiplier']:.3f}"],
            ["price_floor", f"{config['price_floor_multiplier']:.3f}"],
            ["close_confirm", f"{config['close_confirm_buffer']:.3f}"],
        ]
        print("")
        print("最优参数")
        print(format_table(["参数", "值"], config_rows))
    if args.walk_forward:
        wf_result = walk_forward_tune(stocks, lows_map, args.top, args.days, 0.6, strategy_ratios)
        config = wf_result["config"]
        result = wf_result["combined_result"]
        config_rows = [
            ["regime_base", f"{config['regime_base']:.3f}"],
            ["weak_cap", f"{config['weak_cap']:.3f}"],
            ["regime_floor", f"{config['regime_floor']:.3f}"],
            ["ma30_stop", f"{config['ma30_stop_multiplier']:.3f}"],
            ["price_floor", f"{config['price_floor_multiplier']:.3f}"],
            ["close_confirm", f"{config['close_confirm_buffer']:.3f}"],
            ["训练天数", f"{int(wf_result['train_days'])}"],
            ["验证天数", f"{int(wf_result['validation_days'])}"],
        ]
        train_result = wf_result["train_result"]
        validation_result = wf_result["validation_result"]
        split_rows = [
            ["训练期累计收益", f"{train_result['total_return']:.2%}"],
            ["训练期超额收益", f"{train_result['excess_return']:.2%}"],
            ["验证期累计收益", f"{validation_result['total_return']:.2%}"],
            ["验证期超额收益", f"{validation_result['excess_return']:.2%}"],
        ]
        print("")
        print("滚动窗口最优参数")
        print(format_table(["参数", "值"], config_rows))
        print("")
        print("训练/验证表现")
        print(format_table(["指标", "结果"], split_rows))
    quota_rows = [
        ["突破配额", f"{strategy_ratios['多均线突破']:.2%}"],
        ["回踩配额", f"{strategy_ratios['多均线回踩']:.2%}"],
        ["趋势配额", f"{strategy_ratios['多均线趋势']:.2%}"],
    ]
    print("")
    print("形态配额")
    print(format_table(["指标", "结果"], quota_rows))
    summary_rows = [
        ["回测交易日", f"{int(result['backtest_days'])}"],
        ["有持仓天数", f"{int(result['position_days'])}"],
        ["持仓覆盖率", f"{result['position_ratio']:.2%}"],
        ["平均仓位", f"{result['avg_exposure']:.2%}"],
        ["策略累计收益", f"{result['total_return']:.2%}"],
        ["策略年化收益", f"{result['annualized_return']:.2%}"],
        ["基准累计收益", f"{result['benchmark_return']:.2%}"],
        ["超额收益", f"{result['excess_return']:.2%}"],
        ["胜率", f"{result['win_rate']:.2%}"],
        ["止损触发次数", f"{int(result['stop_hits'])}"],
        ["弱势市场天数", f"{int(result['market_weak_days'])}"],
        ["单日平均收益", f"{result['avg_daily_return']:.2%}"],
        ["日均入选数量", f"{result['avg_daily_picks']:.2f}"],
    ]
    print("")
    print("均线策略一年回测")
    print(format_table(["指标", "结果"], summary_rows))


if __name__ == "__main__":
    main()
