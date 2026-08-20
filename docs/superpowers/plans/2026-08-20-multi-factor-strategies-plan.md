# 多因子选股策略 + 回测引擎实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现两个多因子选股策略(医药多因子、高股息低波)及完整回测引擎

**Architecture:** 三层架构: 策略层(选股信号) → 回测引擎层(订单执行、仓位管理) → 业绩分析层(指标计算、可视化)

**Tech Stack:** Python, pandas, matplotlib, numpy, SQLite

**Spec:** docs/superpowers/specs/2026-08-20-multi-factor-strategies-design.md

---

## 文件结构

```
strategies/
├── multi_factor_base.py       # 新增: 多因子框架基类
├── pharma_multi_factor.py      # 新增: 医药多因子策略
└── dividend_multi_factor.py   # 新增: 高股息低波策略

backtest/
├── __init__.py                # 新增
├── config.py                  # 新增: BacktestConfig
├── models.py                   # 新增: Order, Trade, Position, Portfolio
├── cost_calculator.py          # 新增: 交易成本计算
├── order_executor.py           # 新增: 订单执行(T+1、涨跌停)
├── engine.py                   # 新增: 回测引擎主循环
├── performance.py              # 新增: 业绩分析器
└── visualization.py            # 新增: 可视化

domain_models.py               # 修改: 扩展Stock模型
indicators.py                  # 修改: 添加z_score_normalize
```

---

## Task 1: 数据模型扩展

**Files:**
- Modify: `domain_models.py` (扩展Stock类)
- Modify: `indicators.py` (添加z_score标准化)

**Interfaces:**
- Produces: `Stock` 带新字段, `z_score_normalize()`

- [ ] **Step 1: 添加z_score_normalize到indicators.py**

```python
def z_score_normalize(values: List[float], higher_is_better: bool = True) -> List[float]:
    """Z-score标准化, 返回0-100范围便于组合"""
    if len(values) < 2:
        return [50.0] * len(values)
    mean = sum(values) / len(values)
    variance = sum((x - mean) ** 2 for x in values) / len(values)
    std = variance ** 0.5
    if std < 1e-10:
        return [50.0] * len(values)
    z_scores = [(x - mean) / std for x in values]
    min_z, max_z = min(z_scores), max(z_scores)
    if max_z - min_z < 1e-10:
        return [50.0] * len(values)
    normalized = [(z - min_z) / (max_z - min_z) * 100 for z in z_scores]
    if not higher_is_better:
        normalized = [100 - n for n in normalized]
    return normalized
```

- [ ] **Step 2: 扩展domain_models.py的Stock类**

```python
from dataclasses import dataclass, field
from typing import List

@dataclass
class Stock:
    # === 现有字段 ===
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

    # === 新增: 行业分类 ===
    sector: str = ""
    sub_sector: str = ""

    # === 新增: 价值因子 ===
    ev_ebitda: float = 0.0

    # === 新增: 质量因子 ===
    cash_div_ratio: float = 0.0
    gross_margin_std: float = 0.0
    gross_margin: float = 0.0
    debt_ratio: float = 0.0

    # === 新增: 成长因子 ===
    revenue_cagr_3y: float = 0.0
    profit_cagr_3y: float = 0.0
    rd_expense_ratio: float = 0.0

    # === 新增: 动量/低波因子 ===
    volatility_120d: float = 0.0
    max_drawdown_1y: float = 0.0
    momentum_12m_1m: float = 0.0
    excess_momentum: float = 0.0

    # === 新增: 分红因子 ===
    dividend_yield: float = 0.0
    dividend_stability: float = 0.0
    dividend_payout_ratio: float = 0.0

    # === 新增: 历史数据 ===
    gross_margins_hist: List[float] = field(default_factory=list)
```

- [ ] **Step 3: 验证修改**

Run: `cd /Users/shichaopeng/Work/self-dir/projects/a-finder-cli && python -c "from domain_models import Stock; s = Stock(code='000001', name='test', pe=10, pb=1.5, peg=1, revenue_growth=0.1, profit_growth=0.1, roe=0.15, cashflow=0.1, prices=[10,11,12], volumes=[1000,1000,1000], sector='医药生物'); print(f'sector={s.sector}, dividend_yield={s.dividend_yield}')"`

Expected: `sector=医药生物, dividend_yield=0.0`

---

## Task 2: 多因子基础框架

**Files:**
- Create: `strategies/multi_factor_base.py`

**Interfaces:**
- Consumes: `Stock`, `z_score_normalize()`
- Produces: `MultiFactorConfig`, `MultiFactorBase`, `TargetPosition`, `SelectionResult`

- [ ] **Step 1: 创建strategies/multi_factor_base.py**

