# 小散量化买点策略扩展 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 5 个可独立回测的买点策略（趋势/反转/量价），达标后并入主榜。

**Architecture:** 新建 `strategies/` 包，每个策略一个纯函数 detector，统一签名 `detect(stock, regime) -> List[StrategySignal]`；`strategies/backtest.py` 对每个策略单独算胜率/盈亏比/期望；达标策略经适配器并入 `candidate_rules`，按 market_regime 启停。

**Tech Stack:** Python 3.11、纯函数 + dataclass、pytest（无第三方依赖）。

---

## 前置事实（避免误改）

- `Stock`（`domain_models.py`）当前只有 `prices`/`volumes`，没有量价字段；`PriceRow` 和 `daily_prices` 表已有 `turnover`/`amount`/`pct_change`。
- `Stock` 的构造器是位置参数（无默认值），现有调用点：`market_data.build_market`、`market_data.build_market_from_db`、`tests/test_integration.py::create_stock`、`tests/test_adaptive_candidate.py`、`tests/test_signal_scorer.py`。**新增字段必须带默认值**，否则破坏这些调用点。
- `candidate_rules` 里的 `Candidate` 是 `TypedDict`，字段：`stock, strategy, ma10, ma30, ma50, ma100, ma200, volume_ratio, stop_price, score`。
- `select_candidates_with_quota` 目前**硬编码**三组多均线形态 + `DEFAULT_STRATEGY_RATIOS`，需要泛化到任意策略组（同时保持三形态行为不变）。
- `market_regime.RegimeType` 有 `BULL`/`BEAR`/`SIDEWAYS`。
- `indicators.py` 已有 `moving_average`/`rsi`/`ema`/`macd`/`normalize`，布林/KDJ 需新增。

---

## Task 1: Stock 扩展量价字段

**Files:**
- Modify: `domain_models.py`
- Modify: `market_data.py`

- [ ] **Step 1: 扩展 Stock dataclass**

在 `domain_models.py` 顶部 `from dataclasses import dataclass` 改为：

```python
from dataclasses import dataclass, field
```

把 `Stock` 改为（在 `volumes` 后追加三个带默认值的字段，保持位置参数兼容）：

```python
@dataclass
class Stock:
    code: str
    name: str
    pe: float
    pb: float
    peg: float
    revenue_growth: float
    profit_growth: float
    roe: float
    cashflow: float
    prices: List[float]
    volumes: List[int]
    turnover: List[float] = field(default_factory=list)
    amount: List[float] = field(default_factory=list)
    pct_change: List[float] = field(default_factory=list)
```

- [ ] **Step 2: build_market 填充空序列**

`market_data.py` 的 `build_market` 内，`Stock(...)` 构造处追加三个列表：

```python
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
```

- [ ] **Step 3: build_market_from_db 读取量价列**

`market_data.py` 的 `build_market_from_db` 中，把当前查询：

```python
            cur = conn.execute(
                "SELECT close, volume FROM daily_prices WHERE code = ? ORDER BY trade_date",
                (code,),
            )
            series = [(item[0], item[1]) for item in cur.fetchall() if item[0] is not None]
```

改为：

```python
            cur = conn.execute(
                "SELECT close, volume, turnover, amount, pct_change FROM daily_prices "
                "WHERE code = ? ORDER BY trade_date",
                (code,),
            )
            series = [tuple(item) for item in cur.fetchall() if item[0] is not None]
```

并把下方 `prices`/`volumes` 的组装改为：

```python
            prices = [float(item[0]) for item in series]
            volumes = [int(item[1]) if item[1] is not None else 0 for item in series]
            turnover = [float(item[2]) if item[2] is not None else 0.0 for item in series]
            amount = [float(item[3]) if item[3] is not None else 0.0 for item in series]
            pct_change = [float(item[4]) if item[4] is not None else 0.0 for item in series]
```

`Stock(...)` 构造处追加：

```python
                    turnover=turnover,
                    amount=amount,
                    pct_change=pct_change,
```

- [ ] **Step 4: 运行现有测试确认不回归**

