# 多因子选股策略 + 完整回测引擎 设计规格

**Date:** 2026-08-20
**Status:** Approved
**Author:** Claude

## 1. Overview

实现两个多因子量化选股策略及其完整回测框架：

1. **策略1: 医药多因子价值+质量策略** - 中长期稳健超额收益
2. **策略3: 高股息+低波防御策略** - 底仓配置型

采用三层架构：
- 策略层：选股信号生成
- 回测引擎层：订单执行、仓位管理、成本模拟
- 业绩分析层：指标计算、归因、可视化

---

## 2. 数据模型扩展

### 2.1 Stock 模型扩展 (`domain_models.py`)

```python
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

    # === 新增字段 ===

    # 行业分类
    sector: str = ""                    # 申万一级行业 (如 "医药生物")
    sub_sector: str = ""                # 细分赛道 (化学药/中药/生物制品/医疗器械)

    # === 价值因子 ===
    ev_ebitda: float = 0.0             # EV/EBITDA

    # === 质量因子 ===
    cash_div_ratio: float = 0.0        # 经营性现金流/净利润 (>1 为佳)
    gross_margin_std: float = 0.0      # 毛利率标准差 (越小越好)

    # === 成长因子 ===
    revenue_cagr_3y: float = 0.0        # 营收3年复合增速
    profit_cagr_3y: float = 0.0        # 净利润3年复合增速
    rd_expense_ratio: float = 0.0       # 研发费用率 (R&D/营收)

    # === 动量/低波因子 ===
    volatility_120d: float = 0.0        # 120日收益率标准差
    max_drawdown_1y: float = 0.0       # 过去1年最大回撤
    momentum_12m_1m: float = 0.0       # 12m-1m 动量 (过去12月剔除最近1月)
    excess_momentum: float = 0.0         # 相对行业指数超额动量

    # === 分红因子 ===
    dividend_yield: float = 0.0         # 股息率 (近12月分红/市值)
    dividend_stability: float = 0.0    # 分红稳定性 (0-1, 持续分红越高)
    dividend_payout_ratio: float = 0.0  # 分红率 (分红/净利润)

    # === 质量因子(补充) ===
    gross_margin: float = 0.0           # 毛利率
    debt_ratio: float = 0.0            # 资产负债率

    # === 历史数据用于计算 ===
    gross_margins_hist: List[float] = field(default_factory=list)  # 多年毛利率
    prices_hist: List[float] = field(default_factory=list)          # 历史价格(用于计算动量)
```

---

## 3. 策略层

### 3.1 多因子基础框架 (`strategies/multi_factor_base.py`)

```python
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Callable
from datetime import date
from enum import Enum

class FactorDirection(Enum):
    HIGHER_IS_BETTER = 1
    LOWER_IS_BETTER = -1

@dataclass
class FactorConfig:
    name: str
    weight: float
    direction: FactorDirection
    get_value: Callable[[Stock], float]  # 从 Stock 获取因子值

@dataclass
class MultiFactorConfig:
    """多因子策略配置"""
    name: str
    factors: List[FactorConfig]
    top_n: int = 30
    max_weight: float = 0.05           # 单票最大权重
    sector_limits: Dict[str, float] = field(default_factory=dict)  # 行业上限
    sub_sector_limits: Dict[str, float] = field(default_factory=dict)  # 细分赛道上限

    def validate(self) -> bool:
        """验证权重之和为1"""
        total = sum(f.weight for f in self.factors)
        return abs(total - 1.0) < 1e-6

@dataclass
class TargetPosition:
    """目标持仓"""
    code: str
    name: str
    weight: float
    score: float
    sector: str = ""
    sub_sector: str = ""

@dataclass
class SelectionResult:
    """选股结果"""
    date: date
    positions: List[TargetPosition]
    excluded: List[str] = field(default_factory=list)  # 被排除的股票及原因
    rebalance_reason: str = ""  # 调仓原因

class MultiFactorBase:
    """多因子策略基类"""

    def __init__(self, config: MultiFactorConfig):
        self.config = config

    def select(self, date: date, candidates: List[Stock]) -> SelectionResult:
        """
        选股主流程:
        1. 过滤候选池
        2. 计算各因子Z-score
        3. 计算综合得分
        4. 行业约束过滤
        5. 返回目标持仓
        """
        pass

    def _filter_candidates(self, stocks: List[Stock]) -> List[Stock]:
        """基础过滤: ST、流动性等"""
        pass

    def _calculate_z_scores(self, stocks: List[Stock]) -> Dict[str, List[float]]:
        """
        计算Z-score标准化后的因子值
        z = (x - mean) / std
        """
        pass

    def _calculate_composite_score(self, z_scores: Dict[str, List[float]]) -> List[float]:
        """
        综合得分 = Σ(因子Z-score × 权重)
        """
        pass

    def _apply_sector_constraints(self, positions: List[TargetPosition]) -> List[TargetPosition]:
        """应用行业权重约束"""
        pass

    def _rebalance_to_equal_weight(self, positions: List[TargetPosition]) -> List[TargetPosition]:
        """等权分配或按得分加权"""
        pass
```

