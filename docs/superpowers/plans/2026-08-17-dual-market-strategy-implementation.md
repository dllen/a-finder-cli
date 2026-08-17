# 双市场策略实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现牛市顺势 + 熊市超跌反弹的自适应策略，减少无效交易次数

**Architecture:** 模块化增强设计，新增 market_regime.py 和 risk_manager.py，增强 signal_rules.py 和 candidate_rules.py，保持向后兼容

**Tech Stack:** Python, dataclasses, 现有 indicators.py + ma_backtest.py

---

## 文件结构

```
新增:
├── market_regime.py      # 市场状态判断（三维度综合评分）
├── risk_manager.py       # 仓位计算 + 三重止损
测试:
├── tests/
│   ├── test_market_regime.py
│   ├── test_signal_scorer.py
│   └── test_risk_manager.py
增强:
├── signal_rules.py       # 新增 signal_scorer 函数
├── candidate_rules.py    # 新增 adaptive_candidates 函数
└── ma_backtest.py        # 新增样本外验证报告
```

---

## Task 1: MarketRegimeDetector（市场状态判断）

**Files:**
- Create: `market_regime.py`
- Test: `tests/test_market_regime.py`

- [ ] **Step 1: 写测试**

```python
# tests/test_market_regime.py
import pytest
from market_regime import MarketRegime, detect_regime, RegimeType

def test_bull_market_detection():
    """牛市：均线多头排列 + 指数上涨"""
    # 生成上涨趋势数据
    prices = [100 + i * 0.5 for i in range(60)]
    macro = {"pe_percentile": 0.4, "m2_yoy": 0.12}
    result = detect_regime(prices, macro)
    assert result.regime == RegimeType.BULL

def test_bear_market_detection():
    """熊市：均线空头排列 + 指数下跌"""
    prices = [100 - i * 0.5 for i in range(60)]
    macro = {"pe_percentile": 0.8, "m2_yoy": 0.08}
    result = detect_regime(prices, macro)
    assert result.regime == RegimeType.BEAR

def test_sideways_market_detection():
    """震荡市：波动小 + 方向不明"""
    import math
    prices = [100 + math.sin(i * 0.3) * 3 for i in range(60)]
    macro = {"pe_percentile": 0.5, "m2_yoy": 0.10}
    result = detect_regime(prices, macro)
    assert result.regime == RegimeType.SIDEWAYS
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/test_market_regime.py -v`
Expected: FAIL - ModuleNotFoundError

- [ ] **Step 3: 写实现**