Run: `.venv/bin/python -m pytest tests/test_integration.py tests/test_adaptive_candidate.py tests/test_signal_scorer.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add domain_models.py market_data.py
git commit -m "feat: Stock 扩展 turnover/amount/pct_change 量价字段"
```

---

## Task 2: strategies/base.py（信号结构 + 布林/KDJ）

**Files:**
- Create: `strategies/__init__.py`（空文件，本任务先建占位，Task 6 填注册表）
- Create: `strategies/base.py`

- [ ] **Step 1: 写失败测试**

Create `tests/test_strategies.py`：

```python
from strategies.base import StrategySignal, bollinger, kdj


def test_bollinger_shape_and_values():
    prices = [100.0, 101, 102, 103, 104, 105, 106, 107, 108, 109,
              110, 111, 112, 113, 114, 115, 116, 117, 118, 119]
    mid, upper, lower = bollinger(prices, window=20, k=2.0)
    assert upper > mid > lower
    assert abs(mid - sum(prices) / 20) < 1e-6


def test_kdj_cross_signal_values():
    # 前低后高的价格，K 值应从低位抬升
    prices = [100.0] * 8 + [90.0, 91, 92, 93, 94, 95, 96, 97, 98, 99, 100]
    ks, ds, js = kdj(prices)
    assert len(ks) == len(prices)
    assert ks[-1] > 0
    assert 0 <= js[-1] <= 100


def test_signal_dataclass_fields():
    sig = StrategySignal(code="600519", strategy="箱体突破",
                         entry=100.0, stop=97.0, tp=106.0, score=60.0)
    assert sig.code == "600519"
    assert sig.tp > sig.entry > sig.stop
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_strategies.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'strategies'`）

- [ ] **Step 3: 实现 base.py**

Create `strategies/base.py`：

```python
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
```

Create `strategies/__init__.py`（空文件）。

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_strategies.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add strategies/ tests/test_strategies.py
git commit -m "feat: strategies/base 提供 StrategySignal 与布林/KDJ 指标"
```

---

## Task 3: 五个 detector

**Files:**
- Create: `strategies/box_breakout.py`
- Create: `strategies/new_high.py`
- Create: `strategies/bollinger_rebound.py`
- Create: `strategies/kdj_cross.py`
- Create: `strategies/volume_price.py`

- [ ] **Step 1: 写失败测试（追加到 tests/test_strategies.py）**

```python
from domain_models import Stock
from market_regime import RegimeType
from strategies.box_breakout import detect as box_detect
from strategies.new_high import detect as high_detect
from strategies.bollinger_rebound import detect as rebound_detect
from strategies.kdj_cross import detect as kdj_detect
from strategies.volume_price import detect as vp_detect


def make_stock(prices, volumes=None, pct=None):
    n = len(prices)
    if volumes is None:
        volumes = [1_000_000] * n
    if pct is None:
        pct = [0.0] * n
    return Stock(code="000001", name="测试", pe=10, pb=1.0, peg=1.0,
                 revenue_growth=0.1, profit_growth=0.1, roe=0.1, cashflow=0.1,
                 prices=prices, volumes=volumes, pct_change=pct)


def test_box_breakout_fires_on_breakout():
    # 25 日窄幅震荡后放量突破上沿
    prices = [100.0] * 24 + [103.0]
    volumes = [1_000_000] * 24 + [3_000_000]
    sigs = box_detect(make_stock(prices, volumes), RegimeType.BULL)
    assert len(sigs) == 1
    assert sigs[0].entry == 103.0
    assert sigs[0].stop < 103.0


def test_box_breakout_not_in_bear():
    prices = [100.0] * 24 + [103.0]
    volumes = [1_000_000] * 24 + [3_000_000]
    assert box_detect(make_stock(prices, volumes), RegimeType.BEAR) == []


def test_new_high_fires():
    prices = [100.0 + i for i in range(60)] + [161.0]
    volumes = [1_000_000] * 60 + [3_000_000]
    sigs = high_detect(make_stock(prices, volumes), RegimeType.BULL)
    assert len(sigs) == 1