```python
from dataclasses import dataclass, field
from typing import Dict, List, Callable, Optional
from datetime import date
from enum import Enum
from domain_models import Stock
from indicators import z_score_normalize

class FactorDirection(Enum):
    HIGHER_IS_BETTER = 1
    LOWER_IS_BETTER = -1

@dataclass
class FactorConfig:
    name: str
    weight: float
    direction: FactorDirection
    get_value: Callable[[Stock], float]

@dataclass
class MultiFactorConfig:
    name: str
    factors: List[FactorConfig]
    top_n: int = 30
    max_weight: float = 0.05
    sector_limits: Dict[str, float] = field(default_factory=dict)
    sub_sector_limits: Dict[str, float] = field(default_factory=dict)
    rebalance_freq: str = "monthly"

    def validate(self) -> bool:
        total = sum(f.weight for f in self.factors)
        return abs(total - 1.0) < 1e-6

@dataclass
class TargetPosition:
    code: str
    name: str
    weight: float
    score: float
    sector: str = ""
    sub_sector: str = ""

@dataclass
class SelectionResult:
    date: date
    positions: List[TargetPosition]
    excluded: List[Dict] = field(default_factory=list)
    rebalance_reason: str = ""

class MultiFactorBase:
    def __init__(self, config: MultiFactorConfig):
        self.config = config

    def select(self, date: date, candidates: List[Stock]) -> SelectionResult:
        # 1. 过滤候选
        filtered = self._filter_candidates(candidates)
        if len(filtered) < self.config.top_n:
            filtered = candidates[:self.config.top_n]
        
        # 2. 计算Z-score
        z_scores = self._calculate_z_scores(filtered)
        
        # 3. 计算综合得分
        scores = self._calculate_composite_score(z_scores)
        
        # 4. 构建持仓
        positions = []
        for stock, score in zip(filtered, scores):
            positions.append(TargetPosition(
                code=stock.code,
                name=stock.name,
                weight=0.0,  # 待分配
                score=score,
                sector=stock.sector,
                sub_sector=stock.sub_sector
            ))
        
        # 5. 排序并应用约束
        positions.sort(key=lambda p: p.score, reverse=True)
        positions = self._apply_sector_constraints(positions)
        positions = self._rebalance_weights(positions)
        
        return SelectionResult(date=date, positions=positions[:self.config.top_n])

    def _filter_candidates(self, stocks: List[Stock]) -> List[Stock]:
        """子类可重写"""
        return [s for s in stocks if s.prices and len(s.prices) >= 120]

    def _calculate_z_scores(self, stocks: List[Stock]) -> Dict[str, List[float]]:
        result = {}
        for factor in self.config.factors:
            values = [factor.get_value(s) for s in stocks]
            higher = factor.direction == FactorDirection.HIGHER_IS_BETTER
            result[factor.name] = z_score_normalize(values, higher)
        return result

    def _calculate_composite_score(self, z_scores: Dict[str, List[float]]) -> List[float]:
        n = len(list(z_scores.values())[0])
        weights = {f.name: f.weight for f in self.config.factors}
        scores = [0.0] * n
        for name, values in z_scores.items():
            w = weights.get(name, 0)
            for i, v in enumerate(values):
                scores[i] += v * w
        return scores

    def _apply_sector_constraints(self, positions: List[TargetPosition]) -> List[TargetPosition]:
        """应用行业权重上限"""
        # 简化: 暂不实现,后续扩展
        return positions

    def _rebalance_weights(self, positions: List[TargetPosition]) -> List[TargetPosition]:
        """等权分配权重"""
        n = min(len(positions), self.config.top_n)
        weight = 1.0 / n if n > 0 else 0
        for p in positions[:n]:
            p.weight = min(weight, self.config.max_weight)
        return positions[:n]
```

- [ ] **Step 2: 验证**

Run: `cd /Users/shichaopeng/Work/self-dir/projects/a-finder-cli && python -c "
from strategies.multi_factor_base import MultiFactorConfig, MultiFactorBase, FactorConfig, FactorDirection
from domain_models import Stock
from datetime import date

# Mock因子
def get_pe(s): return s.pe
def get_roe(s): return s.roe

config = MultiFactorConfig(
    name='test',
    factors=[
        FactorConfig('pe', 0.5, FactorDirection.LOWER_IS_BETTER, get_pe),
        FactorConfig('roe', 0.5, FactorDirection.HIGHER_IS_BETTER, get_roe),
    ],
    top_n=10
)

stocks = [
    Stock(code='1', name='A', pe=10, pb=1, peg=1, revenue_growth=0.1, profit_growth=0.1, roe=0.15, cashflow=0.1, prices=[10]*120, volumes=[1000]*120),
    Stock(code='2', name='B', pe=20, pb=2, peg=2, revenue_growth=0.2, profit_growth=0.2, roe=0.20, cashflow=0.2, prices=[20]*120, volumes=[1000]*120),
]

strategy = MultiFactorBase(config)
result = strategy.select(date(2024,1,1), stocks)
print(f'positions: {len(result.positions)}')
print(f'scores: {[p.score for p in result.positions]}')
"`

Expected: 输出positions和scores

---

## Task 3: 医药多因子策略

**Files:**
- Create: `strategies/pharma_multi_factor.py`

**Interfaces:**
- Consumes: `MultiFactorBase`
- Produces: `PharmaMultiFactorStrategy`

- [ ] **Step 1: 创建策略**