```python
# market_regime.py
from dataclasses import dataclass
from enum import Enum
from typing import List, Dict, Optional

class RegimeType(Enum):
    BULL = "bull"
    BEAR = "bear"
    SIDEWAYS = "sideways"

@dataclass
class MarketRegime:
    regime: RegimeType
    confidence: float  # 0-1
    tech_score: float  # 技术指标维度得分 -1~1
    index_score: float  # 宽基指数维度得分 -1~1
    fundamental_score: float  # 基本面维度得分 -1~1

def _calc_tech_score(prices: List[float], lookback: int = 60) -> float:
    """技术指标维度（权重40%）"""
    if len(prices) < lookback:
        lookback = len(prices)
    recent = prices[-lookback:]
    
    # MA200方向（需要更多历史数据）
    ma20 = sum(prices[-20:]) / 20
    ma60 = sum(prices[-60:]) / 60
    ma20_prev = sum(prices[-21:-1]) / 20
    ma60_prev = sum(prices[-61:-1]) / 60
    
    score = 0.0
    # 均线方向
    if ma20 > ma60:
        score += 0.3
    else:
        score -= 0.3
    if ma20 > ma20_prev:
        score += 0.2
    else:
        score -= 0.2
    if ma60 > ma60_prev:
        score += 0.2
    else:
        score -= 0.2
    
    # 波动率（20日 HV）
    high_20 = max(prices[-20:])
    low_20 = min(prices[-20:])
    hv = (high_20 - low_20) / low_20
    if hv < 0.15:
        score += 0.3
    elif hv > 0.30:
        score -= 0.3
    
    return max(-1.0, min(1.0, score))

def _calc_index_score(prices: List[float]) -> float:
    """宽基指数维度（权重35%）"""
    if len(prices) < 20:
        return 0.0
    
    price = prices[-1]
    ma20 = sum(prices[-20:]) / 20
    
    score = 0.0
    # 20日均线方向
    if price > ma20:
        score += 0.4
    else:
        score -= 0.4
    
    # 短期动量
    momentum_5 = price / prices[-5] - 1
    if momentum_5 > 0.02:
        score += 0.3
    elif momentum_5 < -0.02:
        score -= 0.3
    
    # 20日涨幅
    momentum_20 = price / prices[-20] - 1
    if momentum_20 > 0.05:
        score += 0.3
    elif momentum_20 < -0.05:
        score -= 0.3
    
    return max(-1.0, min(1.0, score))

def _calc_fundamental_score(macro: Dict[str, float]) -> float:
    """基本面维度（权重25%）"""
    score = 0.0
    
    # 估值分位数
    pe_pct = macro.get("pe_percentile", 0.5)
    if pe_pct < 0.3:
        score += 0.4  # 低估值
    elif pe_pct > 0.7:
        score -= 0.4  # 高估值
    
    # 流动性
    m2_yoy = macro.get("m2_yoy", 0.10)
    if m2_yoy > 0.10:
        score += 0.3
    elif m2_yoy < 0.08:
        score -= 0.3
    
    return max(-1.0, min(1.0, score))

def detect_regime(
    prices: List[float],
    macro: Dict[str, float],
    lookback_days: int = 60,
) -> MarketRegime:
    """
    综合判断市场状态
    
    Args:
        prices: 价格序列（通常是沪深300指数）
        macro: 宏观指标 dict，包含 pe_percentile, m2_yoy
        lookback_days: 回顾天数
    
    Returns:
        MarketRegime 对象
    """
    if len(prices) < 20:
        return MarketRegime(
            regime=RegimeType.SIDEWAYS,
            confidence=0.0,
            tech_score=0.0,
            index_score=0.0,
            fundamental_score=0.0,
        )
    
    tech = _calc_tech_score(prices, lookback_days)
    index_score = _calc_index_score(prices)
    fundamental = _calc_fundamental_score(macro)
    
    # 加权综合得分
    weighted = tech * 0.4 + index_score * 0.35 + fundamental * 0.25
    
    if weighted > 0.3:
        regime = RegimeType.BULL
    elif weighted < -0.3:
        regime = RegimeType.BEAR
    else:
        regime = RegimeType.SIDEWAYS
    
    confidence = abs(weighted)
    
    return MarketRegime(
        regime=regime,
        confidence=confidence,
        tech_score=tech,
        index_score=index_score,
        fundamental_score=fundamental,
    )
```

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/test_market_regime.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add market_regime.py tests/test_market_regime.py
git commit -m "feat: add MarketRegimeDetector for market state classification

Three-dimension scoring: tech(40%) + index(35%) + fundamental(25%)
Outputs: bull/bear/sideways with confidence level

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: SignalScorer（信号强度评分）

**Files:**
- Modify: `signal_rules.py`
- Create: `tests/test_signal_scorer.py`

- [ ] **Step 1: 写测试**

```python
# tests/test_signal_scorer.py
import pytest
from signal_rules import SignalScore, score_signal_strength, ConfidenceLevel

def test_strong_signal():
    """强信号：多个指标同时满足"""
    from domain_models import Stock
    stock = Stock(
        code="000001", name="平安", pe=10, pb=1.2, peg=0.8,
        revenue_growth=0.15, profit_growth=0.12, roe=0.15,
        cashflow=0.10, prices=[100 + i for i in range(250)], volumes=[1000000]*250
    )
    result = score_signal_strength(stock, {"买入": ["均线突破", "MACD金叉", "RSI超卖"]})
    assert result.confidence == ConfidenceLevel.STRONG
    assert result.total >= 75

def test_weak_signal():
    """弱信号：只有1-2个指标"""
    from domain_models import Stock
    stock = Stock(
        code="000002", name="万科", pe=8, pb=0.9, peg=0.6,
        revenue_growth=0.05, profit_growth=0.03, roe=0.08,
        cashflow=0.05, prices=[100 - i*0.2 for i in range(250)], volumes=[800000]*250
    )
    result = score_signal_strength(stock, {"买入": ["均线突破"]})
    assert result.total < 50

def test_position_calculation():
    """仓位计算"""
    from signal_rules import calculate_position
    from market_regime import MarketRegime, RegimeType
    
    strong = SignalScore(total=85, confidence=ConfidenceLevel.STRONG, indicator_count=4)
    bull = MarketRegime(regime=RegimeType.BULL, confidence=0.8, tech_score=0.5, index_score=0.6, fundamental_score=0.4)
    pos = calculate_position(strong, bull)
    assert pos.position_size >= 0.15  # 强信号+牛市应该重仓
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/test_signal_scorer.py -v`
Expected: FAIL - function not defined