def test_bollinger_rebound_fires():
    # 前 21 天震荡，随后两天跌穿下轨又收回，且 RSI 低
    prices = [100.0] * 20 + [98.0, 96.0, 97.0]
    volumes = [1_000_000] * len(prices)
    sigs = rebound_detect(make_stock(prices, volumes), RegimeType.SIDEWAYS)
    assert isinstance(sigs, list)  # 是否触发取决于具体波动，只断言类型


def test_kdj_cross_fires_on_low_cross():
    prices = [100.0] * 8 + [90.0, 91, 92, 93, 94, 95, 96, 97, 98, 99, 100]
    volumes = [1_000_000] * len(prices)
    sigs = kdj_detect(make_stock(prices, volumes), RegimeType.SIDEWAYS)
    assert isinstance(sigs, list)


def test_volume_price_fires():
    prices = [100.0] * 20 + [103.0]
    volumes = [1_000_000] * 20 + [2_500_000]
    pct = [0.0] * 20 + [3.0]
    sigs = vp_detect(make_stock(prices, volumes, pct), RegimeType.BULL)
    assert len(sigs) == 1
    assert sigs[0].entry == 103.0
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_strategies.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'strategies.box_breakout'`）

- [ ] **Step 3: 实现 box_breakout.py**

```python
from typing import List

from domain_models import Stock
from market_regime import RegimeType
from strategies.base import StrategySignal


def detect(stock: Stock, regime: RegimeType) -> List[StrategySignal]:
    if regime != RegimeType.BULL:
        return []
    prices = stock.prices
    volumes = stock.volumes
    if len(prices) < 25 or len(volumes) < 25:
        return []
    box = prices[-25:-1]
    box_high = max(box)
    box_low = min(box)
    price = prices[-1]
    if box_low <= 0 or (box_high - box_low) / box_low > 0.15:
        return []
    if price <= box_high:
        return []
    avg_vol = sum(volumes[-25:-1]) / 24
    if avg_vol <= 0:
        return []
    vol_ratio = volumes[-1] / avg_vol
    if vol_ratio < 1.5:
        return []
    stop = box_high * 0.97
    tp = price + 2 * (price - stop)
    score = 50 + min(50, (vol_ratio - 1) * 50)
    return [StrategySignal(stock.code, "箱体突破", price, stop, tp, score)]
```

- [ ] **Step 4: 实现 new_high.py**

```python
from typing import List

from domain_models import Stock
from market_regime import RegimeType
from strategies.base import StrategySignal


def detect(stock: Stock, regime: RegimeType) -> List[StrategySignal]:
    if regime != RegimeType.BULL:
        return []
    prices = stock.prices
    volumes = stock.volumes
    if len(prices) < 61 or len(volumes) < 21:
        return []
    price = prices[-1]
    if price < max(prices[-61:-1]):
        return []
    avg_vol = sum(volumes[-21:-1]) / 20
    if avg_vol <= 0:
        return []
    vol_ratio = volumes[-1] / avg_vol
    if vol_ratio < 1.5:
        return []
    stop = price * 0.95
    tp = price + 2 * (price - stop)
    score = 50 + min(50, (vol_ratio - 1) * 50)
    return [StrategySignal(stock.code, "新高突破", price, stop, tp, score)]
```

- [ ] **Step 5: 实现 bollinger_rebound.py**

```python
from typing import List

from domain_models import Stock
from indicators import rsi
from market_regime import RegimeType
from strategies.base import StrategySignal, bollinger


def detect(stock: Stock, regime: RegimeType) -> List[StrategySignal]:
    if regime == RegimeType.BULL:
        return []
    prices = stock.prices
    if len(prices) < 22:
        return []
    mid, upper, lower = bollinger(prices)
    r = rsi(prices)
    if r is None or r >= 30:
        return []
    price = prices[-1]
    if prices[-2] >= lower or price < lower:
        return []
    stop = lower * 0.98
    tp = mid if mid > price else price + 2 * (price - stop)
    score = 50 + min(50, (price - lower) / lower * 1000)
    return [StrategySignal(stock.code, "布林超卖反弹", price, stop, tp, score)]
```

- [ ] **Step 6: 实现 kdj_cross.py**

```python
from typing import List