```python
from typing import List, Dict
from datetime import date
from domain_models import Stock
from strategies.multi_factor_base import (
    MultiFactorConfig, MultiFactorBase, FactorConfig, 
    FactorDirection, SelectionResult, TargetPosition
)

# 因子配置
PHARMA_FACTORS = [
    # 价值(30%): PE行业分位, PB行业分位, EV/EBITDA
    FactorConfig('pe_rank', 0.10, FactorDirection.LOWER_IS_BETTER, lambda s: s.pe),
    FactorConfig('pb_rank', 0.10, FactorDirection.LOWER_IS_BETTER, lambda s: s.pb),
    FactorConfig('ev_ebitda', 0.10, FactorDirection.LOWER_IS_BETTER, lambda s: s.ev_ebitda),
    # 质量(30%): ROE, 现金流/净利润, 毛利率稳定性
    FactorConfig('roe', 0.12, FactorDirection.HIGHER_IS_BETTER, lambda s: s.roe),
    FactorConfig('cash_div_ratio', 0.10, FactorDirection.HIGHER_IS_BETTER, lambda s: s.cash_div_ratio),
    FactorConfig('gross_margin_std', 0.08, FactorDirection.LOWER_IS_BETTER, lambda s: s.gross_margin_std),
    # 成长(20%): 营收CAGR, 利润CAGR, 研发费用率
    FactorConfig('revenue_cagr', 0.08, FactorDirection.HIGHER_IS_BETTER, lambda s: s.revenue_cagr_3y),
    FactorConfig('profit_cagr', 0.07, FactorDirection.HIGHER_IS_BETTER, lambda s: s.profit_cagr_3y),
    FactorConfig('rd_expense', 0.05, FactorDirection.HIGHER_IS_BETTER, lambda s: s.rd_expense_ratio),
    # 动量(10%): 12m-1m, 超额动量
    FactorConfig('momentum', 0.06, FactorDirection.HIGHER_IS_BETTER, lambda s: s.momentum_12m_1m),
    FactorConfig('excess_momentum', 0.04, FactorDirection.HIGHER_IS_BETTER, lambda s: s.excess_momentum),
    # 低波(10%): 波动率, 最大回撤
    FactorConfig('volatility', 0.05, FactorDirection.LOWER_IS_BETTER, lambda s: s.volatility_120d),
    FactorConfig('max_dd', 0.05, FactorDirection.LOWER_IS_BETTER, lambda s: s.max_drawdown_1y),
]

@dataclass
class PharmaMultiFactorConfig(MultiFactorConfig):
    name: str = "医药多因子价值+质量"
    sector: str = "医药生物"
    top_n: int = 25
    max_weight: float = 0.05
    rebalance_freq: str = "monthly"

class PharmaMultiFactorStrategy(MultiFactorBase):
    def __init__(self, config: PharmaMultiFactorConfig = None):
        config = config or PharmaMultiFactorConfig(factors=PHARMA_FACTORS)
        super().__init__(config)

    def _filter_candidates(self, stocks: List[Stock]) -> List[Stock]:
        filtered = []
        for s in stocks:
            # 过滤条件
            if s.sector != self.config.sector:
                continue
            if not s.prices or len(s.prices) < 365:  # 需1年数据
                continue
            # 可扩展: ST、流动性等
            filtered.append(s)
        return filtered

    def select(self, date: date, candidates: List[Stock]) -> SelectionResult:
        filtered = self._filter_candidates(candidates)
        if len(filtered) < 5:
            filtered = [c for c in candidates if c.prices and len(c.prices) >= 120][:20]
        
        result = super().select(date, filtered)
        result.rebalance_reason = f"月度调仓, 候选{len(filtered)}只, 持仓{len(result.positions)}只"
        return result
```

- [ ] **Step 2: 验证**

```bash
python -c "
from strategies.pharma_multi_factor import PharmaMultiFactorStrategy, PharmaMultiFactorConfig
from domain_models import Stock
from datetime import date

stocks = [
    Stock(code='1', name='恒瑞医药', pe=50, pb=8, peg=2, revenue_growth=0.1, profit_growth=0.05, roe=0.15, cashflow=0.1, prices=[50]*250, volumes=[1000]*250, sector='医药生物', sub_sector='化学制药'),
    Stock(code='2', name='药明康德', pe=60, pb=10, peg=3, revenue_growth=0.3, profit_growth=0.25, roe=0.20, cashflow=0.15, prices=[100]*250, volumes=[1000]*250, sector='医药生物', sub_sector='医疗服务'),
]

strategy = PharmaMultiFactorStrategy()
result = strategy.select(date(2024,6,1), stocks)
print(f'策略: {result.positions[0].name if result.positions else \"无\"}')
"
```

---

## Task 4: 高股息低波策略

**Files:**
- Create: `strategies/dividend_multi_factor.py`

**Interfaces:**
- Consumes: `MultiFactorBase`
- Produces: `DividendMultiFactorStrategy`

- [ ] **Step 1: 创建策略**

