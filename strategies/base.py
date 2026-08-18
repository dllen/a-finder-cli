from dataclasses import dataclass
from typing import List, Tuple


@dataclass
class StrategySignal:
    code: str
    strategy: str
    entry: float
    stop: float
    tp: float
    score: float


def bollinger(prices: List[float], window: int = 20, k: float = 2.0) -> Tuple[float, float, float]:
    """返回 (mid, upper, lower)，基于最近 window 根收盘价。"""
    window = min(window, len(prices))
    seg = prices[-window:]
    mid = sum(seg) / window
    var = sum((p - mid) ** 2 for p in seg) / window
    std = var ** 0.5
    return mid, mid + k * std, mid - k * std


def kdj(prices: List[float], n: int = 9, k_smooth: int = 3, d_smooth: int = 3) -> Tuple[List[float], List[float], List[float]]:
    """简化 KDJ（仅用收盘价）。K/D 初始 50，返回 (K, D, J) 序列，长度与 prices 相同。"""
    if not prices:
        return [], [], []
    ks: List[float] = []
    ds: List[float] = []
    js: List[float] = []
    k_prev = 50.0
    d_prev = 50.0
    for i in range(len(prices)):
        lo = min(prices[max(0, i - n + 1): i + 1])
        hi = max(prices[max(0, i - n + 1): i + 1])
        rsv = 50.0 if hi == lo else (prices[i] - lo) / (hi - lo) * 100
        k_val = (k_prev * (k_smooth - 1) + rsv) / k_smooth
        d_val = (d_prev * (d_smooth - 1) + k_val) / d_smooth
        j_val = 3 * k_val - 2 * d_val
        ks.append(k_val)
        ds.append(d_val)
        js.append(j_val)
        k_prev, d_prev = k_val, d_val
    return ks, ds, js