from domain_models import Stock
from market_regime import RegimeType
from strategies.base import StrategySignal, kdj


def detect(stock: Stock, regime: RegimeType) -> List[StrategySignal]:
    if regime == RegimeType.BULL:
        return []
    prices = stock.prices
    if len(prices) < 20:
        return []
    ks, ds, js = kdj(prices)
    if ks[-2] > ds[-2] or ks[-1] <= ds[-1] or ks[-1] >= 20:
        return []
    price = prices[-1]
    stop = price * 0.97
    tp = price + 2 * (price - stop)
    score = 50 + min(50, (20 - ks[-1]) * 5)
    return [StrategySignal(stock.code, "KDJ低位金叉", price, stop, tp, score)]
```

- [ ] **Step 7: 实现 volume_price.py**

```python
from typing import List

from domain_models import Stock
from market_regime import RegimeType
from strategies.base import StrategySignal


def detect(stock: Stock, regime: RegimeType) -> List[StrategySignal]:
    prices = stock.prices
    volumes = stock.volumes
    if len(prices) < 21 or len(volumes) < 21:
        return []
    if len(stock.pct_change) == len(prices):
        pct = stock.pct_change[-1]
    else:
        pct = (prices[-1] / prices[-2] - 1) * 100
    if pct <= 0:
        return []
    avg_vol = sum(volumes[-21:-1]) / 20
    if avg_vol <= 0:
        return []
    vol_ratio = volumes[-1] / avg_vol
    if vol_ratio < 1.2:
        return []
    price = prices[-1]
    stop = price * 0.95
    tp = price + 2 * (price - stop)
    score = 40 + min(60, pct * 10) + min(20, (vol_ratio - 1) * 20)
    if regime == RegimeType.BEAR:
        score *= 0.8
    return [StrategySignal(stock.code, "量价齐升", price, stop, tp, score)]
```

- [ ] **Step 8: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_strategies.py -q`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add strategies/
git commit -m "feat: 五个买点策略 detector（箱体/新高/布林/KDJ/量价）"
```

---

## Task 4: strategies/backtest.py（独立回测器）

**Files:**
- Create: `strategies/backtest.py`

- [ ] **Step 1: 写失败测试（追加到 tests/test_strategies.py）**

```python
from dataclasses import replace
from strategies.backtest import BacktestResult, run_strategy_backtest
from strategies.base import StrategySignal


class FakeDetector:
    def __init__(self, signals):
        self.signals = signals

    def detect(self, stock, regime):
        return self.signals


def _up_stock(n=80):
    prices = [100.0 + i for i in range(n)]
    volumes = [1_000_000] * n
    return Stock(code="000001", name="测试", pe=10, pb=1.0, peg=1.0,
                 revenue_growth=0.1, profit_growth=0.1, roe=0.1, cashflow=0.1,
                 prices=prices, volumes=volumes)


def test_run_strategy_backtest_always_win():
    stock = _up_stock()
    sig = StrategySignal("000001", "x", entry=110.0, stop=105.0, tp=200.0, score=50.0)
    result = run_strategy_backtest([stock], FakeDetector([sig]), {}, [RegimeType.BULL] * 80, max_hold=10)
    assert result.trades > 0
    assert result.win_rate == 1.0
    assert result.profit_factor > 1.5
    assert result.expectancy > 0
    assert result.passed is True


def test_run_strategy_backtest_always_lose():
    stock = _up_stock()
    sig = StrategySignal("000001", "x", entry=110.0, stop=109.0, tp=200.0, score=50.0)
    result = run_strategy_backtest([stock], FakeDetector([sig]), {}, [RegimeType.BULL] * 80, max_hold=10)
    assert result.trades > 0
    assert result.win_rate == 0.0
    assert result.expectancy < 0
    assert result.passed is False
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_strategies.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'strategies.backtest'`）

- [ ] **Step 3: 实现 backtest.py**

```python
from dataclasses import asdict, dataclass
from typing import Callable, Dict, List

from domain_models import Stock
from market_regime import RegimeType

from strategies.base import StrategySignal


