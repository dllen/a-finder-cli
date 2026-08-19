# 固定 200 股纸面跟踪设计

日期：2026-08-20
状态：已确认（待实现计划）

## 背景与目标

现有系统是**百分比仓位**纸面交易：`risk_manager.position_size`（0–1）→ `PlanRow.size_pct`
→ `open_positions.size_pct` → `trade_plan.size_pct`；收益此前已改为「已实现 + 未实现
mark-to-market 的 size 加权组合收益」。

本次目标：在保留百分比仓位作**参考**的同时，叠加一套**固定 200 股实际成交**语义：

- 每个 buy 标的固定买入 200 股；
- 同 code 连续多日入选则**每天累积买入**（加权成本）；
- 依据止损价/止盈价与现价，给出四层收益；
- 收益按**实际股数算金额（元）**，另给出组合收益率%。

## 决策记录

1. 仓位模型：百分比仓位（参考）+ 实际 200 股，**并行展示**。
2. 收益四层：当前浮动盈亏 / 止损止盈预期盈亏 / 已实现收益汇总 / 组合总收益（元 + 收益率%）。
3. 重复买入：**每天累积**，加权成本。
4. 止损止盈：**复用现有 regime 配置**（`risk_manager.REGIME_CONFIGS`：牛 -8%/+20%、
   熊 -5%/+10%、震荡 -5%/+8%）。
5. 组合收益率分母 = **总成本**（Σ entry_price × shares，含 open + closed）。
6. 已实现收益 = Σ `(卖出价 − 加权均价) × 平仓股数`。
7. 存量 13 个 open 持仓回填 `shares = 200`（此前每只仅一笔）。

## 数据模型

新增迁移 `db/migrations/2026_08_20_share_lots.sql`：

```sql
ALTER TABLE open_positions ADD COLUMN shares INTEGER NOT NULL DEFAULT 0;
ALTER TABLE trade_events    ADD COLUMN shares INTEGER;
ALTER TABLE trade_events    ADD COLUMN pnl_amt REAL;
```

语义变更：

- `open_positions.shares`：累积股数（>0）。
- `open_positions.entry_price`：**加权均价**。
- `open_positions.size_pct`：保留，含义变为「参考仓位」。
- `trade_events.shares`：成交股数；`pnl_amt`：close 金额盈亏（元）。
- 存量回填：`UPDATE open_positions SET shares = 200 WHERE status='open' AND shares = 0`。

## 后端改动

### `plan_builder.py`

- `PlanRow` 增加 `shares: int = 200`（`shared_lib/strategy.py`）。
- `_build_buy_rows`：每行 `shares=200`，`size_pct` 仍填参考仓位。
- **C1 幂等**：由「查 open_positions 存在即跳过」改为「查 trade_events 已有
  (code, plan_date, 'open') 即跳过」——否则同码多日无法累积。
- **C2 去重**：移除「已持有 code 不买入」，允许累积。
- **累积成交** `_paper_trade` buy 分支：
  - 该 code 已有 open 持仓 → `shares += 200`；
    `entry_price = (old_shares·old_entry + 200·fill) / (old_shares + 200)`；
    按新均价用 regime 配置重算 `stop_price` / `tp_price`。
  - 否则新建 `shares=200`。
- `_paper_trade` close 分支：`pnl_amt = (close_price − entry_price) × shares`。
- **sanity gate**：固定股数下停用「组合总仓位按比例缩放」（不可把 200 股缩成 190）；
  `max_single` 降级为参考告警；「止损高于入场」规则仍生效。

### `db_repository.py`

- `insert_open_position` 增加 `shares` 参数；新增 `accumulate_open_position`。
- `get_open_positions_with_unrealized` 扩展为每股返回：`shares`、加权均价、现价、
  `浮动盈亏(元)`、`止损预期盈亏`、`止盈预期盈亏`。
- 新增 `get_holdings_detail()`：四层收益 + 组合总收益（元）/收益率%。

收益公式（单位：元，每股 `shares` 股）：

- 当前浮动盈亏 = `(现价 − 加权均价) × shares`
- 止损预期盈亏 = `(止损价 − 加权均价) × shares`
- 止盈预期盈亏 = `(止盈价 − 加权均价) × shares`
- 已实现收益汇总 = `Σ (卖出价 − 加权均价) × 平仓股数`
- 组合总收益（元）= 已实现汇总 + Σ 当前浮动盈亏
- 组合收益率% = 组合总收益 / 总成本 × 100
- `get_recent_pnl` 改为**金额口径**：每日 = 当日平仓已实现 + 当日持仓 mark-to-market，
  单位元，按 `daily_prices` 的 trade_date 倒序取最近 N 天。

### `app.py`

- 新增 `GET /api/holdings`：返回四层收益 + 组合汇总。
- `GET /api/dashboard` 的 `pnl_5d` 与 `open_positions` 字段升级为金额口径。

## 前端改动

- **交易计划页**（`PLAN_BODY` / `PLAN_SCRIPT`）：表格加「股数」列（200）；
  「买入合计」同时显示总股数与参考仓位%。
- **Dashboard 持仓概览**：由 top-3 改为汇总（持仓数、总股数、总浮动盈亏元、
  组合总收益元 + 收益率%）。
- **新增持仓跟踪表**：计划页下方渲染 `/api/holdings`，每股列出：
  代码/名称、股数、加权均价、现价、止损价、止盈价、浮动盈亏、止损预期、止盈预期；
  顶部汇总已实现 + 组合总收益。

## 测试

- 新增 `tests/test_share_lots.py`：累积加权成本、幂等（同日不重复、异日累积）、
  已实现金额、四层收益、组合收益率、无行情/空持仓。
- 迁移回填存量 200 股的用例。
- 适配受影响的 `get_recent_pnl` 金额口径用例（`test_recent_pnl.py`、
  `test_db_repository.py`）。

## 非目标

- 不接入真实券商下单、不处理 A 股一手（100 股）整数约束之外的碎股/涨跌停。
- 不改变 `risk_manager` 的止损止盈百分比默认值。
- 不引入真实资金/手续费/印花税计算（滑点沿用 `SLIPPAGE=0.001` 仅作用于成交价）。