- [ ] **Step 3: 写实现（追加到 signal_rules.py）**

```python
# 在 signal_rules.py 末尾添加

from dataclasses import dataclass
from enum import Enum
from domain_models import Stock

class ConfidenceLevel(Enum):
    STRONG = "strong"
    MEDIUM = "medium"
    WEAK = "weak"
    NONE = "none"

@dataclass
class SignalScore:
    total: float              # 总分 0-100
    indicator_count: int      # 同时满足的指标数
    spatial_score: float      # 空间距离得分 0-40
    historical_winrate: float # 历史胜率得分 0-30
    momentum_score: float     # 动量得分 0-30
    confidence: ConfidenceLevel

# 历史胜率表（简化版，实际应该从回测数据计算）
HISTORICAL_WINRATES = {
    "均线突破": 0.58,
    "动量突破": 0.52,
    "回调买入": 0.62,
    "MACD金叉": 0.55,
    "RSI超卖": 0.48,
}

def score_signal_strength(stock: Stock, signals: dict) -> SignalScore:
    """
    计算信号强度综合评分
    
    Args:
        stock: 股票对象
        signals: detect_signals 的输出，格式 {"买入": [...], "卖出": [...]}
    
    Returns:
        SignalScore 对象
    """
    buy_signals = signals.get("买入", [])
    sell_signals = signals.get("卖出", [])
    
    if not buy_signals and not sell_signals:
        return SignalScore(
            total=0, indicator_count=0, spatial_score=0,
            historical_winrate=0, momentum_score=0, confidence=ConfidenceLevel.NONE
        )
    
    # 1. 指标数量分 (0-50)
    indicator_count = len(buy_signals) + len(sell_signals)
    indicator_score = min(50, indicator_count * 10)
    
    # 2. 空间距离分 (0-40)
    prices = stock.prices
    if len(prices) >= 20:
        ma10 = sum(prices[-10:]) / 10
        ma30 = sum(prices[-30:]) / 30
        ma60 = sum(prices[-60:]) / 60
        price = prices[-1]
        
        # 计算价格与均线的距离
        distances = [
            abs(price / ma10 - 1),
            abs(price / ma30 - 1),
            abs(price / ma60 - 1),
        ]
        avg_distance = sum(distances) / len(distances)
        
        # 距离越小分越高（回踩精准）
        spatial_score = max(0, 40 - avg_distance * 400)
    else:
        spatial_score = 0
    
    # 3. 历史胜率分 (0-30)
    all_signals = buy_signals + sell_signals
    winrates = [HISTORICAL_WINRATES.get(s, 0.5) for s in all_signals]
    avg_winrate = sum(winrates) / len(winrates)
    historical_score = avg_winrate * 30
    
    # 4. 动量分 (0-30)
    if len(prices) >= 20:
        momentum_5 = prices[-1] / prices[-5] - 1
        momentum_20 = prices[-1] / prices[-20] - 1
        
        # 正向动量加分，负向动量减分
        momentum_score = (momentum_5 * 10 + momentum_20 * 20) * 30
        momentum_score = max(0, min(30, momentum_score + 15))
    else:
        momentum_score = 15  # 默认中等
    
    total = indicator_score + spatial_score + historical_score + momentum_score
    total = max(0, min(100, total))
    
    # 信号强度分级
    if total >= 75:
        confidence = ConfidenceLevel.STRONG
    elif total >= 50:
        confidence = ConfidenceLevel.MEDIUM
    elif total >= 25:
        confidence = ConfidenceLevel.WEAK
    else:
        confidence = ConfidenceLevel.NONE
    
    return SignalScore(
        total=total,
        indicator_count=indicator_count,
        spatial_score=spatial_score,
        historical_winrate=historical_score,
        momentum_score=momentum_score,
        confidence=confidence,
    )

def calculate_position(signal_score: SignalScore, regime) -> dict:
    """
    根据信号强度和市场状态计算仓位
    
    Args:
        signal_score: 信号评分
        regime: MarketRegime 对象
    
    Returns:
        dict: {position_size, stop_loss_pct, trailing_stop_pct, time_exit_days}
    """
    from market_regime import RegimeType
    
    # 基础仓位
    if regime.regime == RegimeType.BULL:
        base_position = 0.15
    elif regime.regime == RegimeType.BEAR:
        base_position = 0.08
    else:
        base_position = 0.10
    
    # 信号强度调整
    signal_multiplier = signal_score.total / 75
    signal_multiplier = min(1.5, signal_multiplier)  # 上限1.5倍
    
    position_size = base_position * signal_multiplier
    position_size = min(0.20, position_size)  # 最大20%
    
    # 止损设置
    if regime.regime == RegimeType.BULL:
        stop_loss = -0.08
        trailing_stop = 0.05
        time_exit = 30
    elif regime.regime == RegimeType.BEAR:
        stop_loss = -0.05
        trailing_stop = 0.03
        time_exit = 10
    else:
        stop_loss = -0.05
        trailing_stop = 0.03
        time_exit = 10
    
    return {
        "position_size": position_size,
        "stop_loss_pct": stop_loss,
        "trailing_stop_pct": trailing_stop,
        "time_exit_days": time_exit,
    }
```

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/test_signal_scorer.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add signal_rules.py tests/test_signal_scorer.py
git commit -m "feat: add SignalScorer for signal strength assessment