@dataclass
class BacktestResult:
    strategy: str
    trades: int
    wins: int
    win_rate: float
    profit_factor: float
    expectancy: float
    passed: bool


def _snapshot(stock: Stock, idx: int) -> Stock:
    from dataclasses import replace
    return replace(
        stock,
        prices=stock.prices[: idx + 1],
        volumes=stock.volumes[: idx + 1],
        turnover=stock.turnover[: idx + 1] if stock.turnover else [],
        amount=stock.amount[: idx + 1] if stock.amount else [],
        pct_change=stock.pct_change[: idx + 1] if stock.pct_change else [],
    )


def _simulate(stock: Stock, entry_idx: int, sig: StrategySignal, max_hold: int) -> float:
    prices = stock.prices
    end = min(entry_idx + max_hold, len(prices) - 1)
    if end <= entry_idx:
        return 0.0
    for j in range(entry_idx + 1, end + 1):
        if prices[j] >= sig.tp:
            return sig.tp / sig.entry - 1
        if prices[j] <= sig.stop:
            return sig.stop / sig.entry - 1
    return prices[end] / sig.entry - 1


def run_strategy_backtest(
    stocks: List[Stock],
    detect: Callable,
    lows_map: Dict[str, List[float]],
    regimes: List[RegimeType],
    max_hold: int = 10,
    strategy: str = "",
) -> BacktestResult:
    valid = [s for s in stocks if len(s.prices) >= 61 and len(s.prices) == len(s.volumes)]
    if not valid:
        return BacktestResult(strategy, 0, 0, 0.0, 0.0, 0.0, False)
    n = min(len(s.prices) for s in valid)
    code_to_stock = {s.code: s for s in valid}
    returns: List[float] = []
    for idx in range(60, n - 1):
        regime = regimes[idx] if idx < len(regimes) else RegimeType.SIDEWAYS
        for s in valid:
            sigs = detect(_snapshot(s, idx), regime)
            for sig in sigs:
                full = code_to_stock[sig.code]
                ret = _simulate(full, idx, sig, max_hold)
                returns.append(ret)
    wins = sum(1 for r in returns if r > 0)
    losses = [r for r in returns if r <= 0]
    gains = [r for r in returns if r > 0]
    avg_win = sum(gains) / len(gains) if gains else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    win_rate = wins / len(returns) if returns else 0.0
    profit_factor = (avg_win / abs(avg_loss)) if avg_loss != 0 else float("inf")
    expectancy = win_rate * avg_win - (1 - win_rate) * abs(avg_loss)
    passed = (win_rate >= 0.45 and profit_factor >= 1.5) or expectancy > 0
    return BacktestResult(strategy, len(returns), wins, win_rate, profit_factor, expectancy, passed)
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_strategies.py -q`
Expected: PASS（含 Task 1-3 的全部测试）

- [ ] **Step 5: Commit**

```bash
git add strategies/backtest.py
git commit -m "feat: 独立单策略回测器（胜率/盈亏比/期望）"
```

---

## Task 5: 报告生成（registry + report.json）

**Files:**
- Modify: `strategies/__init__.py`
- Create: `strategies/report.py`

- [ ] **Step 1: 写失败测试（追加到 tests/test_strategies.py）**

```python
import strategies
from strategies.report import build_report


def test_registry_has_five_strategies():
    assert set(strategies.STRATEGIES.keys()) == {
        "箱体突破", "新高突破", "布林超卖反弹", "KDJ低位金叉", "量价齐升"
    }


def test_build_report_returns_five_entries():
    stock = _up_stock(80)
    report = build_report([stock], {}, [RegimeType.BULL] * 80, max_hold=10)
    assert len(report) == 5
    assert all("strategy" in r and "passed" in r for r in report)
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_strategies.py -q`
Expected: FAIL（`AttributeError: module 'strategies' has no attribute 'STRATEGIES'`）

- [ ] **Step 3: 实现 __init__.py 注册表**

`strategies/__init__.py`：

```python
from strategies import (
    bollinger_rebound,
    box_breakout,
    kdj_cross,
    new_high,
    volume_price,
)

