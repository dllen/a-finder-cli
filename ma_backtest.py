import argparse
import os
import random
from dataclasses import replace
from typing import Dict, List

from candidate_rules import (
    DEFAULT_STRATEGY_RATIOS,
    CandidateConfig,
    ma_strategy_candidates,
    ma_strategy_candidates_adaptive,
    select_candidates_with_quota,
)
from market_regime import detect_regime
from db_repository import open_db
from domain_models import Stock
from formatter import format_table
from market_data import build_market, build_market_from_db
from view_models import build_ma_picks_rows


def load_stocks(db_path: str) -> List[Stock]:
    if db_path and os.path.exists(db_path):
        stocks = build_market_from_db(db_path, min_days=460, max_days=520)
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


def default_candidate_config() -> CandidateConfig:
    return CandidateConfig()


def score_weight_variants(base: CandidateConfig) -> List[CandidateConfig]:
    variants = []
    for slope200 in [1.5, 2.0, 2.5, 3.0]:
        for slope100 in [1.5, 2.0, 2.5]:
            for momentum20 in [100.0, 150.0, 200.0]:
                for momentum10 in [50.0, 80.0, 110.0]:
                    for volume_bonus in [8.0, 10.0, 12.0]:
                        variants.append(
                            replace(
                                base,
                                slope200_weight=slope200,
                                slope100_weight=slope100,
                                momentum20_weight=momentum20,
                                momentum10_weight=momentum10,
                                volume_bonus_weight=volume_bonus,
                            )
                        )
    return variants


def quota_ratio_variants() -> List[Dict[str, float]]:
    variants = []
    for b in [0, 20, 30, 40, 50, 60, 80, 100]:
        for r in [0, 20, 30, 40, 50, 60, 80, 100]:
            for t in [0, 20, 30, 40, 50, 60, 80, 100]:
                total = b + r + t
                if total <= 0:
                    continue
                variants.append(
                    {
                        "多均线突破": b / total,
                        "多均线回踩": r / total,
                        "多均线趋势": t / total,
                    }
                )
    return variants


def optimize_score_weights(
    stocks: List[Stock],
    lows_map: Dict[str, List[float]],
    top: int,
    backtest_days: int,
    strategy_ratios: Dict[str, float] | None = None,
) -> tuple[CandidateConfig, Dict[str, float]]:
    base = default_candidate_config()
    best = base
    best_result = run_backtest(stocks, lows_map, top, backtest_days, default_backtest_config(), 0, strategy_ratios, base)
    for cand in score_weight_variants(base):
        result = run_backtest(stocks, lows_map, top, backtest_days, default_backtest_config(), 0, strategy_ratios, cand)
        if result_rank(result) > result_rank(best_result):
            best = cand
            best_result = result
    return best, best_result


def optimize_quota_ratios(
    stocks: List[Stock],
    lows_map: Dict[str, List[float]],
    top: int,
    backtest_days: int,
    candidate_config: CandidateConfig | None = None,
) -> tuple[Dict[str, float], Dict[str, float]]:
    candidate_config = candidate_config or default_candidate_config()
    best_ratios = DEFAULT_STRATEGY_RATIOS
    best_result = run_backtest(stocks, lows_map, top, backtest_days, default_backtest_config(), 0, best_ratios, candidate_config)
    for ratios in quota_ratio_variants():
        result = run_backtest(stocks, lows_map, top, backtest_days, default_backtest_config(), 0, ratios, candidate_config)
        if result_rank(result) > result_rank(best_result):
            best_ratios = ratios
            best_result = result
    return best_ratios, best_result