### 3.2 Z-Score 标准化实现

```python
def z_score_normalize(values: List[float], higher_is_better: bool = True) -> List[float]:
    """
    Z-score 标准化: (x - mean) / std
    返回值范围约 [-3, +3]
    转换为正数便于组合: z_normalized = (z - min_z) / (max_z - min_z) * 100
    """
    if len(values) < 2:
        return [50.0] * len(values)

    mean = sum(values) / len(values)
    variance = sum((x - mean) ** 2 for x in values) / len(values)
    std = variance ** 0.5

    if std < 1e-10:
        return [50.0] * len(values)

    z_scores = [(x - mean) / std for x in values]

    # 转换为 [0, 100] 范围便于理解
    min_z, max_z = min(z_scores), max(z_scores)
    if max_z - min_z < 1e-10:
        return [50.0] * len(values)

    normalized = [(z - min_z) / (max_z - min_z) * 100 for z in z_scores]

    # 如果 lower_is_better, 反转
    if not higher_is_better:
        normalized = [100 - n for n in normalized]

    return normalized
```

### 3.3 策略1: 医药多因子 (`strategies/pharma_multi_factor.py`)

```python
@dataclass
class PharmaMultiFactorConfig(MultiFactorConfig):
    """医药多因子策略配置"""
    name: str = "医药多因子价值+质量"
    sector: str = "医药生物"
    top_n: int = 25
    max_weight: float = 0.05
    rebalance_freq: str = "monthly"  # 月度调仓

# 因子权重
PHARMA_FACTOR_WEIGHTS = {
    # === 价值因子 (30%) ===
    "pe_rank": 0.10,       # PE行业分位 (低越好)
    "pb_rank": 0.10,       # PB行业分位 (低越好)
    "ev_ebitda_rank": 0.10, # EV/EBITDA行业分位 (低越好)

    # === 质量因子 (30%) ===
    "roe": 0.12,           # ROE (高越好)
    "cash_div_ratio": 0.10, # 现金流/净利润 (高越好)
    "gross_margin_std_inv": 0.08, # 毛利率稳定性 (低越好, 用倒数)

    # === 成长因子 (20%) ===
    "revenue_cagr": 0.08,  # 营收3年CAGR (高越好)
    "profit_cagr": 0.07,   # 净利润3年CAGR (高越好)
    "rd_expense_ratio": 0.05, # 研发费用率 (高越好, 创新药更重要)

    # === 动量因子 (10%) ===
    "momentum_12m_1m": 0.06,  # 12m-1m动量 (高越好)
    "excess_momentum": 0.04,  # 相对行业超额动量 (高越好)

    # === 低波因子 (10%) ===
    "volatility_inv": 0.05,    # 波动率 (低越好, 用倒数)
    "max_drawdown_inv": 0.05,  # 最大回撤 (低越好, 用倒数)
}

# 过滤条件
PHARMA_EXCLUDE_RULES = {
    "st": True,                    # 剔除ST/*ST
    "ipo_days_min": 365,          # 上市满1年
    "avg_volume_60d_min": 30_000_000,  # 60日日均成交额 >= 3000万
}

# 行业约束
PHARMA_SECTOR_LIMITS = {
    "sub_sector": {
        "化学制药": 0.40,
        "中药": 0.40,
        "生物制品": 0.40,
        "医疗器械": 0.40,
    }
}
```

### 3.4 策略3: 高股息低波 (`strategies/dividend_multi_factor.py`)

