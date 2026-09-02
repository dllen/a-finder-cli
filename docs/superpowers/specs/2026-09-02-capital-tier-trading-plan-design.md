# 按初始资金生成交易计划（资金档位切换）设计

日期：2026-09-02
状态：已确认（待实现计划）

## 背景与目标

现行纸面交易沿用「固定 200 股」语义（见
[`2026-08-20-share-lots-design.md`](./2026-08-20-share-lots-design.md)）：每个 buy
标的固定买 200 股，`size_pct`（`risk_manager.position_size`，0–1）只作参考仓位展示，
不参与实际股数计算。

本次目标：**把股数改为按初始资金计算的真仓位**，提供 5W / 10W / 20W / 30W / 50W
五档初始资金，页面可切换档位并查看对应交易计划：

- 股数 = f(资金, 仓位%, 价格)，按 A 股一手（100 股）向下取整；
- 资金只放大/缩小**股数**，不改选股、止损、止盈、仓位%（这些都资金无关，保持不变）；
- 端到端：`build_plan` 按资金算出的股数落库并驱动纸面成交/持仓跟踪；
- 单一活跃资金：`open_positions` 仍是单一组合，不加资金维度。

这是对 `2026-08-20-share-lots-design.md`「固定 200 股」决策的延伸/取代，并**正式引入
A 股一手整数约束**（此前该约束被列为非目标）。

## 决策记录

1. **资金模型**：单一活跃资金（参数），非 5 个并行纸面组合。切换档位 = 换资金重算
   「查看」；「生成 plan」用当前选中档位真实建仓。
2. **股数公式**：单票预算 = 资金 × 仓位%；股数 = `floor(预算 / 价格 / 100) × 100`。
3. **不足一手**：返回 0 股，页面保留该行并标注「资金不足一手」，不跳过、不超配。
4. **总仓位**：各票按自己 `size_pct` 独立算股数，不做总仓位缩放（沿用现有「不缩放
   总仓位」设计）。汇总区显示已用资金/现金余额/资金利用率，利用率 >100% 红色高亮。
5. **默认档位**：10W（`DEFAULT_CAPITAL = 100000`）。
6. **「生成 plan」随选中档位建仓**：`POST /api/plan/build` 接收 `capital`，透传
   `build_plan`。
7. **资金参与缓存键**：`params_hash` 含 `capital`（`params` dict 里新增），不同资金
   得不同缓存，避免换资金重算被缓存短路。
8. **同日已成交不重成交**：沿用 C1 幂等（`trade_events` 的 `(code, plan_date, 'open')`
   门禁）。换资金**只影响未来新买单**，当日已成交订单不改、既有持仓保留历史股数。

## 核心公式（单一事实来源）

`shared_lib/strategy.py` 新增纯函数：

```python
LOT_SIZE = 100  # A股一手

def size_shares(capital: float, size_pct: float, price: float) -> int:
    """预算 = capital*size_pct，股数 = floor(预算/价格/100)*100；不足一手返回 0。"""
    if capital <= 0 or size_pct <= 0 or price <= 0:
        return 0
    budget = float(capital) * float(size_pct)
    return int(budget // (price * LOT_SIZE)) * LOT_SIZE
```

前端 `PLAN_SCRIPT` 内的 JS 镜像（与 Python 同式，注释互相指向，禁止单边改动）：

```js
function sizeShares(capital, sizePct, price){
  if (!(capital > 0) || !(sizePct > 0) || !(price > 0)) return 0;
  return Math.floor(capital * sizePct / (price * 100)) * 100;
}
```

## 数据模型

不改表结构、不加列、不加迁移。`trade_plan.shares` / `open_positions.shares` /
`trade_events.shares` 已在 `2026_08_20_share_lots.sql` 就位，继续复用：

- `trade_plan.shares`：最近一次建仓所用资金的股数（upsert 保证最新）。
- `open_positions.shares`：累积股数，由资金感知的 `_paper_trade` 写入。
- 语义不变之处：`open_positions.entry_price` 仍为加权均价，`size_pct` 仍为参考仓位。

## 后端改动

### `config.py`

- 新增 `CAPITAL_TIERS = [50000, 100000, 200000, 300000, 500000]`
- 新增 `DEFAULT_CAPITAL = 100000`

### `shared_lib/strategy.py`

- 新增 `LOT_SIZE` 与 `size_shares()`（见上）。

### `plan_builder.py`

- `build_plan(plan_date, db_path, params, slippage)` 读取 `params["capital"]`，缺省
  `DEFAULT_CAPITAL`。
- `_build_buy_rows(..., capital)`：`shares = size_shares(capital, cfg.position_size,
  plan_price)`，取代硬编码 `shares=200`。`size_pct` 仍填 `cfg.position_size`。