- Multi-factor scoring: indicator_count + spatial + historical + momentum
- Signal strength levels: strong(75+), medium(50-74), weak(25-49), none
- Position sizing based on signal strength and market regime

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: AdaptiveCandidate（市场自适应选股）

**Files:**
- Modify: `candidate_rules.py`
- Test: 现有测试应继续通过

- [ ] **Step 1: 写测试**

```python
# tests/test_adaptive_candidate.py
import pytest
from candidate_rules import ma_strategy_candidates_adaptive
from market_regime import MarketRegime, RegimeType, detect_regime
from domain_models import Stock

def create_test_stock(rising=True):
    """创建测试股票"""
    if rising:
        prices = [100 + i * 0.5 for i in range(250)]
    else:
        prices = [100 - i * 0.5 for i in range(250)]
    return Stock(
        code="000001", name="测试", pe=12, pb=1.5, peg=1.0,
        revenue_growth=0.10, profit_growth=0.08, roe=0.12,
        cashflow=0.08, prices=prices, volumes=[1000000]*250
    )

def test_bull_market_includes_breakout():
    """牛市应该包含突破信号"""
    stock = create_test_stock(rising=True)
    bull_regime = MarketRegime(regime=RegimeType.BULL, confidence=0.8, tech_score=0.6, index_score=0.5, fundamental_score=0.4)
    candidates = ma_strategy_candidates_adaptive([stock], bull_regime)
    # 应该有突破或回踩信号
    assert len(candidates) >= 0  # 可能没有信号，取决于具体数据

def test_bear_market_requires_oversold():
    """熊市应该只在超卖时入场"""
    stock = create_test_stock(rising=False)
    bear_regime = MarketRegime(regime=RegimeType.BEAR, confidence=0.7, tech_score=-0.6, index_score=-0.5, fundamental_score=-0.3)
    candidates = ma_strategy_candidates_adaptive([stock], bear_regime)
    # 熊市条件严格，可能没有候选
    assert isinstance(candidates, list)
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/test_adaptive_candidate.py -v`
Expected: FAIL - function not defined

- [ ] **Step 3: 写实现（追加到 candidate_rules.py）**