```python
@dataclass
class DividendMultiFactorConfig(MultiFactorConfig):
    """高股息低波防御策略配置"""
    name: str = "高股息+低波防御"
    sectors: List[str] = field(default_factory=lambda: [
        "银行", "保险", "公用事业", "中药", "可选消费"
    ])
    top_n: int = 40
    max_weight: float = 0.04       # 单票上限4%
    rebalance_freq: str = "quarterly"  # 季度调仓

# 因子权重
DIVIDEND_FACTOR_WEIGHTS = {
    # === 股息因子 (40%) ===
    "dividend_yield": 0.25,       # 股息率 (高越好)
    "dividend_stability": 0.15,  # 分红稳定性 (高越好)

    # === 低波因子 (25%) ===
    "volatility_inv": 0.15,       # 250日波动率 (低越好)
    "max_drawdown_inv": 0.10,    # 最大回撤 (低越好)

    # === 估值因子 (20%) ===
    "pe_rank": 0.10,              # PE全市场分位 (低越好)
    "pb_rank": 0.05,             # PB分位 (低越好)
    "dividend_yield_premium": 0.05, # 股息率相对行业溢价 (高越好)

    # === 质量因子 (15%) ===
    "roe": 0.07,                  # ROE (高越好)
    "debt_ratio_inv": 0.04,      # 资产负债率 (低越好, 用倒数)
    "cash_div_ratio": 0.04,      # 现金流/净利润 (高越好)
}

# 过滤条件
DIVIDEND_EXCLUDE_RULES = {
    "st": True,
    "dividend_yield_min": 0.0,    # 股息率 > 0
    "profit_negative_3y": True,   # 剔除过去3年净利润为负
    "payout_ratio_max": 1.0,      # 分红率 <= 100% (避免借钱分红)
    "avg_volume_60d_min": 20_000_000,  # 60日日均成交额 >= 2000万
}

# 行业约束
DIVIDEND_SECTOR_LIMITS = {
    "sector": {
        # 单一行业不超过30%
    }
}
```

---

## 4. 回测引擎层

### 4.1 目录结构

```
backtest/
├── __init__.py
├── config.py           # BacktestConfig
├── models.py            # Order, Trade, Position, Portfolio
├── engine.py            # BacktestEngine
├── cost_calculator.py    # 成本计算
└── order_executor.py    # 订单执行逻辑
```

### 4.2 配置模型 (`config.py`)

```python
from dataclasses import dataclass
from typing import Optional

@dataclass
class BacktestConfig:
    """回测配置"""

    # === 初始资金 ===
    initial_cash: float = 1_000_000.0

    # === 交易成本 ===
    commission_rate: float = 0.00025   # 手续费: 万2.5
    stamp_tax_rate: float = 0.001      # 印花税: 千1 (仅卖出)
    slippage_rate: float = 0.001       # 滑点: 0.1%

    # === 交易规则 ===
    enable_T1: bool = True             # T+1 限制
    allow_limit_up_buy: bool = False   # 涨停是否能买入
    allow_limit_down_sell: bool = False  # 跌停是否能卖出

    # === 风控 ===
    max_single_position: float = 0.10  # 单票最大仓位10%
    stop_loss: Optional[float] = None  # 止损线, 如 0.75
    take_profit: Optional[float] = None  # 止盈线, 如 1.50

    # === 回测模式 ===
    price_type: str = "close"         # 使用收盘价/开盘价/VWAP
    rebalance_type: str = "full"      # full=全部换仓, incremental=增量调整

@dataclass
class MarketData:
    """某日市场数据"""
    date: date
    close_prices: Dict[str, float]     # code -> 收盘价
    open_prices: Dict[str, float]      # code -> 开盘价
    suspended: Set[str]                 # 停牌股票
    limit_up: Set[str]                 # 涨停股票
    limit_down: Set[str]               # 跌停股票
    turnover: Dict[str, float]         # code -> 换手率
```

### 4.3 数据模型 (`models.py`)

```python
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import date
from enum import Enum
from decimal import Decimal

class OrderDirection(Enum):
    BUY = "BUY"
    SELL = "SELL"

class OrderType(Enum):
    MARKET = "MARKET"      # 市价单
    LIMIT = "LIMIT"         # 限价单

@dataclass
class Order:
    """订单"""
    order_id: str
    date: date             # 下单日期
    code: str
    direction: OrderDirection
    order_type: OrderType
    price: float = 0.0     # 限价, 市价单=0
    target_quantity: int = 0  # 目标持股数
    filled_quantity: int = 0  # 成交数量
    status: str = "PENDING"  # PENDING, FILLED, CANCELLED, REJECTED
    reason: str = ""        # 拒绝原因

@dataclass
class Trade:
    """成交记录"""
    trade_id: str
    order_id: str
    date: date
    code: str
    direction: OrderDirection
    price: float           # 成交价(含滑点)
    quantity: int          # 成交数量
    commission: float      # 手续费
    stamp_tax: float       # 印花税
    net_amount: float      # 净成交金额(扣费后)

@dataclass
class Position:
    """持仓"""
    code: str
    name: str = ""
    quantity: int = 0                  # 持股数
    avg_cost: float = 0.0              # 加权平均成本
    current_price: float = 0.0         # 当前价
    current_value: float = 0.0         # 当前市值
    unrealized_pnl: float = 0.0        # 浮动盈亏
    unrealized_pnl_pct: float = 0.0   # 浮动盈亏率
    buy_date: Optional[date] = None    # 买入日期(T+1用)

@dataclass
class Portfolio:
    """组合"""
    cash: float = 0.0
    positions: Dict[str, Position] = field(default_factory=dict)
    total_value: float = 0.0
    date: date = None

    def update(self, prices: Dict[str, float]):
        """更新市值和盈亏"""
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
    """每日记录"""
    date: date
    portfolio: Portfolio
    trades: List[Trade] = field(default_factory=list)
    signals: List[str] = field(default_factory=list)  # 当日信号说明

@dataclass
class BacktestResult:
    """回测结果"""
    config: BacktestConfig
    start_date: date
    end_date: date
    initial_cash: float
    final_value: float
    daily_records: List[DailyRecord] = field(default_factory=list)
    all_trades: List[Trade] = field(default_factory=list)

    @property
    def total_return(self) -> float:
        return (self.final_value - self.initial_cash) / self.initial_cash

    @property
    def trade_count(self) -> int:
        return len(self.all_trades)
```