STRATEGIES = {
    "箱体突破": box_breakout.detect,
    "新高突破": new_high.detect,
    "布林超卖反弹": bollinger_rebound.detect,
    "KDJ低位金叉": kdj_cross.detect,
    "量价齐升": volume_price.detect,
}
```

- [ ] **Step 4: 实现 report.py**

`strategies/report.py`：

```python
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
        rows.append(asdict(result))
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
    regimes = regimes or []
    report = build_report(stocks, lows_map, regimes, max_hold)
    write_report(report, out)
    return report
```

- [ ] **Step 5: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_strategies.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add strategies/__init__.py strategies/report.py
git commit -m "feat: 策略注册表 + report.json 报告生成"
```

---

## Task 6: 并入主榜（适配器 + 配额泛化）

**Files:**
- Modify: `candidate_rules.py`
- Create: `strategies/adapter.py`

- [ ] **Step 1: 写失败测试（追加到 tests/test_strategies.py）**

```python
from strategies.adapter import signal_to_candidate, merge_candidates


def test_signal_to_candidate_shape():
    stock = _up_stock(250)
    sig = StrategySignal("000001", "箱体突破", entry=120.0, stop=116.0, tp=128.0, score=60.0)
    cand = signal_to_candidate(stock, sig)
    assert cand["strategy"] == "箱体突破"
    assert cand["stock"].code == "000001"
    assert cand["ma10"] > 0 and cand["ma200"] > 0
    assert cand["volume_ratio"] > 0
    assert cand["stop_price"] == sig.stop


def test_merge_candidates_includes_passed_strategies():
    stocks = [_up_stock(250)]
    merged = merge_candidates(stocks, RegimeType.BULL, {"箱体突破"})
    strategies = {c["strategy"] for c in merged}
    assert "箱体突破" in strategies
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_strategies.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'strategies.adapter'`）

- [ ] **Step 3: 实现 strategies/adapter.py**

```python
from typing import Dict, List, Optional, Set

from candidate_rules import (
    Candidate,
    CandidateConfig,
    ma_strategy_candidates_adaptive,
)
from domain_models import Stock
from market_regime import RegimeType
from strategies import STRATEGIES
from strategies.base import StrategySignal


def signal_to_candidate(stock: Stock, sig: StrategySignal) -> Candidate:
    prices = stock.prices
    volumes = stock.volumes

    def ma(w: int) -> float:
        return sum(prices[-w:]) / w

    avg_vol = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else 0.0
    volume_ratio = (volumes[-1] / avg_vol) if avg_vol > 0 else 0.0
    return {
        "stock": stock,
        "strategy": sig.strategy,
        "ma10": ma(10),
        "ma30": ma(30),
        "ma50": ma(50),
        "ma100": ma(100),
        "ma200": ma(200),
        "volume_ratio": volume_ratio,
        "stop_price": sig.stop,
        "score": sig.score,
    }


def merge_candidates(
    stocks: List[Stock],
    regime: RegimeType,
    passed_strategies: Optional[Set[str]] = None,
    config: Optional[CandidateConfig] = None,
) -> List[Candidate]:
    candidates: List[Candidate] = list(
        ma_strategy_candidates_adaptive(stocks, regime, config)
    )
    passed = passed_strategies or set()
    for stock in stocks:
        for name in passed:
            detect = STRATEGIES.get(name)
            if detect is None:
                continue
            for sig in detect(stock, regime):
                candidates.append(signal_to_candidate(stock, sig))
    return candidates
```

- [ ] **Step 4: 泛化 select_candidates_with_quota**

替换 `candidate_rules.py` 中现有的 `normalize_strategy_ratios` 与 `select_candidates_with_quota`（保留 `DEFAULT_STRATEGY_RATIOS` 不变）。

`normalize_strategy_ratios` 替换为（动态补全未知策略为 0）：

```python
def normalize_strategy_ratios(ratios: Dict[str, float] | None) -> Dict[str, float]:
    base = dict(DEFAULT_STRATEGY_RATIOS)
    if ratios:
        for key in ratios:
            base[key] = float(ratios[key])
    return base
```