def candidate_config_variants() -> List[CandidateConfig]:
    configs = []
    for momentum_20_min in [0.0, 0.015, 0.03, 0.045, 0.06, 0.075, 0.09]:
        for volatility_20_max in [0.25, 0.35, 0.45, 0.55, 0.65]:
            for ma10_distance_max in [0.05, 0.09, 0.13]:
                for breakout_volume_ratio_min in [1.0, 1.1, 1.2]:
                    for trend_follow_momentum_min in [0.02, 0.03, 0.04]:
                        for score_distance200_weight in [0.4, 0.8]:
                            for score_distance50_weight in [0.3, 0.6]:
                                for alignment_depth in [2, 3, 4, 5]:
                                    configs.append(
                                        CandidateConfig(
                                            momentum_20_min=momentum_20_min,
                                            volatility_20_max=volatility_20_max,
                                            ma10_distance_max=ma10_distance_max,
                                            breakout_volume_ratio_min=breakout_volume_ratio_min,
                                            trend_follow_momentum_min=trend_follow_momentum_min,
                                            score_distance200_weight=score_distance200_weight,
                                            score_distance50_weight=score_distance50_weight,
                                            alignment_depth=alignment_depth,
                                        )
                                    )
    return configs


def default_backtest_config() -> Dict[str, float]:
    return {
        "regime_base": 0.795,
        "regime_breadth_weight": 0.2,
        "regime_floor": 0.62,
        "weak_cap": 0.98,
        "ma30_stop_multiplier": 0.979,
        "price_floor_multiplier": 0.974,
        "stop_cap_multiplier": 0.995,
        "close_confirm_buffer": 1.001,
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
        if value < 0:
            raise ValueError("quota 的每一项必须为非负数")
        values.append(value)
    total = sum(values)
    if total <= 0:
        raise ValueError("quota 之和必须大于 0")
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
    candidate_config: CandidateConfig | None = None,
    adaptive: bool = False,
) -> Dict[str, float]:
    config = config or default_backtest_config()
    candidate_config = candidate_config or default_candidate_config()
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
        if adaptive:
            # Use adaptive stock selection with market regime detection
            index_prices = [s.prices[end_idx] for s in snapshot[:50]]
            regime = detect_regime(index_prices, {})
            candidates = select_candidates_with_quota(
                ma_strategy_candidates_adaptive(snapshot, regime, candidate_config),
                top_size, strategy_ratios
            )
        else:
            # Use original logic
            candidates = select_candidates_with_quota(
                ma_strategy_candidates(snapshot, candidate_config), top_size, strategy_ratios
            )
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
    candidate_config: CandidateConfig | None = None,
) -> tuple[Dict[str, float], Dict[str, float]]:
    best_config = default_backtest_config()
    best_result = run_backtest(stocks, lows_map, top, backtest_days, best_config, end_shift_days, strategy_ratios, candidate_config)
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
                            result = run_backtest(stocks, lows_map, top, backtest_days, config, end_shift_days, strategy_ratios, candidate_config)
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
                            result = run_backtest(stocks, lows_map, top, backtest_days, config, end_shift_days, strategy_ratios, candidate_config)
                            if result_rank(result) > result_rank(best_result):
                                best_result = result
                                best_config = config
    return best_config, best_result


def available_backtest_days(stocks: List[Stock]) -> int:
    valid_lengths = [len(stock.prices) for stock in stocks if len(stock.prices) >= 221 and len(stock.prices) == len(stock.volumes)]
    if not valid_lengths:
        return 0
    return min(valid_lengths) - 221 + 1


def tune_on_window(
    stocks: List[Stock],
    lows_map: Dict[str, List[float]],
    top: int,
    train_days: int,
    validation_days: int,
    strategy_ratios: Dict[str, float] | None = None,
) -> tuple[Dict[str, float], CandidateConfig, Dict[str, float]]:
    base_config = default_backtest_config()
    best_candidate = default_candidate_config()
    best_candidate_result = run_backtest(stocks, lows_map, top, train_days, base_config, validation_days, strategy_ratios, best_candidate)
    for cand in candidate_config_variants():
        cand_result = run_backtest(stocks, lows_map, top, train_days, base_config, validation_days, strategy_ratios, cand)
        if result_rank(cand_result) > result_rank(best_candidate_result):
            best_candidate = cand
            best_candidate_result = cand_result
    best_config, train_result = optimize_backtest_params(stocks, lows_map, top, train_days, validation_days, strategy_ratios, best_candidate)
    return best_config, best_candidate, train_result


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
    best_config, best_candidate, train_result = tune_on_window(stocks, lows_map, top, train_days, validation_days, strategy_ratios)
    validation_result = run_backtest(stocks, lows_map, top, validation_days, best_config, 0, strategy_ratios, best_candidate)
    combined_result = run_backtest(stocks, lows_map, top, total_days, best_config, 0, strategy_ratios, best_candidate)
    return {
        "config": best_config,
        "candidate_config": best_candidate,
        "train_days": float(train_days),
        "validation_days": float(validation_days),
        "train_result": train_result,
        "validation_result": validation_result,
        "combined_result": combined_result,
    }