```python
from typing import List
from domain_models import Stock
from strategies.multi_factor_base import (
    MultiFactorConfig, MultiFactorBase, FactorConfig, 
    FactorDirection
)

# 因子配置
DIVIDEND_FACTORS = [
    # 股息(40%): 股息率, 分红稳定性
    FactorConfig('dividend_yield', 0.25, FactorDirection.HIGHER_IS_BETTER, lambda s: s.dividend_yield),
    FactorConfig('dividend_stability', 0.15, FactorDirection.HIGHER_IS_BETTER, lambda s: s.dividend_stability),
    # 低波(25%): 波动率, 最大回撤
    FactorConfig('volatility', 0.15, FactorDirection.LOWER_IS_BETTER, lambda s: s.volatility_120d),
    FactorConfig('max_dd', 0.10, FactorDirection.LOWER_IS_BETTER, lambda s: s.max_drawdown_1y),
    # 估值(20%): PE, PB, 股息率溢价
    FactorConfig('pe', 0.10, FactorDirection.LOWER_IS_BETTER, lambda s: s.pe),
    FactorConfig('pb', 0.05, FactorDirection.LOWER_IS_BETTER, lambda s: s.pb),
    FactorConfig('cash_div_ratio', 0.05, FactorDirection.HIGHER_IS_BETTER, lambda s: s.cash_div_ratio),
    # 质量(15%): ROE, 资产负债率, 现金流
    FactorConfig('roe', 0.07, FactorDirection.HIGHER_IS_BETTER, lambda s: s.roe),
    FactorConfig('debt_ratio', 0.04, FactorDirection.LOWER_IS_BETTER, lambda s: s.debt_ratio),
    FactorConfig('cashflow', 0.04, FactorDirection.HIGHER_IS_BETTER, lambda s: s.cashflow),
]

class DividendMultiFactorConfig(MultiFactorConfig):
    name: str = "高股息+低波防御"
    sectors: List[str] = None
    top_n: int = 40
    max_weight: float = 0.04
    rebalance_freq: str = "quarterly"

    def __post_init__(self):
        self.sectors = self.sectors or ["银行", "保险", "公用事业", "中药"]

class DividendMultiFactorStrategy(MultiFactorBase):
    def __init__(self, config: DividendMultiFactorConfig = None):
        config = config or DividendMultiFactorConfig(factors=DIVIDEND_FACTORS)
        super().__init__(config)

    def _filter_candidates(self, stocks: List[Stock]) -> List[Stock]:
        filtered = []
        for s in stocks:
            # 过滤: 股息率为0/负
            if s.dividend_yield <= 0:
                continue
            # 过滤: 资产负债率过高
            if s.debt_ratio > 0.9:
                continue
            # 可扩展: 3年净利润为负、分红率>100%
            filtered.append(s)
        return filtered
```

- [ ] **Step 2: 验证**

```bash
python -c "
from strategies.dividend_multi_factor import DividendMultiFactorStrategy
from domain_models import Stock
from datetime import date

stocks = [
    Stock(code='1', name='长江电力', pe=20, pb=2, peg=1, revenue_growth=0.05, profit_growth=0.05, roe=0.15, cashflow=0.2, prices=[25]*250, volumes=[1000]*250, sector='公用事业', dividend_yield=0.04, debt_ratio=0.5),
    Stock(code='2', name='工商银行', pe=5, pb=0.7, peg=0.5, revenue_growth=0.03, profit_growth=0.03, roe=0.12, cashflow=0.15, prices=[5]*250, volumes=[1000]*250, sector='银行', dividend_yield=0.05, debt_ratio=0.9),
]

strategy = DividendMultiFactorStrategy()
result = strategy.select(date(2024,6,1), stocks)
print(f'选中: {[p.name for p in result.positions]}')
"
```

---

## Task 5: 回测数据模型

**Files:**
- Create: `backtest/__init__.py`
- Create: `backtest/config.py`
- Create: `backtest/models.py`

- [ ] **Step 1: backtest/__init__.py**

```python
from backtest.config import BacktestConfig, MarketData
from backtest.models import Order, Trade, Position, Portfolio, DailyRecord, BacktestResult
```

- [ ] **Step 2: backtest/config.py**

```python
from dataclasses import dataclass
from typing import Dict, Set, Optional

@dataclass
class BacktestConfig:
    initial_cash: float = 1_000_000.0
    commission_rate: float = 0.00025
    stamp_tax_rate: float = 0.001
    slippage_rate: float = 0.001
    enable_T1: bool = True
    allow_limit_up_buy: bool = False
    allow_limit_down_sell: bool = False
    max_single_position: float = 0.10
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    price_type: str = "close"

@dataclass
class MarketData:
    date
    close_prices: Dict[str, float]
    open_prices: Dict[str, float]
    suspended: Set[str]
    limit_up: Set[str]
    limit_down: Set[str]
```

- [ ] **Step 3: backtest/models.py**