### 4.4 成本计算 (`cost_calculator.py`)

```python
@dataclass
class CostBreakdown:
    """成本明细"""
    commission: float
    stamp_tax: float
    slippage: float
    total_cost: float

def calculate_costs(
    direction: OrderDirection,
    price: float,
    quantity: int,
    config: BacktestConfig
) -> CostBreakdown:
    """
    计算交易成本

    买入:
    - 手续费 = 成交金额 × commission_rate
    - 滑点 = 成交金额 × slippage_rate
    - 印花税 = 0

    卖出:
    - 手续费 = 成交金额 × commission_rate
    - 滑点 = 成交金额 × slippage_rate
    - 印花税 = 成交金额 × stamp_tax_rate
    """
    amount = price * quantity

    commission = amount * config.commission_rate

    if direction == OrderDirection.SELL:
        stamp_tax = amount * config.stamp_tax_rate
    else:
        stamp_tax = 0.0

    slippage = amount * config.slippage_rate

    total_cost = commission + stamp_tax + slippage

    return CostBreakdown(
        commission=commission,
        stamp_tax=stamp_tax,
        slippage=slippage,
        total_cost=total_cost
    )
```

### 4.5 订单执行 (`order_executor.py`)

```python
from typing import List, Tuple, Optional

class OrderExecutor:
    """订单执行器"""

    def __init__(self, config: BacktestConfig):
        self.config = config
        self.hold_history: Dict[str, List[date]] = {}  # code -> 买入日期列表

    def execute(
        self,
        orders: List[Order],
        market_data: MarketData,
        portfolio: Portfolio,
        next_date: date
    ) -> Tuple[List[Trade], List[Order]]:
        """
        执行订单

        Returns:
            (成功成交列表, 被拒绝/待处理订单)
        """
        executed = []
        rejected = []

        for order in orders:
            result = self._execute_single(order, market_data, portfolio, next_date)
            if result[0]:  # Trade
                executed.append(result[0])
            else:  # Order (rejected or pending)
                rejected.append(result[1])

        return executed, rejected

    def _execute_single(
        self,
        order: Order,
        market_data: MarketData,
        portfolio: Portfolio,
        next_date: date
    ) -> Tuple[Optional[Trade], Order]:
        """
        执行单个订单

        执行规则:
        1. 涨停时买入被拒绝
        2. 跌停时卖出被拒绝
        3. 停牌时无法交易
        4. T+1: 当日买入不可当日卖出
        """
        code = order.code

        # 检查停牌
        if code in market_data.suspended:
            order.status = "REJECTED"
            order.reason = "停牌"
            return None, order

        # 获取成交价格
        if code in market_data.close_prices:
            exec_price = market_data.close_prices[code]
        else:
            order.status = "REJECTED"
            order.reason = "无价格数据"
            return None, order

        # 涨跌停检查
        if order.direction == OrderDirection.BUY:
            if code in market_data.limit_up and not self.config.allow_limit_up_buy:
                order.status = "REJECTED"
                order.reason = "涨停无法买入"
                return None, order

            # 检查T+1
            if code in self.hold_history:
                last_buy = self.hold_history[code][-1]
                if last_buy == next_date:
                    order.status = "REJECTED"
                    order.reason = "T+1限制"
                    return None, order

        elif order.direction == OrderDirection.SELL:
            if code in market_data.limit_down and not self.config.allow_limit_down_sell:
                order.status = "REJECTED"
                order.reason = "跌停无法卖出"
                return None, order

        # 应用滑点
        if order.direction == OrderDirection.BUY:
            exec_price = exec_price * (1 + self.config.slippage_rate)
        else:
            exec_price = exec_price * (1 - self.config.slippage_rate)

        # 市价单转换为实际价格
        quantity = order.target_quantity
        costs = calculate_costs(order.direction, exec_price, quantity, self.config)

        # 构建成交记录
        trade = Trade(
            trade_id=generate_trade_id(),
            order_id=order.order_id,
            date=next_date,
            code=code,
            direction=order.direction,
            price=exec_price,
            quantity=quantity,
            commission=costs.commission,
            stamp_tax=costs.stamp_tax,
            net_amount=exec_price * quantity - costs.total_cost
        )

        order.status = "FILLED"
        order.filled_quantity = quantity

        return trade, order
```

