# 均线选股策略优化 — 设计文档

日期：2026-08-15
状态：待评审

## 目标

让均线选股策略跑赢等权基准（`excess_return > 0`），优先相对收益，而非绝对收益或胜率。

## 现状诊断（60 只、2 年、174 天回测窗口）

| 指标 | 默认参数 | 滚动寻优后 |
|---|---|---|
| 策略累计收益 | 8.75% | 9.25% |
| 基准（等权 60 只） | 9.74% | 9.74% |
| 超额收益 | −0.99% | −0.49% |
| 日均入选 | 2.2 / 10 | 2.2 / 10 |
| 弱势市场天数 | 75 / 174（43%） | 75 / 174 |

**根因**：
1. 选股门槛过严（`trend_ok` 要求完整多头排列 + 三重过滤），日均仅入选 2.2 只；
2. 弱势市场判定占比过高（43%），进一步压缩仓位，平均仓位约 40%，而基准满仓；
3. 评分公式中 `distance200`/`distance50` 越正分越高，系统性追高，回踩等低乖离买点评分吃亏。

## 方案

### 1. 参数化候选规则

将 `candidate_rules.py` 中写死的阈值/权重抽成 `CandidateConfig`（dataclass），默认值等于现值，保证零行为变化。

### 2. 扩大寻优搜索空间

`--walk-forward` 除回测参数外，也搜索 `CandidateConfig`，目标函数仍为 `excess_return`（现有 `result_rank` 已按超额优先）。

### 3. 搜索旋钮（7 个高杠杆项）

| 旋钮 | 现值 | 搜索范围 | 意图 |
|---|---|---|---|
| `momentum_20_min` | 0.015 | 0.0 / 0.015 / 0.03 | 放松入场，增加候选 |
| `volatility_20_max` | 0.35 | 0.25 / 0.35 / 0.45 | 放松波动限制 |
| `ma10_distance_max` | 0.09 | 0.05 / 0.09 / 0.13 | 控制追高 |
| `breakout_volume_ratio_min` | 1.1 | 1.0 / 1.1 / 1.2 | 放宽突破量比 |
| `trend_follow_momentum_min` | 0.03 | 0.02 / 0.03 / 0.04 | 放宽趋势 |
| `score_distance200_weight` | 0.8 | 0.4 / 0.8 | 减少追高偏置 |
| `score_distance50_weight` | 0.6 | 0.3 / 0.6 | 减少追高偏置 |

### 4. 训练/验证防过拟合

沿用现有 walk-forward：训练窗口搜参，验证窗口独立评估。取验证期超额为正且稳健的组合，固化为默认值。

## 明确不做

- 不改机器学习、不加新指标（MACD/RSI 已在 `signal_rules.py` 层）。
- 不动回测止损/regime 逻辑（该部分已由现有 `--tune` 覆盖）。
- 不引入新依赖。

## 验证标准

- 合并回测 `excess_return > 0`；
- 验证期（未参与训练）`excess_return > 0`；
- 默认参数行为不变（`CandidateConfig` 默认值 = 现值），改动后需运行 `ma_backtest.py --db hs300.db --top 10 --days 240` 对比基线。

## 相关文件

- `candidate_rules.py`：新增 `CandidateConfig`，`ma_strategy_candidates` 接受配置。
- `ma_backtest.py`：`walk_forward_tune` / `optimize_backtest_params` 增加规则参数搜索。