def robust_walk_forward(
    stocks: List[Stock],
    lows_map: Dict[str, List[float]],
    top: int,
    backtest_days: int,
    train_ratio: float = 0.6,
    strategy_ratios: Dict[str, float] | None = None,
    splits: int = 10,
    seed: int = 42,
) -> Dict[str, object]:
    # 对当前固化策略做样本外稳健性检验：不再重新寻优（避免 lookahead），
    # 只用固定参数在多个随机切分的验证段上独立评估。
    total_days = min(backtest_days, available_backtest_days(stocks))
    cfg = default_backtest_config()
    cand = default_candidate_config()
    fixed_train = int(total_days * train_ratio)
    fixed_train = max(10, min(fixed_train, total_days - 10))
    fixed_val = total_days - fixed_train
    fixed_val_result = run_backtest(stocks, lows_map, top, fixed_val, cfg, 0, strategy_ratios, cand)
    split_excess = []
    rng = random.Random(seed)
    for _ in range(splits):
        train_days = rng.randint(int(total_days * 0.3), int(total_days * 0.7))
        train_days = max(10, min(train_days, total_days - 10))
        val_days = total_days - train_days
        val_result = run_backtest(stocks, lows_map, top, val_days, cfg, 0, strategy_ratios, cand)
        split_excess.append(val_result["excess_return"])
    split_excess.sort()
    return {
        "total_days": float(total_days),
        "fixed_validation_excess": fixed_val_result["excess_return"],
        "split_excess": split_excess,
        "positive_ratio": sum(1 for e in split_excess if e > 0) / len(split_excess) if split_excess else 0.0,
        "combined_result": run_backtest(stocks, lows_map, top, total_days, cfg, 0, strategy_ratios, cand),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="自动运行均线选股并回测一年收益")
    parser.add_argument("--db", type=str, default="hs300.db", help="SQLite 文件路径")
    parser.add_argument("--top", type=int, default=10, help="每日最多持仓股票数")
    parser.add_argument("--days", type=int, default=240, help="回测交易日数量")
    parser.add_argument("--quota", type=str, default="3,1,0", help="形态配额，格式例如 3,1,0")
    parser.add_argument("--tune", action="store_true", help="自动搜索更优回测参数")
    parser.add_argument("--walk-forward", action="store_true", help="滚动窗口训练/验证寻优")
    parser.add_argument("--robust", action="store_true", help="稳健性验证：固定末段留出 + 多随机切分")
    parser.add_argument("--splits", type=int, default=10, help="稳健性验证的随机切分次数")
    parser.add_argument("--search-weights", action="store_true", help="搜索评分权重（slope/动量/量比）")
    parser.add_argument("--search-quota", action="store_true", help="搜索形态配额（突破/回踩/趋势）")
    parser.add_argument("--reduce-trades", action="store_true", help="使用自适应策略减少无效交易")
    args = parser.parse_args()
    strategy_ratios = parse_quota_ratios(args.quota)
    stocks = load_stocks(args.db)
    lows_map = load_daily_lows(stocks, args.db)
    picks_rows = build_ma_picks_rows(stocks, args.top, strategy_ratios)
    print(f"均线选股结果 Top {args.top}")
    if picks_rows:
        headers = ["代码", "名称", "形态", "信号", "策略", "最新价", "MA10", "MA30", "MA50", "MA100", "MA200", "量比", "止损价"]
        print(format_table(headers, picks_rows))
    else:
        print("无符合条件的标的")
    config = default_backtest_config()
    result = run_backtest(stocks, lows_map, args.top, args.days, config, 0, strategy_ratios, adaptive=args.reduce_trades)
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
    if args.walk_forward and not args.robust:
        wf_result = walk_forward_tune(stocks, lows_map, args.top, args.days, 0.6, strategy_ratios)
        config = wf_result["config"]
        result = wf_result["combined_result"]
        cand = wf_result["candidate_config"]
        config_rows = [
            ["regime_base", f"{config['regime_base']:.3f}"],
            ["weak_cap", f"{config['weak_cap']:.3f}"],
            ["regime_floor", f"{config['regime_floor']:.3f}"],
            ["ma30_stop", f"{config['ma30_stop_multiplier']:.3f}"],
            ["price_floor", f"{config['price_floor_multiplier']:.3f}"],
            ["close_confirm", f"{config['close_confirm_buffer']:.3f}"],
            ["momentum_20_min", f"{cand.momentum_20_min:.3f}"],
            ["volatility_20_max", f"{cand.volatility_20_max:.3f}"],
            ["ma10_distance_max", f"{cand.ma10_distance_max:.3f}"],
            ["breakout_vol_ratio", f"{cand.breakout_volume_ratio_min:.3f}"],
            ["trend_momentum_min", f"{cand.trend_follow_momentum_min:.3f}"],
            ["score_d200_weight", f"{cand.score_distance200_weight:.3f}"],
            ["score_d50_weight", f"{cand.score_distance50_weight:.3f}"],
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
    if args.robust:
        robust = robust_walk_forward(stocks, lows_map, args.top, args.days, 0.6, strategy_ratios, args.splits)
        result = robust["combined_result"]
        split_excess = robust["split_excess"]
        median = sorted(split_excess)[len(split_excess) // 2] if split_excess else 0.0
        robust_rows = [
            ["固定末段验证超额", f"{robust['fixed_validation_excess']:.2%}"],
            ["随机切分验证超额中位", f"{median:.2%}"],
            ["随机切分验证超额最小", f"{min(split_excess):.2%}" if split_excess else "-"],
            ["随机切分验证超额最大", f"{max(split_excess):.2%}" if split_excess else "-"],
            ["正超额窗口占比", f"{robust['positive_ratio']:.0%}"],
            ["结论", "稳健" if median > 0 and robust["positive_ratio"] >= 0.7 else "可能过拟合"],
        ]
        print("")
        print(f"稳健性验证（{len(split_excess)} 次随机切分）")
        print(format_table(["指标", "结果"], robust_rows))
    if args.search_weights:
        best_cand, best_res = optimize_score_weights(stocks, lows_map, args.top, args.days, strategy_ratios)
        weight_rows = [
            ["slope200", f"{best_cand.slope200_weight:.1f}"],
            ["slope100", f"{best_cand.slope100_weight:.1f}"],
            ["momentum20", f"{best_cand.momentum20_weight:.0f}"],
            ["momentum10", f"{best_cand.momentum10_weight:.0f}"],
            ["volume_bonus", f"{best_cand.volume_bonus_weight:.0f}"],
            ["合并超额", f"{best_res['excess_return']:.2%}"],
        ]
        print("")
        print("评分权重搜索")
        print(format_table(["权重", "值"], weight_rows))
    if args.search_quota:
        best_ratios, best_res = optimize_quota_ratios(stocks, lows_map, args.top, args.days)
        quota_search_rows = [
            ["突破配额", f"{best_ratios['多均线突破']:.2%}"],
            ["回踩配额", f"{best_ratios['多均线回踩']:.2%}"],
            ["趋势配额", f"{best_ratios['多均线趋势']:.2%}"],
            ["合并超额", f"{best_res['excess_return']:.2%}"],
        ]
        print("")
        print("形态配额搜索")
        print(format_table(["指标", "结果"], quota_search_rows))
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
