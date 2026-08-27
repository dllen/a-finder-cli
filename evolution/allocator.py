"""分配器：按策略 expectancy 在信号策略预算内比例分配配额 + 去留判定（纯函数）。

阈值来自 spec 2026-08-27-strategy-evolution-design §4。均线系（70% 基线）不参与进化。
"""

from dataclasses import dataclass, field
from typing import Dict, List, Union

from candidate_rules import DEFAULT_STRATEGY_RATIOS

EVOLVE_BUDGET = 0.30   # 信号策略总预算（均线基线之上）
MIN_RATIO = 0.05       # 在册策略配额下限（≈top20 榜单的 1 席）
MAX_RATIO = 0.15       # 单策略配额上限（集中度帽）
TRIAL_RATIO = 0.05     # 宽限试跑席位
MIN_SAMPLES = 30       # 去留判定最小样本量
WIN_RATE_FLOOR = 0.35  # 胜率下限
SUPPRESS_L1 = 0.05     # 变化抑制：L1 距离 ≤ 该值不生成新版本


@dataclass
class Challenger:
    active: List[str]
    ratios: Dict[str, float]


@dataclass
class NoChange:
    reason: str = field(default="")


def _allocate(names: List[str], exps: Dict[str, float], total: float,
              min_ratio: float, max_ratio: float) -> Dict[str, float]:
    """expectancy 比例分配 + 上下限夹逼，残差在未触限策略中再分配。"""
    alloc: Dict[str, float] = {n: 0.0 for n in names}
    free = list(names)
    rem = total
    while free:
        s = sum(max(exps[n], 1e-9) for n in free)
        shares = {n: rem * max(exps[n], 1e-9) / s for n in free}
        clipped = False
        for n in list(free):
            if shares[n] < min_ratio:
                alloc[n] = min_ratio
                rem -= min_ratio
                free.remove(n)
                clipped = True
            elif shares[n] > max_ratio:
                alloc[n] = max_ratio
                rem -= max_ratio
                free.remove(n)
                clipped = True
        if not clipped:
            for n in free:
                alloc[n] = shares[n]
            rem = 0.0
            break
    if rem > 1e-9 and names:
        # 夹逼后的剩余额度按 expectancy 降序回填（不超上限）
        for n in sorted(names, key=lambda k: -exps.get(k, 0.0)):
            add = min(max_ratio - alloc[n], rem)
            if add > 0:
                alloc[n] += add
                rem -= add
            if rem <= 1e-9:
                break
    if rem < -1e-9:
        # ponytail: 下限总和超预算（>6 个正配额策略才会发生）→ 均分回退
        alloc = {n: max(total / len(names), 0.0) for n in names} if names else alloc
    return alloc


def next_config(stats: Dict[str, "object"], ratios: Dict[str, float],
                active: List[str], universe: List[str]) -> Union[Challenger, NoChange]:
    """universe = 全部信号策略名；stats = attribute() 输出；返回挑战者或 NoChange。"""
    base_ma = {k: float(ratios.get(k, v)) for k, v in DEFAULT_STRATEGY_RATIOS.items()}
    active_set = set(active)

    positive: List[str] = []
    trial: List[str] = []
    off: List[str] = []
    for name in universe:
        s = stats.get(name)
        n = getattr(s, "n", 0) if s else 0
        win_rate = getattr(s, "win_rate", 0.0) if s else 0.0
        expectancy = getattr(s, "expectancy", 0.0) if s else 0.0
        if n >= MIN_SAMPLES:
            if win_rate < WIN_RATE_FLOOR or expectancy <= 0:
                off.append(name)
            else:
                positive.append(name)
        elif n > 0 and expectancy > 0 and win_rate >= WIN_RATE_FLOOR and name not in active_set:
            off.append(name)  # 样本不足的场外候选：证据不够不激活（复活需 n≥30）
        elif name in active_set or n > 0:
            trial.append(name)  # 宽限试跑：在册未满 MIN_SAMPLES 保留 1 席
        else:
            off.append(name)

    budget = EVOLVE_BUDGET
    if trial and TRIAL_RATIO * len(trial) > budget:
        trial_each = budget / len(trial)
        trial_map = {t: trial_each for t in trial}
    else:
        trial_map = {t: TRIAL_RATIO for t in trial}
    remaining = budget - sum(trial_map.values())
    exps = {p: float(getattr(stats[p], "expectancy", 0.0)) for p in positive}
    pos_map = _allocate(positive, exps, max(remaining, 0.0), MIN_RATIO, MAX_RATIO) if positive else {}

    sig_new: Dict[str, float] = {name: 0.0 for name in universe}
    sig_new.update(pos_map)
    sig_new.update(trial_map)
    sig_new = {k: round(v, 4) for k, v in sig_new.items()}
    new_active = sorted(set(positive) | set(trial))

    cur_sig = {name: float(ratios.get(name, 0.0)) for name in universe}
    l1 = sum(abs(sig_new.get(n, 0.0) - cur_sig.get(n, 0.0)) for n in universe)
    if set(new_active) == active_set and l1 <= SUPPRESS_L1:
        return NoChange(f"配置变化在抑制带内（L1={l1:.3f} ≤ {SUPPRESS_L1}）")

    full = dict(base_ma)
    full.update(sig_new)
    return Challenger(active=new_active, ratios=full)