```python
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import date
from enum import Enum

class OrderDirection(Enum):
    BUY = "BUY"
    SELL = "SELL"

class OrderType(Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"

@dataclass
class Order:
    order_id: str
    date: date
    code: str
    direction: OrderDirection
    order_type: OrderType
    price: float = 0.0
    target_quantity: int = 0
    filled_quantity: int = 0
    status: str = "PENDING"
    reason: str = ""

@dataclass
class Trade:
    trade_id: str
    order_id: str
    date: date
    code: str
    direction: OrderDirection
    price: float
    quantity: int
    commission: float
    stamp_tax: float
    net_amount: float

@dataclass
class Position:
    code: str
    name: str = ""
    quantity: int = 0
    avg_cost: float = 0.0
    current_price: float = 0.0
    current_value: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0
    buy_date: Optional[date] = None

@dataclass
class Portfolio:
    cash: float = 0.0
    positions: Dict[str, Position] = field(default_factory=dict)
    total_value: float = 0.0

    def update(self, prices: Dict[str, float]):
        self.total_value = self.cash
        for code, pos in self.positions.items():
            if code in prices:
                pos.current_price = prices[code]
                pos.current_value = pos.quantity * pos.current_price
                pos.unrealized_pnl = pos.current_value - pos.quantity * pos.avg_cost
                if pos.avg_cost > 0:
                    pos.unrealized_pnl_pct = pos.unrealized_pnl / (pos.quantity * pos.avg_cost)
            self.total_value += pos.current_value

@dataclass
class DailyRecord:
    date: date
    portfolio: Portfolio
    trades: List[Trade] = field(default_factory=list)
    signals: List[str] = field(default_factory=list)

@dataclass
class BacktestResult:
    config: BacktestConfig
    start_date: date
    end_date: date
    initial_cash: float
    final_value: float = 0.0
    daily_records: List[DailyRecord] = field(default_factory=list)
    all_trades: List[Trade] = field(default_factory=list)
```

---

## Task 6: 回测引擎核心

**Files:**
- Create: `backtest/engine.py`
- Create: `backtest/cost_calculator.py`
- Create: `backtest/order_executor.py`

- [ ] **Step 1: backtest/cost_calculator.py**

```python
from backtest.config import BacktestConfig
from backtest.models import OrderDirection

def calculate_costs(direction: OrderDirection, price: float, quantity: int, config: BacktestConfig):
    amount = price * quantity
    commission = amount * config.commission_rate
    stamp_tax = amount * config.stamp_tax_rate if direction == OrderDirection.SELL else 0.0
    slippage = amount * config.slippage_rate
    return commission, stamp_tax, slippage
```

- [ ] **Step 2: backtest/order_executor.py**

```python
from typing import List, Tuple, Set, Dict
from datetime import date
from backtest.config import BacktestConfig, MarketData
from backtest.models import Order, Trade, Position, Portfolio, OrderDirection

class OrderExecutor:
    def __init__(self, config: BacktestConfig):
        self.config = config
        self.hold_history: Dict[str, List[date]] = {}

    def execute(self, orders: List[Order], market_data: MarketData, 
                portfolio: Portfolio, exec_date: date) -> Tuple[List[Trade], List[Order]]:
        executed, rejected = [], []
        trade_counter = 0
        
        for order in orders:
            trade, updated_order = self._execute_single(order, market_data, portfolio, exec_date, trade_counter)
            trade_counter += 1
            if trade:
                executed.append(trade)
            else:
                rejected.append(updated_order)
        
        return executed, rejected

    def _execute_single(self, order: Order, market_data: MarketData, 
                        portfolio: Portfolio, exec_date: date, trade_id: int):
        code = order.code
        
        # 停牌检查
        if code in market_data.suspended:
            order.status = "REJECTED"
            order.reason = "停牌"
            return None, order
        
        # 获取价格
        if code not in market_data.close_prices:
            order.status = "REJECTED"
            order.reason = "无价格"
            return None, order
        
        exec_price = market_data.close_prices[code]
        
        # 涨跌停检查
        if order.direction == OrderDirection.BUY:
            if code in market_data.limit_up and not self.config.allow_limit_up_buy:
                order.status = "REJECTED"
                order.reason = "涨停"
                return None, order
            if code in self.hold_history:
                if self.hold_history[code] and self.hold_history[code][-1] == exec_date:
                    order.status = "REJECTED"
                    order.reason = "T+1"
                    return None, order
        elif order.direction == OrderDirection.SELL:
            if code in market_data.limit_down and not self.config.allow_limit_down_sell:
                order.status = "REJECTED"
                order.reason = "跌停"
                return None, order
        
        # 应用滑点
        if order.direction == OrderDirection.BUY:
            exec_price *= (1 + self.config.slippage_rate)
        else:
            exec_price *= (1 - self.config.slippage_rate)
        
        quantity = order.target_quantity
        from backtest.cost_calculator import calculate_costs
        commission, stamp_tax, slippage = calculate_costs(order.direction, exec_price, quantity, self.config)
        
        trade = Trade(
            trade_id=f"T{trade_id}",
            order_id=order.order_id,
            date=exec_date,
            code=code,
            direction=order.direction,
            price=exec_price,
            quantity=quantity,
            commission=commission,
            stamp_tax=stamp_tax,
            net_amount=exec_price * quantity - commission - stamp_tax
        )
        
        order.status = "FILLED"
        order.filled_quantity = quantity
        
        return trade, order
```

- [ ] **Step 3: backtest/engine.py**