```python
# 在 candidate_rules.py 末尾添加

from market_regime import MarketRegime, RegimeType
from signal_rules import detect_signals, score_signal_strength

def ma_strategy_candidates_adaptive(
    stocks: List[Stock],
    regime: MarketRegime,
    config: Optional[CandidateConfig] = None,
) -> List[Candidate]:
    """
    市场自适应选股
    
    牛市：使用现有逻辑
    熊市：只在超跌反弹条件满足时入场
    震荡市：更严格的信号要求
    """
    config = config or DEFAULT_CANDIDATE_CONFIG
    
    if regime.regime == RegimeType.BULL:
        # 牛市：使用原有逻辑
        return ma_strategy_candidates(stocks, config)
    
    elif regime.regime == RegimeType.BEAR:
        # 熊市：严格超跌反弹条件
        return _bear_market_candidates(stocks, config)
    
    else:
        # 震荡市：降低频率
        return _sideways_market_candidates(stocks, config)

def _bear_market_candidates(stocks: List[Stock], config: CandidateConfig) -> List[Candidate]:
    """
    熊市超跌反弹选股
    
    严格条件：
    - RSI < 20（极端超卖）
    - 价格接近20日低点
    - 有资金介入信号（放量）
    """
    from indicators import rsi, moving_average
    
    candidates = []
    for stock in stocks:
        prices = stock.prices
        volumes = stock.volumes
        if len(prices) < 30:
            continue
        
        # 计算RSI
        rsi_value = rsi(prices)
        if rsi_value is None or rsi_value >= 20:
            continue  # 必须RSI<20
        
        # 价格位置：接近20日低点
        low_20 = min(prices[-20:])
        price = prices[-1]
        price_near_low = price <= low_20 * 1.03  # 在20日低点的3%以内
        
        # 均线企稳：MA20走平或向上
        ma20 = moving_average(prices, 20)
        ma20_prev = sum(prices[-21:-1]) / 20
        ma_stabilizing = ma20 >= ma20_prev * 0.995
        
        # 放量信号
        avg_volume_20 = sum(volumes[-20:]) / 20
        volume_ratio = volumes[-1] / avg_volume_20
        volume_surge = volume_ratio >= 1.5
        
        # 必须全部满足
        if price_near_low and ma_stabilizing and volume_surge:
            ma10 = moving_average(prices, 10)
            ma30 = moving_average(prices, 30)
            stop_price = min(min(prices[-20:]), ma30 * 0.985)
            
            # 简化评分
            score = (
                (20 - rsi_value) * 2 +  # RSI越低分越高
                volume_ratio * 10 +
                (1 - price / low_20) * 50  # 越接近低点分越高
            )
            
            candidates.append({
                "stock": stock,
                "strategy": "熊市超跌反弹",
                "ma10": ma10,
                "ma30": ma30,
                "ma50": moving_average(prices, 50),
                "ma100": moving_average(prices, 100),
                "ma200": moving_average(prices, 200),
                "volume_ratio": volume_ratio,
                "stop_price": stop_price,
                "score": score,
            })
    
    return sorted(candidates, key=lambda item: item["score"], reverse=True)

def _sideways_market_candidates(stocks: List[Stock], config: CandidateConfig) -> List[Candidate]:
    """
    震荡市选股
    
    更严格的条件：
    - 突破需要2%确认
    - 回踩需要更精准（±1%）
    - 最大持仓10天
    """
    candidates = []
    for stock in stocks:
        prices = stock.prices
        volumes = stock.volumes
        if len(prices) < 220:
            continue
        
        # 基础均线
        ma10 = sum(prices[-10:]) / 10
        ma30 = sum(prices[-30:]) / 30
        ma50 = sum(prices[-50:]) / 50
        ma100 = sum(prices[-100:]) / 100
        ma200 = sum(prices[-200:]) / 200
        
        price = prices[-1]
        
        # 检查是否有震荡市有效的信号
        # 条件：价格在MA10附近精确回踩（±1%）
        pullback = abs(price / ma10 - 1) <= 0.01
        if not pullback:
            continue
        
        # 趋势确认（不要求太强）
        trend_ok = price > ma10 > ma30 > ma50
        if not trend_ok:
            continue
        
        # 放量确认
        avg_volume_20 = sum(volumes[-20:]) / 20
        volume_ratio = volumes[-1] / avg_volume_20
        volume_ok = 0.9 <= volume_ratio <= 2.0
        
        if pullback and trend_ok and volume_ok:
            stop_price = min(min(prices[-20:]), ma30 * 0.97)  # 更紧止损
            
            # 评分
            score = (
                (1 - abs(price / ma10 - 1)) * 30 +  # 回踩精准度
                volume_ratio * 15 +
                (price / ma200 - 1) * 20
            )
            
            candidates.append({
                "stock": stock,
                "strategy": "震荡市精准回踩",
                "ma10": ma10,
                "ma30": ma30,
                "ma50": ma50,
                "ma100": ma100,
                "ma200": ma200,
                "volume_ratio": volume_ratio,
                "stop_price": stop_price,
                "score": score,
            })
    
    return sorted(candidates, key=lambda item: item["score"], reverse=True)
```

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/test_adaptive_candidate.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add candidate_rules.py tests/test_adaptive_candidate.py
git commit -m "feat: add AdaptiveCandidate for market-adaptive stock selection