- `_paper_trade` buy 分支：跳过 `shares <= 0` 的买单（不建 0 股仓）。
- sanity gate 不变（固定股数下的「不缩放」逻辑继续适用；「止损高于入场」仍是唯一硬
  失败）。

### `db_repository.py`

- `insert_trade_plan`：`INSERT OR IGNORE` 改为
  `INSERT ... ON CONFLICT(plan_date, code, action) DO UPDATE SET
  plan_price=excluded.plan_price, size_pct=excluded.size_pct,
  stop_price=excluded.stop_price, tp_price=excluded.tp_price,
  rr_ratio=excluded.rr_ratio, status=excluded.status, reason=excluded.reason,
  rationale_json=excluded.rationale_json, params_hash=excluded.params_hash,
  shares=excluded.shares`。保证换资金重建时 DB 行与最近一次建仓一致，缓存命中返回的
  股数不陈旧。`created_at` 不动。

### `app.py`

- `_start_plan_job(db_path, plan_date, capital)`：`params` 里加 `"capital": capital`。
- `POST /api/plan/build`：body 读 `capital`（整数），校验落在 `CAPITAL_TIERS` 内，
  非法/缺省回退 `DEFAULT_CAPITAL`。
- `GET /api/plan/<date>`：不改 —— 展示层股数由前端按 `size_pct`+`plan_price` 重算，
  服务端不另算（静态站点无后端，需同一套 JS 逻辑）。

## 前端改动（`app.py` 的 `PLAN_BODY` + `PLAN_SCRIPT`，同时作用于静态 `site/plan.html`）

- 筛选栏加**资金档位切换器**：5W/10W/20W/30W/50W 胶囊按钮（Bootstrap 风格，沿用现有
  工具栏），默认选中 10W。档位列表在 `PLAN_SCRIPT` 内以常量定义，注释指向 `config.py`
  的 `CAPITAL_TIERS`。
- `PLAN_STATE` 加 `capital`（默认 `DEFAULT_CAPITAL`）。
- `row()` / 移动端卡片：buy 行股数改为 `sizeShares(capital, size_pct, plan_price)`；
  hold/exit 行保留存储股数。buy 行 `size_pct > 0` 且算得 0 股时，股数格显示「资金不足
  一手」（警告样式）。
- 汇总条新增：`资金 XW · 已用 ¥Y (Z%) · 现金 ¥C`。`已用 = Σ(shares × plan_price)`（buy
  且 ok），`现金 = capital − 已用`，`利用率 = 已用/capital`；利用率 >100% 或现金 <0 时
  红色高亮。保留现有「买入合计仓位%」。
- 切换档位只重绘当前视图，不触发请求/建仓（`drawPlan` 复用已加载数据）。

## 静态站点（`export_json.py`）

不改导出逻辑。`site/data/plan-<date>.json` 已含 `size_pct` + `plan_price`（资金无关），
前端重算即可；档位切换器是纯前端控件，随 `PLAN_BODY/PLAN_SCRIPT` 自动进入 `plan.html`。
`export_json.py` 的 `_strip_write_controls` 会按需移除「生成 plan」写入按钮，切换器
（非 write-control）保留。

## 测试

- 新增 `size_shares` 单测（`tests/test_sizing.py` 或并入现有）：整手取整、不足一手=0、
  非正参数=0、不同资金不同股数。
- 更新 `tests/test_share_lots.py`：200 股断言 → 资金感知股数。默认 10W、BULL、
  score=2.0（`position_size = 0.15×(0.5+0.667×0.5) = 0.125`）、buy 价 100 元时，
  `size_shares(100000, 0.125, 100) = floor(12500/10000)×100 = 100` 股，据此更新
  `test_build_plan_buys_200_shares_and_accumulates_next_day` 的预期（首日 100、次日累积
  200）。
- 新增 `build_plan` 带 `capital` 端到端：不同 `capital` 落库/成交股数不同；`shares=0`
  的 buy 不建仓；换资金重建后 `trade_plan.shares` 为最新。
- 新增 upsert 用例：同 `(plan_date, code, action)` 二次 build 更新 `shares` 而非忽略。

## 非目标

- 不引入并行账户 / `open_positions` 加资金维度。
- 不改 `risk_manager` 止损止盈百分比、不进化 `size_pct` 规则。
- 不处理碎股/涨跌停/科创板 200 股最低申报、手续费/印花税（滑点沿用 `SLIPPAGE=0.001`
  仅作用于成交价）。
- 不把资金档位做成可自由输入，固定五档。

## 已知限制

- 换资金重建同日计划不会追溯改当日已成交订单（C1 幂等）；新资金从下一交易日新买单生效。
- 独立算股数下，多票 `size_pct` 之和可能 >100%（现有设计本就如此），此时汇总区
  利用率 >100% 高亮提示，不自动缩仓。
- 小资金（5W）+ 高价股（如 1400+ 元/股）在 10% 仓位下买不起一手，会呈现较多「资金不足
  一手」行，属预期。