### 4.6 回测引擎 (`engine.py`)

```python
class BacktestEngine:
    """
    回测引擎

    主流程:
    1. 初始化: 设置初始资金、加载历史数据
    2. 按日循环:
        a. 获取当日市场数据
        b. 调用策略生成信号
        c. 生成订单
        d. 执行订单
        e. 更新持仓
        f. 记录每日结果
    3. 输出回测结果
    """

    def __init__(
        self,
        config: BacktestConfig,
        strategy: MultiFactorBase,
        market_data_provider: Callable[[date], MarketData]
    ):
        self.config = config
        self.strategy = strategy
        self.market_data_provider = market_data_provider
        self.executor = OrderExecutor(config)

    def run(
        self,
        start_date: date,
        end_date: date,
        trade_dates: List[date],
        stock_pool: List[Stock],
        benchmark_data: Optional[Dict[date, float]] = None
    ) -> BacktestResult:
        """
        运行回测

        Args:
            start_date: 回测开始日期
            end_date: 回测结束日期
            trade_dates: 交易日列表
            stock_pool: 股票池(每日更新最新数据)
            benchmark_data: 基准净值数据 {date: value}

        Returns:
            BacktestResult
        """
        result = BacktestResult(
            config=self.config,
            start_date=start_date,
            end_date=end_date,
            initial_cash=self.config.initial_cash,
            final_value=self.config.initial_cash,
            daily_records=[]
        )

        # 初始化组合
        portfolio = Portfolio(cash=self.config.initial_cash)

        # 按日期循环
        for i, current_date in enumerate(trade_dates):
            if current_date < start_date or current_date > end_date:
                continue

            # 获取市场数据
            market_data = self.market_data_provider(current_date)

            # 更新持仓市值
            prices = market_data.close_prices
            portfolio.update(prices)

            # === 策略信号 ===
            targets = None
            signals = []
            rebalance_orders = []

            # 判断是否调仓日
            if self._should_rebalance(current_date, self.strategy.config.rebalance_freq):
                selection = self.strategy.select(current_date, stock_pool)
                targets = {p.code: p.weight for p in selection.positions}
                signals.append(selection.rebalance_reason)
                rebalance_orders = self._generate_rebalance_orders(
                    portfolio, targets, prices, current_date
                )

            # === 止损/止盈检查 ===
            stop_orders = self._check_stop_loss(portfolio, prices, current_date)
            all_orders = rebalance_orders + stop_orders

            # === 执行订单 ===
            executed_trades, rejected = self.executor.execute(
                all_orders, market_data, portfolio, current_date
            )

            # === 更新持仓 ===
            self._update_portfolio(portfolio, executed_trades, current_date)
            portfolio.update(prices)

            # === 记录 ===
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
        """判断是否调仓日"""
        if freq == "monthly":
            return date.day <= 5  # 每月前5个交易日
        elif freq == "quarterly":
            return date.day <= 5 and date.month in [1, 4, 7, 10]
        return False

    def _generate_rebalance_orders(
        self,
        portfolio: Portfolio,
        targets: Dict[str, float],
        prices: Dict[str, float],
        date: date
    ) -> List[Order]:
        """生成调仓订单"""
        orders = []
        total_value = portfolio.total_value
        order_id = 0

        # 计算目标持仓
        target_positions = {}
        for code, weight in targets.items():
            if weight > 0 and code in prices:
                target_value = total_value * weight
                target_quantity = int(target_value / prices[code] / 100) * 100  # 整百
                if target_quantity > 0:
                    target_positions[code] = target_quantity

        # 卖出不在目标中的持仓
        for code, pos in portfolio.positions.items():
            if code not in target_positions and pos.quantity > 0:
                orders.append(Order(
                    order_id=f"{date}_{order_id}",
                    date=date,
                    code=code,
                    direction=OrderDirection.SELL,
                    order_type=OrderType.MARKET,
                    target_quantity=pos.quantity
                ))
                order_id += 1

        # 买入目标持仓
        for code, target_qty in target_positions.items():
            current_qty = portfolio.positions.get(code, Position(code=code)).quantity
            diff = target_qty - current_qty
            if diff > 0:  # 买入
                orders.append(Order(
                    order_id=f"{date}_{order_id}",
                    date=date,
                    code=code,
                    direction=OrderDirection.BUY,
                    order_type=OrderType.MARKET,
                    target_quantity=diff
                ))
                order_id += 1
            elif diff < 0:  # 卖出
                orders.append(Order(
                    order_id=f"{date}_{order_id}",
                    date=date,
                    code=code,
                    direction=OrderDirection.SELL,
                    order_type=OrderType.MARKET,
                    target_quantity=-diff
                ))
                order_id += 1

        return orders

    def _check_stop_loss(
        self,
        portfolio: Portfolio,
        prices: Dict[str, float],
        date: date
    ) -> List[Order]:
        """止损/止盈检查"""
        orders = []
        if self.config.stop_loss is None and self.config.take_profit is None:
            return orders

        for code, pos in portfolio.positions.items():
            if pos.avg_cost <= 0 or code not in prices:
                continue

            pct = prices[code] / pos.avg_cost

            if self.config.stop_loss and pct <= self.config.stop_loss:
                orders.append(Order(
                    order_id=f"{date}_stop_{code}",
                    date=date,
                    code=code,
                    direction=OrderDirection.SELL,
                    order_type=OrderType.MARKET,
                    target_quantity=pos.quantity
                ))
            elif self.config.take_profit and pct >= self.config.take_profit:
                orders.append(Order(
                    order_id=f"{date}_tp_{code}",
                    date=date,
                    code=code,
                    direction=OrderDirection.SELL,
                    order_type=OrderType.MARKET,
                    target_quantity=pos.quantity
                ))

        return orders

    def _update_portfolio(
        self,
        portfolio: Portfolio,
        trades: List[Trade],
        date: date
    ):
        """更新持仓"""
        for trade in trades:
            pos = portfolio.positions.get(trade.code, Position(code=trade.code))

            if trade.direction == OrderDirection.BUY:
                # 买入: 更新持股数和成本
                total_cost = pos.quantity * pos.avg_cost + trade.quantity * trade.price
                pos.quantity += trade.quantity
                pos.avg_cost = total_cost / pos.quantity if pos.quantity > 0 else 0
                pos.buy_date = date

                # 扣除现金
                portfolio.cash -= trade.net_amount + trade.commission + trade.stamp_tax

                # 记录买入历史(T+1用)
                self.executor.hold_history.setdefault(trade.code, []).append(date)

            elif trade.direction == OrderDirection.SELL:
                # 卖出: 更新持股数
                pos.quantity -= trade.quantity
                if pos.quantity == 0:
                    pos.avg_cost = 0
                    pos.buy_date = None

                # 增加现金
                portfolio.cash += trade.net_amount - trade.commission

            # 更新positions字典
            if pos.quantity > 0:
                portfolio.positions[trade.code] = pos
            elif trade.code in portfolio.positions:
                del portfolio.positions[trade.code]
```