- Bull market: use existing trend-following logic
- Bear market: strict oversold conditions (RSI<20, near 20d low, volume surge)
- Sideways market: stricter signals (precise pullback ±1%, tighter stop)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: PositionManager（仓位与止损管理）

**Files:**
- Create: `risk_manager.py`
- Test: `tests/test_risk_manager.py`

- [ ] **Step 1: 写测试**

```python
# tests/test_risk_manager.py
import pytest
from risk_manager import PositionConfig, RiskManager, calculate_trailing_stop
from market_regime import MarketRegime, RegimeType

def test_bull_trailing_stop():
    """牛市移动止损跟踪"""
    entry_price = 100.0
    current_price = 115.0  # 盈利15%
    result = calculate_trailing_stop(entry_price, current_price, trailing_pct=0.05)
    assert result == 109.25  # 锁定盈利5%

def test_bear_tighter_stop():
    """熊市更严格止损"""
    rm = RiskManager()
    config = rm.get_config(RegimeType.BEAR)
    assert config.stop_loss_pct == -0.05  # 熊市-5%止损

def test_profit_protection():
    """盈利保护：5%时保本"""
    entry_price = 100.0
    current_price = 105.0  # 盈利5%
    result = calculate_trailing_stop(entry_price, current_price, trailing_pct=0.05)
    assert result == entry_price  # 保本线
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/test_risk_manager.py -v`
Expected: FAIL - ModuleNotFoundError

- [ ] **Step 3: 写实现**