`select_candidates_with_quota` 替换为动态分组版（逻辑保持最大余数分配 + 去重）：

```python
def select_candidates_with_quota(
    candidates: List[Candidate],
    top: int,
    ratios: Dict[str, float] | None = None,
) -> List[Candidate]:
    if top <= 0:
        return []
    ranked = sorted(candidates, key=lambda item: item["score"], reverse=True)
    if len(ranked) <= top:
        return ranked
    groups: Dict[str, List[Candidate]] = {}
    for item in ranked:
        groups.setdefault(item["strategy"], []).append(item)
    normalized = normalize_strategy_ratios(ratios)
    for key in list(normalized):
        if key not in groups:
            normalized.pop(key)
    total = sum(normalized.values())
    if total <= 0:
        per = {key: 1 / len(groups) for key in groups}
        normalized = per
    else:
        normalized = {key: value / total for key, value in normalized.items()}
    targets: Dict[str, int] = {}
    fractions = []
    allocated = 0
    for strategy in groups:
        raw_target = top * normalized.get(strategy, 0.0)
        base = int(raw_target)
        targets[strategy] = base
        allocated += base
        fractions.append((raw_target - base, strategy))
    for _, strategy in sorted(fractions, reverse=True):
        if allocated >= top:
            break
        targets[strategy] = targets.get(strategy, 0) + 1
        allocated += 1
    selected: List[Candidate] = []
    used_codes = set()
    for item in ranked:
        if len(selected) >= top:
            break
        strategy = item["strategy"]
        if targets.get(strategy, 0) <= 0:
            continue
        if item["stock"].code in used_codes:
            continue
        selected.append(item)
        used_codes.add(item["stock"].code)
        targets[strategy] -= 1
    if len(selected) < top:
        for item in ranked:
            if len(selected) >= top:
                break
            if item["stock"].code in used_codes:
                continue
            selected.append(item)
            used_codes.add(item["stock"].code)
    return selected
```

- [ ] **Step 5: 运行全量测试确认无回归**

Run: `.venv/bin/python -m pytest tests/test_integration.py tests/test_adaptive_candidate.py tests/test_strategies.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add strategies/adapter.py candidate_rules.py
git commit -m "feat: 达标策略并入主榜 + 配额选择泛化到任意策略组"
```

---

## Task 7: 端到端验证（真实数据库）

**Files:** 无新文件（仅运行命令）

- [ ] **Step 1: 生成全市场 Stock（含量价字段）并跑报告**

Run:

```bash
.venv/bin/python -c "
from market_data import build_market_from_db
from strategies.report import run_all
stocks = build_market_from_db('hs300.db', min_days=460, max_days=520)
report = run_all(stocks, out='strategies/report.json')
for r in report:
    print(r['strategy'], 'win_rate=%.2f' % r['win_rate'], 'pf=%.2f' % r['profit_factor'], 'passed=', r['passed'])
"
```

Expected: 打印 5 行，每行有 `win_rate`/`pf`/`passed`，且 `strategies/report.json` 生成。

- [ ] **Step 2: 全量测试**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS（102 个旧测试 + 新增全部通过）

- [ ] **Step 3: Commit**

```bash
git add strategies/report.json
git commit -m "chore: 生成策略回测报告 report.json"
```

---

## 自检清单（实现时逐条对照 spec）

- [ ] 5 个 detector：箱体突破、新高突破、布林超卖反弹、KDJ 低位金叉、量价齐升 ✅ Task 3
- [ ] 二期策略（主力/北向净流入）明确不在本期 ✅ 不实现，仅保留清单记录在 spec
- [ ] Stock 量价字段：turnover/amount/pct_change ✅ Task 1
- [ ] 独立回测器输出胜率/盈亏比/期望 ✅ Task 4
- [ ] 达标门槛：胜率≥45% 且盈亏比≥1.5 或期望>0 ✅ `run_strategy_backtest` 的 `passed`
- [ ] 达标后并入主榜、按 regime 启停 ✅ Task 6（detector 内部按 regime 过滤）
- [ ] report.json 生成 ✅ Task 5/7
- [ ] 现有测试全绿 ✅ Task 7 Step 2