---

## 5. 业绩分析层

### 5.1 目录结构

```
backtest/
├── performance.py        # PerformanceAnalyzer, PerformanceMetrics
├── attribution.py        # 归因分析
└── visualization.py      # 图表生成
```

### 5.2 性能指标 (`performance.py`)

```python
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from datetime import date
import pandas as pd

@dataclass
class PerformanceMetrics:
    """性能指标"""

    # === 基础指标 ===
    total_return: float           # 累计收益率
    annualized_return: float      # 年化收益率 (CAGR)
    volatility: float             # 年化波动率
    sharpe_ratio: float          # 夏普比率 (rf=0.02)
    max_drawdown: float          # 最大回撤
    max_drawdown_date: date      # 最大回撤日期
    calmar_ratio: float          # Calmar = CAGR / MDD
    win_rate: float              # 胜率

    # === 交易统计 ===
    total_trades: int
    buy_trades: int
    sell_trades: int
    avg_holding_days: float      # 平均持仓天数
    turnover: float             # 年化换手率
    avg_trade_value: float      # 平均单笔交易金额

    # === 风险指标 ===
    downside_deviation: float    # 下行偏差
    sortino_ratio: float         # 索提诺比率
    var_95: float               # 95% VaR
    cvar_95: float              # 95% CVaR

    # === 基准对比 ===
    benchmark_return: float      # 基准收益率
    excess_return: float         # 超额收益
    tracking_error: float        # 跟踪误差
    information_ratio: float     # 信息比率

    # === 时间分解 ===
    annual_returns: Dict[int, float]  # {year: return}
    monthly_returns: Dict[str, float]  # {"2024-01": return}


@dataclass
class AnnualPerformance:
    """年度表现"""
    year: int
    return_: float
    benchmark_return: float
    excess_return: float
    max_drawdown: float
    trades: int
    turnover: float
    sharpe: float


@dataclass
class FactorContribution:
    """因子贡献度"""
    factor: str
    weight: float
    realized_return: float
    contribution: float  # weight * realized_return


class PerformanceAnalyzer:
    """业绩分析器"""

    def __init__(self, risk_free_rate: float = 0.02):
        self.rf = risk_free_rate

    def analyze(
        self,
        result: BacktestResult,
        benchmark: Optional[pd.Series] = None
    ) -> PerformanceMetrics:
        """
        分析回测结果

        Args:
            result: 回测结果
            benchmark: 基准净值序列 (index=date, values=净值)

        Returns:
            PerformanceMetrics
        """
        # 构建净值序列
        nav_series = self._build_nav_series(result)

        # 计算基础指标
        metrics = PerformanceMetrics(
            total_return=self._calc_total_return(nav_series),
            annualized_return=self._calc_annualized_return(nav_series),
            volatility=self._calc_volatility(nav_series),
            sharpe_ratio=self._calc_sharpe(nav_series),
            max_drawdown=self._calc_max_drawdown(nav_series),
            # ... 其他指标
        )

        return metrics

    def _build_nav_series(self, result: BacktestResult) -> pd.Series:
        """构建净值序列"""
        data = {
            rec.date: rec.portfolio.total_value / result.initial_cash
            for rec in result.daily_records
        }
        return pd.Series(data).sort_index()

    def _calc_total_return(self, nav: pd.Series) -> float:
        return (nav.iloc[-1] / nav.iloc[0]) - 1

    def _calc_annualized_return(self, nav: pd.Series) -> float:
        total_return = self._calc_total_return(nav)
        days = (nav.index[-1] - nav.index[0]).days
        years = days / 365
        return (1 + total_return) ** (1 / years) - 1 if years > 0 else 0

    def _calc_volatility(self, nav: pd.Series) -> float:
        returns = nav.pct_change().dropna()
        return returns.std() * np.sqrt(252)

    def _calc_sharpe(self, nav: pd.Series) -> float:
        returns = nav.pct_change().dropna()
        excess = returns - self.rf / 252
        return excess.mean() / returns.std() * np.sqrt(252) if returns.std() > 0 else 0

    def _calc_max_drawdown(self, nav: pd.Series) -> tuple:
        """返回 (max_dd, max_dd_date)"""
        cummax = nav.cummax()
        drawdown = (nav - cummax) / cummax
        max_dd = drawdown.min()
        max_dd_date = drawdown.idxmin()
        return max_dd, max_dd_date

    def analyze_by_year(self, result: BacktestResult) -> List[AnnualPerformance]:
        """按年度分析"""
        pass

    def factor_attribution(
        self,
        result: BacktestResult,
        factor_returns: Dict[str, pd.Series]
    ) -> List[FactorContribution]:
        """
        因子归因分析

        对多因子策略, 分析各因子对收益的贡献
        """
        pass
```