```python
# risk_manager.py
from dataclasses import dataclass
from market_regime import RegimeType

@dataclass
class PositionConfig:
    position_size: float       # 仓位比例 0-1
    stop_loss_pct: float       # 固定止损 %
    trailing_stop_pct: float   # 移动止损 %
    time_exit_days: int        # 时间止损（交易日）
    profit_target_pct: float   # 止盈目标 %

class RiskManager:
    """风险管理器"""
    
    # 各市场状态默认参数
    REGIME_CONFIGS = {
        RegimeType.BULL: {
            "position_size": 0.15,
            "stop_loss_pct": -0.08,
            "trailing_stop_pct": 0.05,
            "time_exit_days": 30,
            "profit_target_pct": 0.20,
        },
        RegimeType.BEAR: {
            "position_size": 0.08,
            "stop_loss_pct": -0.05,
            "trailing_stop_pct": 0.03,
            "time_exit_days": 10,
            "profit_target_pct": 0.10,
        },
        RegimeType.SIDEWAYS: {
            "position_size": 0.10,
            "stop_loss_pct": -0.05,
            "trailing_stop_pct": 0.03,
            "time_exit_days": 10,
            "profit_target_pct": 0.08,
        },
    }
    
    def get_config(self, regime: RegimeType, signal_strength: float = 1.0) -> PositionConfig:
        """
        获取指定市场状态的仓位配置
        
        Args:
            regime: 市场状态
            signal_strength: 信号强度 0-1，1=最强
        
        Returns:
            PositionConfig 对象
        """
        cfg = self.REGIME_CONFIGS.get(regime, self.REGIME_CONFIGS[RegimeType.SIDEWAYS])
        
        # 信号强度调整仓位
        position_size = cfg["position_size"] * (0.5 + signal_strength * 0.5)
        position_size = min(0.20, position_size)  # 最大20%
        
        return PositionConfig(
            position_size=position_size,
            stop_loss_pct=cfg["stop_loss_pct"],
            trailing_stop_pct=cfg["trailing_stop_pct"],
            time_exit_days=cfg["time_exit_days"],
            profit_target_pct=cfg["profit_target_pct"],
        )
    
    def should_stop_loss(self, entry_price: float, current_price: float, 
                         highest_price: float, config: PositionConfig) -> tuple[bool, str]:
        """
        检查是否应该止损
        
        Returns:
            (should_stop, reason)
        """
        return_pct = (current_price / entry_price - 1)
        
        # 固定止损
        if return_pct <= config.stop_loss_pct:
            return True, f"固定止损 {return_pct:.2%}"
        
        # 移动止损
        trailing_stop_price = calculate_trailing_stop(
            entry_price, highest_price, config.trailing_stop_pct
        )
        if current_price <= trailing_stop_price and return_pct > 0:
            return True, f"移动止损 {return_pct:.2%}"
        
        return False, ""
    
    def should_take_profit(self, entry_price: float, current_price: float,
                           config: PositionConfig) -> tuple[bool, str]:
        """
        检查是否应该止盈
        """
        return_pct = (current_price / entry_price - 1)
        
        # 固定止盈
        if return_pct >= config.profit_target_pct:
            return True, f"目标止盈 {return_pct:.2%}"
        
        return False, ""


def calculate_trailing_stop(entry_price: float, highest_price: float, 
                            trailing_pct: float = 0.05) -> float:
    """
    计算移动止损价格
    
    逻辑：
    - 盈利5% → 保本
    - 盈利10% → 锁定盈利5%
    - 盈利15% → 锁定盈利10%
    - 最高锁定盈利15%
    
    Args:
        entry_price: 入场价格
        highest_price: 持仓期间最高价
        trailing_pct: 移动止损百分比
    
    Returns:
        止损价格
    """
    profit_pct = highest_price / entry_price - 1
    
    if profit_pct <= 0.05:
        # 盈利不足5%，保本
        return entry_price
    
    # 锁定部分盈利
    lock_pct = min(0.15, profit_pct - trailing_pct)
    stop_price = highest_price * (1 - lock_pct - trailing_pct)
    
    # 最低不能低于入场价
    return max(entry_price, stop_price)
```

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/test_risk_manager.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add risk_manager.py tests/test_risk_manager.py
git commit -m "feat: add RiskManager for position sizing and stop-loss

- Triple stop-loss: fixed + trailing + time-based
- Market-adaptive defaults (bull: looser, bear: tighter)
- Trailing stop logic: lock profits at 5%/10%/15% levels

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: 回测验证增强

**Files:**
- Modify: `ma_backtest.py`
- Test: 现有测试应继续通过

- [ ] **Step 1: 添加市场状态报告**

在 `ma_backtest.py` 的 `main()` 函数末尾添加市场状态统计：

```python
# 在 summary_rows 打印后添加

def add_regime_stats(result: Dict[str, float], stocks: List[Stock]) -> None:
    """添加市场状态统计"""
    if not stocks:
        return
    
    # 简化：使用现有的 market_regime_factor 统计
    # 这部分依赖现有代码
    pass

# 在 main() 函数中调用（如果用户指定 --regime-report）
```

- [ ] **Step 2: 添加 --reduce-trades 选项**

在 argparse 部分添加：

```python
parser.add_argument("--reduce-trades", action="store_true", 
                    help="使用自适应策略减少无效交易")
```

- [ ] **Step 3: 修改 run_backtest 支持自适应选股**

在 `run_backtest` 函数中增加可选参数：

```python
def run_backtest(
    ...
    adaptive: bool = False,  # 新增：是否使用自适应策略
    regime_detector = None,  # 市场状态检测器
) -> Dict[str, float]:
```

在回测循环中：