```python
import copy
from typing import List, Callable, Optional, Dict
from datetime import date
from backtest.config import BacktestConfig, MarketData
from backtest.models import Order, Trade, Position, Portfolio, DailyRecord, BacktestResult, OrderDirection, OrderType
from backtest.order_executor import OrderExecutor
from strategies.multi_factor_base import MultiFactorBase

class BacktestEngine:
    def __init__(self, config: BacktestConfig, strategy: MultiFactorBase,
                 market_data_provider: Callable[[date], MarketData]):
        self.config = config
        self.strategy = strategy
        self.market_data_provider = market_data_provider
        self.executor = OrderExecutor(config)

    def run(self, start_date: date, end_date: date, 
            trade_dates: List[date], stock_pool: List[Stock],
            benchmark_data: Optional[Dict[date, float]] = None) -> BacktestResult:
        result = BacktestResult(
            config=self.config,
            start_date=start_date,
            end_date=end_date,
            initial_cash=self.config.initial_cash,
            final_value=self.config.initial_cash
        )
        
        portfolio = Portfolio(cash=self.config.initial_cash)
        
        for current_date in trade_dates:
            if current_date < start_date or current_date > end_date:
                continue
            
            market_data = self.market_data_provider(current_date)
            prices = market_data.close_prices
            portfolio.update(prices)
            
            # 调仓检查
            rebalance_orders = []
            signals = []
            if self._should_rebalance(current_date, self.strategy.config.rebalance_freq):
                selection = self.strategy.select(current_date, stock_pool)
                targets = {p.code: p.weight for p in selection.positions}
                signals.append(selection.rebalance_reason)
                rebalance_orders = self._generate_rebalance_orders(portfolio, targets, prices, current_date)
            
            # 止损检查
            stop_orders = self._check_stop_loss(portfolio, prices, current_date)
            all_orders = rebalance_orders + stop_orders
            
            # 执行
            executed_trades, rejected = self.executor.execute(all_orders, market_data, portfolio, current_date)
            
            # 更新持仓
            self._update_portfolio(portfolio, executed_trades, current_date)
            portfolio.update(prices)
            
            # 记录
            daily_record = DailyRecord(
                date=current_date,
                portfolio=copy.deepcopy(portfolio),
                trades=executed_trades,
                signals=signals
            )
            result.daily_records.append(daily_record)
            result.all_trades.extend(executed_trades)
        
        result.final_value = portfolio.total_value
        return result

    def _should_rebalance(self, date: date, freq: str) -> bool:
        if freq == "monthly":
            return date.day <= 5
        elif freq == "quarterly":
            return date.day <= 5 and date.month in [1, 4, 7, 10]
        return False

    def _generate_rebalance_orders(self, portfolio: Portfolio, targets: Dict[str, float],
                                   prices: Dict[str, float], date: date) -> List[Order]:
        orders = []
        total_value = portfolio.total_value
        order_counter = 0
        
        target_positions = {}
        for code, weight in targets.items():
            if code in prices:
                target_value = total_value * weight
                qty = int(target_value / prices[code] / 100) * 100
                if qty > 0:
                    target_positions[code] = qty
        
        # 卖出
        for code, pos in portfolio.positions.items():
            if code not in target_positions and pos.quantity > 0:
                orders.append(Order(f"{date}_{order_counter}", date, code, OrderDirection.SELL, OrderType.MARKET, target_quantity=pos.quantity))
                order_counter += 1
        
        # 买入/调整
        for code, target_qty in target_positions.items():
            current_qty = portfolio.positions.get(code, Position(code=code)).quantity
            diff = target_qty - current_qty
            if diff != 0:
                orders.append(Order(f"{date}_{order_counter}", date, code, 
                                   OrderDirection.BUY if diff > 0 else OrderDirection.SELL,
                                   OrderType.MARKET, target_quantity=abs(diff)))
                order_counter += 1
        
        return orders

    def _check_stop_loss(self, portfolio: Portfolio, prices: Dict[str, float], date: date) -> List[Order]:
        orders = []
        if self.config.stop_loss is None:
            return orders
        
        for code, pos in portfolio.positions.items():
            if pos.avg_cost > 0 and code in prices:
                pct = prices[code] / pos.avg_cost
                if pct <= self.config.stop_loss:
                    orders.append(Order(f"{date}_sl_{code}", date, code, OrderDirection.SELL,
                                       OrderType.MARKET, target_quantity=pos.quantity))
        return orders

    def _update_portfolio(self, portfolio: Portfolio, trades: List[Trade], date: date):
        for trade in trades:
            pos = portfolio.positions.get(trade.code, Position(code=trade.code))
            
            if trade.direction == OrderDirection.BUY:
                total_cost = pos.quantity * pos.avg_cost + trade.quantity * trade.price
                pos.quantity += trade.quantity
                pos.avg_cost = total_cost / pos.quantity if pos.quantity > 0 else 0
                pos.buy_date = date
                portfolio.cash -= trade.net_amount + trade.commission + trade.stamp_tax
                self.executor.hold_history.setdefault(trade.code, []).append(date)
            else:
                pos.quantity -= trade.quantity
                if pos.quantity == 0:
                    pos.avg_cost = 0
                    pos.buy_date = None
                portfolio.cash += trade.net_amount - trade.commission
            
            if pos.quantity > 0:
                portfolio.positions[trade.code] = pos
            elif trade.code in portfolio.positions:
                del portfolio.positions[trade.code]
```

---

## Task 7: 业绩分析

**Files:**
- Create: `backtest/performance.py`