### 5.3 可视化 (`visualization.py`)

```python
import matplotlib.pyplot as plt
from pathlib import Path

class PerformanceVisualizer:
    """业绩可视化"""

    def __init__(self, output_dir: str = "./backtest_results"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)

    def plot_results(
        self,
        result: BacktestResult,
        metrics: PerformanceMetrics,
        benchmark: Optional[pd.Series] = None,
        save: bool = True
    ):
        """
        生成所有图表

        图表列表:
        1. 净值曲线 vs 基准
        2. 回撤曲线
        3. 年度收益柱状图
        4. 月度收益热力图
        5. 持仓分布饼图
        6. 交易统计仪表盘
        """
        fig, axes = plt.subplots(3, 2, figsize=(16, 12))

        self._plot_nav_curve(axes[0, 0], result, benchmark)
        self._plot_drawdown(axes[0, 1], result)
        self._plot_annual_returns(axes[1, 0], result)
        self._plot_monthly_heatmap(axes[1, 1], result)
        self._plot_trade_distribution(axes[2, 0], result)
        self._plot_metrics_dashboard(axes[2, 1], metrics)

        plt.tight_layout()

        if save:
            path = self.output_dir / f"backtest_{result.start_date}_{result.end_date}.png"
            plt.savefig(path, dpi=150)
            plt.close()
        else:
            plt.show()

    def _plot_nav_curve(
        self,
        ax: plt.Axes,
        result: BacktestResult,
        benchmark: Optional[pd.Series]
    ):
        """净值曲线"""
        nav = self._build_nav_series(result)
        ax.plot(nav.index, nav.values, label='Strategy', linewidth=1.5)

        if benchmark is not None:
            # 对齐基准
            bm = benchmark.reindex(nav.index).dropna()
            ax.plot(bm.index, bm.values / bm.iloc[0] * nav.iloc[0],
                   label='Benchmark', linewidth=1.5, alpha=0.7)

        ax.set_title('Net Asset Value')
        ax.set_xlabel('Date')
        ax.set_ylabel('NAV')
        ax.legend()
        ax.grid(True, alpha=0.3)

    def _plot_drawdown(self, ax: plt.Axes, result: BacktestResult):
        """回撤曲线"""
        nav = self._build_nav_series(result)
        cummax = nav.cummax()
        drawdown = (nav - cummax) / cummax * 100

        ax.fill_between(drawdown.index, drawdown.values, 0, alpha=0.3, color='red')
        ax.plot(drawdown.index, drawdown.values, color='red', linewidth=1)
        ax.set_title('Drawdown')
        ax.set_xlabel('Date')
        ax.set_ylabel('Drawdown (%)')
        ax.grid(True, alpha=0.3)

    def _plot_annual_returns(self, ax: plt.Axes, result: BacktestResult):
        """年度收益柱状图"""
        annual = self._get_annual_returns(result)

        colors = ['green' if r > 0 else 'red' for r in annual.values()]
        ax.bar(annual.keys(), annual.values(), color=colors, alpha=0.7)
        ax.axhline(y=0, color='black', linestyle='-', linewidth=0.5)
        ax.set_title('Annual Returns')
        ax.set_xlabel('Year')
        ax.set_ylabel('Return (%)')
        ax.grid(True, alpha=0.3)
```

