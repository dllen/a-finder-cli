# 小散量化买点策略扩展设计

日期：2026-08-19
状态：已批准（分节确认通过）

## 目标

为小散户在现有「多均线 + 信号 + 评分」体系之上，新增更多买点信号，并通过显式回测闸门控制「哪些策略值钱、能进主榜」，避免盲目追涨加仓。

非目标：不做参数寻优、不做持仓/资金分配改造、不改现有「多均线」主策略。

## 现有体系（基线）

- 买点信号 `signal_rules.detect_signals`：均线突破/跌破、动量突破、回调买入、MACD 金叉/死叉、RSI 超卖。
- 候选 `candidate_rules`：多均线突破/回踩/趋势，含熊市超跌反弹、震荡市精准回踩，按 `market_regime` 自适应。
- 评分 `scoring`：价值(PE/PB/PEG) + 成长 + 动量 + 质量(ROE/现金流) 加权。
- 择时 `market_regime`：牛/熊/震荡（技术 + 指数 + 基本面加权）。
- 回测 `ma_backtest`：walk-forward / 稳健性 / 参数优化，但为**组合+仓位覆盖**口径，无法拆出单策略盈亏比。
- 数据：`PriceRow` 已含 `turnover` / `amount` / `amplitude` / `pct_change`（东方财富 kline），但 `Stock` 只带 `prices` / `volumes`，量价字段在选股/回测链路被丢弃。

## 需求决策（已与用户确认）

- 风格：组合型 —— 趋势 / 反转 / 量价资金 三类各 1-2 个，按 market_regime 自动启停。
- 数据：可加 akshare 免费字段；一期只使用 `PriceRow` 已有的 `turnover`/`amount`/`pct_change`，北向/主力资金流列为二期。
- 集成：先并行再合并 —— 新策略独立运行、独立回测，达标后经适配器并入主榜。
- 门槛：胜率 ≥ 45% 且盈亏比 ≥ 1.5，或期望收益 > 0。

## 第 1 节：策略清单

趋势型（BULL 启用）
1. 箱体突破：20 日窄幅整理（振幅 < 阈值）后，收盘价 + 放量突破箱体上沿。
2. 新高突破：创 60 日新高，且量比 ≥ 1.5。

反转型（BEAR / SIDEWAYS 启用）
3. 布林下轨超卖反弹：收盘跌破布林下轨后收回，RSI < 30。
4. KDJ 低位金叉：KDJ 的 K 上穿 D 且 K < 20。

量价资金型（全 regime，弱市降权）
5. 量价齐升确认：涨幅与成交额同步放大（`amount` / `turnover` / `pct_change`）。
6. 主力/北向净流入：需 akshare 额外字段（`ak.stock_hsgt_*` / 资金流接口），二期。

一期落地 5 个（第 6 个二期）。

## 第 2 节：架构与数据流

新增目录 `strategies/`：

```
strategies/
  __init__.py          # 注册表：NAME -> detect 函数
  base.py              # StrategySignal dataclass + 公共指标(布林/KDJ/箱体)
  box_breakout.py      # 箱体突破
  new_high.py          # 新高突破
  bollinger_rebound.py # 布林超卖反弹
  kdj_cross.py         # KDJ 低位金叉
  volume_price.py      # 量价齐升
  backtest.py          # 独立单策略回测器（胜率/盈亏比/期望）
```

数据流：
1. `domain_models.Stock` 增加可选字段 `turnover` / `amount` / `pct_change`；`build_market_from_db` 从 `daily_prices` 对应列填充，合成 `build_market` 用 0 或 None 兜底，不破坏现有测试。
2. detector 统一签名：`detect(stock: Stock, regime: RegimeType) -> List[StrategySignal]`，返回 `{code, strategy, entry, stop, tp, score}`。
3. 独立验证器 `strategies/backtest.py`：复刻 `ma_backtest.run_backtest` 的逐日回测骨架，但对每个策略**单独**计算胜率 / 盈亏比 / 期望收益。
4. 输出 `strategies/report.json`：每策略三项指标 + 达标与否。
5. 并入主榜（达标后才走）：适配器把 `StrategySignal` 转成 `Candidate`，与「多均线」候选一起进配额，按 `market_regime` 启停。

## 第 3 节：回测细节与测试

独立回测器（纯函数）：
- 输入：全市场 `Stock` + 某策略 `detect` + `regime` 序列 + 止损/止盈/持有期上限。
- 逐日建仓；按 `entry` 建仓、`stop` 止损、`tp` 止盈、`max_hold`（默认 10 日）到期离场。
- 每笔完整交易记 `{ret, win}`，汇总：
  - `win_rate = 盈利笔数 / 总笔数`
  - `profit_factor = 平均盈利 / 平均亏损`
  - `expectancy = win_rate*avg_win - (1-win_rate)*avg_loss`
- 达标：`win_rate >= 0.45 and profit_factor >= 1.5` 或 `expectancy > 0`。
- 用 `load_daily_lows` 的当日最低价判断止损是否日内触发（与 `candidate_return_with_stop` 同思路，避免次日开盘价测止损的乐观偏差）。

测试 `tests/test_strategies.py`：
- 每个 detector 用合成行情构造确定性触发/不触发样本，断言信号正确。
- 回测器：构造已知盈亏序列，断言胜率/盈亏比/期望计算正确；断言达标判定边界（45% / 1.5 恰好过与不过）。
- 主榜并入：达标信号能转成 `Candidate` 进入配额；未达标不进入。

不做（YAGNI）：参数寻优（`--tune` / walk-forward）——一期固定参数，验证闸门只回答「该策略值不值得进主榜」。

## 验收标准

- 5 个 detector 有确定性单测，回测器有边界单测，全部通过。
- `strategies/backtest.py` 对 5 个策略各自输出胜率/盈亏比/期望，生成 `report.json`。
- 达标策略并入主榜后，`ma_backtest` 全量回归不退化（现有测试全绿）。
- 未达标策略不进主榜，且在报告中显式标注原因。