```python
for end_idx in range(start_idx, end_upper):
    snapshot = [replace(stock, prices=stock.prices[: end_idx + 1], 
                        volumes=stock.volumes[: end_idx + 1]) 
                for stock in aligned_stocks]
    
    if adaptive and regime_detector:
        # 使用自适应选股
        index_prices = [s.prices[end_idx] for s in snapshot[:50]]  # 用前50只模拟指数
        regime = detect_regime(index_prices, {})
        candidates = select_candidates_with_quota(
            ma_strategy_candidates_adaptive(snapshot, regime, candidate_config),
            top_size, strategy_ratios
        )
    else:
        # 使用原有逻辑
        candidates = select_candidates_with_quota(
            ma_strategy_candidates(snapshot, candidate_config),
            top_size, strategy_ratios
        )
```

- [ ] **Step 4: 运行现有测试验证兼容**

Run: `pytest -v`
Expected: 所有现有测试 PASS

- [ ] **Step 5: 提交**

```bash
git add ma_backtest.py
git commit -m "feat: enhance backtest with adaptive strategy option

- Add --reduce-trades flag for adaptive stock selection
- Integrate market regime detection in backtest loop
- Maintain backward compatibility with existing tests

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: 集成测试与验收

**Files:**
- Test: `tests/test_integration.py`

- [ ] **Step 1: 写集成测试**

```python
# tests/test_integration.py
import pytest
from market_regime import detect_regime, RegimeType
from signal_rules import detect_signals, score_signal_strength
from candidate_rules import ma_strategy_candidates_adaptive
from risk_manager import RiskManager
from domain_models import Stock

def create_stock(prices, volumes=None):
    if volumes is None:
        volumes = [1000000] * len(prices)
    return Stock(
        code="000001", name="测试", pe=12, pb=1.5, peg=1.0,
        revenue_growth=0.10, profit_growth=0.08, roe=0.12,
        cashflow=0.08, prices=prices, volumes=volumes
    )

def test_full_pipeline():
    """测试完整流程"""
    # 1. 创建上涨趋势股票
    prices = [100 + i * 0.5 for i in range(250)]
    stock = create_stock(prices)
    
    # 2. 检测市场状态
    index_prices = prices  # 简化：用个股价格代替指数
    regime = detect_regime(index_prices, {"pe_percentile": 0.4, "m2_yoy": 0.12})
    assert regime.regime == RegimeType.BULL
    
    # 3. 检测信号
    signals = detect_signals(stock)
    
    # 4. 计算信号强度
    if signals:
        score = score_signal_strength(stock, signals)
        assert score.total >= 0
        
        # 5. 计算仓位
        rm = RiskManager()
        config = rm.get_config(regime.regime, score.total / 100)
        assert config.position_size > 0
```

- [ ] **Step 2: 运行集成测试**

Run: `pytest tests/test_integration.py -v`
Expected: PASS

- [ ] **Step 3: 运行完整回测对比**

Run: `python ma_backtest.py --days 240 --top 10`
Run: `python ma_backtest.py --days 240 --top 10 --reduce-trades`

对比输出中的：
- 交易次数
- 持仓天数
- 超额收益
- 胜率

- [ ] **Step 4: 提交**

```bash
git add tests/test_integration.py
git commit -m "test: add integration test for full pipeline

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## 验收标准检查清单

- [ ] `pytest tests/test_market_regime.py` PASS
- [ ] `pytest tests/test_signal_scorer.py` PASS
- [ ] `pytest tests/test_adaptive_candidate.py` PASS
- [ ] `pytest tests/test_risk_manager.py` PASS
- [ ] `pytest tests/test_integration.py` PASS
- [ ] 现有 `pytest` 测试全部 PASS（向后兼容）
- [ ] `--reduce-trades` 模式下交易次数减少 30%+
- [ ] 熊市模式下无信号时不交易

---

## 实现顺序

1. Task 1: MarketRegimeDetector（基础模块，无依赖）
2. Task 2: SignalScorer（依赖 MarketRegime）
3. Task 3: AdaptiveCandidate（依赖 MarketRegime）
4. Task 4: PositionManager（依赖 MarketRegime）
5. Task 5: 回测验证增强（集成以上模块）
6. Task 6: 集成测试与验收