---

## 6. 实现计划

### Phase 1: 数据模型扩展 (1天)
- [ ] 扩展 `domain_models.Stock`
- [ ] 新增因子计算辅助函数

### Phase 2: 策略层 (2天)
- [ ] `strategies/multi_factor_base.py`
- [ ] `strategies/pharma_multi_factor.py`
- [ ] `strategies/dividend_multi_factor.py`

### Phase 3: 回测引擎 (3天)
- [ ] `backtest/config.py`
- [ ] `backtest/models.py`
- [ ] `backtest/cost_calculator.py`
- [ ] `backtest/order_executor.py`
- [ ] `backtest/engine.py`

### Phase 4: 业绩分析 (2天)
- [ ] `backtest/performance.py`
- [ ] `backtest/visualization.py`
- [ ] `backtest/attribution.py`

### Phase 5: 测试与集成 (1天)
- [ ] 单元测试
- [ ] 简单回测示例
- [ ] 文档

---

## 7. 已知限制

1. **数据依赖**: 依赖 `akshare_data_provider` 提供完整财务数据(EV/EBITDA、研发费用、毛利率历史等)
2. **行业分类**: 需要准确的申万/中信一级行业分类
3. **历史分位**: Z-score 在候选池内计算, 不依赖全市场历史分位
4. **回测频率**: 初始版本仅支持月度/季度调仓

---

## 8. API 使用示例

```python
from strategies.pharma_multi_factor import PharmaMultiFactorStrategy
from backtest.engine import BacktestEngine
from backtest.performance import PerformanceAnalyzer
from backtest.visualization import PerformanceVisualizer

# 1. 初始化策略
config = PharmaMultiFactorConfig()
strategy = PharmaMultiFactorStrategy(config)

# 2. 配置回测
bt_config = BacktestConfig(
    initial_cash=1_000_000,
    commission_rate=0.00025,
    stop_loss=0.75,  # 25%止损
    rebalance_freq="monthly"
)

# 3. 运行回测
engine = BacktestEngine(bt_config, strategy, market_data_provider)
result = engine.run(
    start_date=date(2021, 1, 1),
    end_date=date(2024, 12, 31),
    trade_dates=trade_dates,
    stock_pool=pharma_stocks
)

# 4. 业绩分析
analyzer = PerformanceAnalyzer(risk_free_rate=0.02)
metrics = analyzer.analyze(result, benchmark=hs300_nav)
print(f"年化收益: {metrics.annualized_return:.2%}")
print(f"夏普比率: {metrics.sharpe_ratio:.2f}")
print(f"最大回撤: {metrics.max_drawdown:.2%}")

# 5. 可视化
viz = PerformanceVisualizer(output_dir="./results")
viz.plot_results(result, metrics, benchmark=hs300_nav)
```