- [ ] **Step 1: 基础指标计算**

```python
from dataclasses import dataclass
from typing import Dict, List, Optional
from datetime import date
import math

@dataclass
class PerformanceMetrics:
    total_return: float = 0.0
    annualized_return: float = 0.0
    volatility: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_date: Optional[date] = None
    win_rate: float = 0.0
    total_trades: int = 0
    avg_holding_days: float = 0.0
    turnover: float = 0.0

class PerformanceAnalyzer:
    def __init__(self, risk_free_rate: float = 0.02):
        self.rf = risk_free_rate

    def analyze(self, result) -> PerformanceMetrics:
        nav_series = self._build_nav_series(result)
        
        metrics = PerformanceMetrics()
        metrics.total_return = self._total_return(nav_series)
        metrics.annualized_return = self._annualized_return(nav_series)
        metrics.volatility = self._volatility(nav_series)
        metrics.sharpe_ratio = self._sharpe(nav_series)
        metrics.max_drawdown, metrics.max_drawdown_date = self._max_drawdown(nav_series)
        metrics.total_trades = len(result.all_trades)
        
        return metrics

    def _build_nav_series(self, result):
        data = [(rec.date, rec.portfolio.total_value / result.initial_cash) for rec in result.daily_records]
        return dict(data)

    def _total_return(self, nav):
        values = list(nav.values())
        return (values[-1] / values[0]) - 1 if values else 0

    def _annualized_return(self, nav):
        dates = list(nav.keys())
        values = list(nav.values())
        if len(values) < 2:
            return 0
        tr = self._total_return(nav)
        years = (dates[-1] - dates[0]).days / 365
        return (1 + tr) ** (1 / years) - 1 if years > 0 else 0

    def _volatility(self, nav):
        dates = list(nav.keys())
        values = list(nav.values())
        if len(values) < 2:
            return 0
        returns = [values[i] / values[i-1] - 1 for i in range(1, len(values))]
        if not returns:
            return 0
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / len(returns)
        return math.sqrt(variance * 252)

    def _sharpe(self, nav):
        dates = list(nav.keys())
        values = list(nav.values())
        if len(values) < 2:
            return 0
        returns = [values[i] / values[i-1] - 1 for i in range(1, len(values))]
        if not returns:
            return 0
        mean = sum(returns) / len(returns)
        std = math.sqrt(sum((r - mean) ** 2 for r in returns) / len(returns))
        if std == 0:
            return 0
        excess = mean - self.rf / 252
        return excess / std * math.sqrt(252)

    def _max_drawdown(self, nav):
        values = list(nav.values())
        if not values:
            return 0, None
        peak = values[0]
        max_dd = 0
        max_dd_date = None
        dates = list(nav.keys())
        for i, v in enumerate(values):
            if v > peak:
                peak = v
            dd = (v - peak) / peak
            if dd < max_dd:
                max_dd = dd
                max_dd_date = dates[i]
        return max_dd, max_dd_date
```

---

## Task 8: 简单回测示例

**Files:**
- Create: `backtest/example.py`

- [ ] **创建运行示例**

```python
"""回测示例"""
from datetime import date, timedelta
from backtest.engine import BacktestEngine
from backtest.config import BacktestConfig, MarketData
from backtest.performance import PerformanceAnalyzer
from strategies.pharma_multi_factor import PharmaMultiFactorStrategy

# 模拟股票池
from domain_models import Stock
import random

def generate_mock_stocks(n=50):
    stocks = []
    for i in range(n):
        prices = [random.uniform(20, 100) for _ in range(300)]
        stocks.append(Stock(
            code=f"60{i:04d}",
            name=f"医药股{i}",
            pe=random.uniform(10, 80),
            pb=random.uniform(1, 10),
            peg=random.uniform(0.5, 3),
            revenue_growth=random.uniform(-0.1, 0.5),
            profit_growth=random.uniform(-0.2, 0.4),
            roe=random.uniform(0.05, 0.25),
            cashflow=random.uniform(0.05, 0.3),
            prices=prices,
            volumes=[random.randint(1000000, 10000000) for _ in range(300)],
            sector="医药生物",
            dividend_yield=random.uniform(0, 0.08),
            roe=random.uniform(0.08, 0.20),
            volatility_120d=random.uniform(0.15, 0.40),
        ))
    return stocks

def mock_market_data(target_date):
    from typing import Set
    prices = {s.code: s.prices[-1] if s.prices else 50 for s in mock_stocks}
    return MarketData(
        date=target_date,
        close_prices=prices,
        open_prices=prices,
        suspended=set(),
        limit_up=set(),
        limit_down=set()
    )

# 生成交易日
def get_trade_dates(start, end):
    dates = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            dates.append(current)
        current += timedelta(days=1)
    return dates

if __name__ == "__main__":
    # 初始化
    mock_stocks = generate_mock_stocks(50)
    strategy = PharmaMultiFactorStrategy()
    
    config = BacktestConfig(
        initial_cash=1_000_000,
        commission_rate=0.00025,
        stop_loss=0.75
    )
    
    # 回测
    engine = BacktestEngine(config, strategy, mock_market_data)
    trade_dates = get_trade_dates(date(2024, 1, 1), date(2024, 6, 30))
    
    result = engine.run(date(2024, 1, 1), date(2024, 6, 30), trade_dates, mock_stocks)
    
    # 分析
    analyzer = PerformanceAnalyzer()
    metrics = analyzer.analyze(result)
    
    print(f"总收益: {metrics.total_return:.2%}")
    print(f"年化收益: {metrics.annualized_return:.2%}")
    print(f"夏普比率: {metrics.sharpe_ratio:.2f}")
    print(f"最大回撤: {metrics.max_drawdown:.2%}")
    print(f"交易次数: {metrics.total_trades}")
```

---

## Task 9: 测试

**Files:**
- Create: `tests/test_multi_factor_base.py`
- Create: `tests/test_backtest_engine.py`

- [ ] **Step 1: test_multi_factor_base.py**

```python
import pytest
from strategies.multi_factor_base import MultiFactorConfig, MultiFactorBase, FactorConfig, FactorDirection
from domain_models import Stock

def test_z_score_normalization():
    from indicators import z_score_normalize
    values = [10, 20, 30, 40, 50]
    result = z_score_normalize(values, higher_is_better=True)
    assert len(result) == 5
    assert min(result) >= 0
    assert max(result) <= 100

def test_multi_factor_scoring():
    config = MultiFactorConfig(
        name="test",
        factors=[
            FactorConfig("pe", 0.5, FactorDirection.LOWER_IS_BETTER, lambda s: s.pe),
            FactorConfig("roe", 0.5, FactorDirection.HIGHER_IS_BETTER, lambda s: s.roe),
        ]
    )
    stocks = [
        Stock(code="1", name="A", pe=10, pb=1, peg=1, revenue_growth=0.1, profit_growth=0.1, roe=0.10, cashflow=0.1, prices=[10]*120, volumes=[100]*120),
        Stock(code="2", name="B", pe=20, pb=2, peg=2, revenue_growth=0.2, profit_growth=0.2, roe=0.20, cashflow=0.2, prices=[20]*120, volumes=[100]*120),
    ]
    strategy = MultiFactorBase(config)
    result = strategy.select(None, stocks)
    assert len(result.positions) == 2
    assert result.positions[0].score >= result.positions[1].score

def test_empty_candidates():
    config = MultiFactorConfig(name="test", factors=[], top_n=10)
    strategy = MultiFactorBase(config)
    result = strategy.select(None, [])
    assert len(result.positions) == 0
```

- [ ] **Step 2: test_backtest_engine.py**

```python
import pytest
from datetime import date
from backtest.engine import BacktestEngine
from backtest.config import BacktestConfig, MarketData
from backtest.models import Order, OrderDirection
from strategies.multi_factor_base import MultiFactorConfig, MultiFactorBase, FactorConfig, FactorDirection
from domain_models import Stock

def test_t1_restriction():
    from backtest.order_executor import OrderExecutor
    
    config = BacktestConfig(enable_T1=True)
    executor = OrderExecutor(config)
    
    market_data = MarketData(
        date=date(2024, 1, 2),
        close_prices={"1": 10.0},
        open_prices={"1": 10.0},
        suspended=set(),
        limit_up=set(),
        limit_down=set()
    )
    
    from backtest.models import Portfolio
    portfolio = Portfolio(cash=10000)
    
    # 买入
    buy_order = Order("1", date(2024, 1, 2), "1", OrderDirection.BUY, None, target_quantity=100)
    trades, _ = executor.execute([buy_order], market_data, portfolio, date(2024, 1, 2))
    assert len(trades) == 1
    
    # 当日再卖 - 应被拒绝
    sell_order = Order("2", date(2024, 1, 2), "1", OrderDirection.SELL, None, target_quantity=100)
    trades2, rejected = executor.execute([sell_order], market_data, portfolio, date(2024, 1, 2))
    assert len(rejected) == 1
    assert rejected[0].reason == "T+1"

def test_limit_up_buy_blocked():
    from backtest.order_executor import OrderExecutor
    
    config = BacktestConfig(allow_limit_up_buy=False)
    executor = OrderExecutor(config)
    
    market_data = MarketData(
        date=date(2024, 1, 2),
        close_prices={"1": 11.0},
        open_prices={"1": 10.0},
        suspended=set(),
        limit_up={"1"},
        limit_down=set()
    )
    
    from backtest.models import Portfolio
    portfolio = Portfolio(cash=10000)
    
    buy_order = Order("1", date(2024, 1, 2), "1", OrderDirection.BUY, None, target_quantity=100)
    trades, rejected = executor.execute([buy_order], market_data, portfolio, date(2024, 1, 2))
    assert len(rejected) == 1
    assert "涨停" in rejected[0].reason
```

---

## 实施顺序

1. **Task 1**: 数据模型扩展 (indicators + domain_models)
2. **Task 2**: 多因子基础框架
3. **Task 3**: 医药多因子策略
4. **Task 4**: 高股息低波策略
5. **Task 5**: 回测数据模型
6. **Task 6**: 回测引擎核心
7. **Task 7**: 业绩分析
8. **Task 8**: 简单回测示例
9. **Task 9**: 测试
