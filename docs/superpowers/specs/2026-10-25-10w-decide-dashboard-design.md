# 10w 决策台（/decide）— 设计文档

**日期**：2026-10-25
**状态**：设计已确认（方案 A，四节逐节通过）

---

## 一、目标

新建 `/decide`「10w 决策台」页面，把《10w 本金决策体系报告》的规则落地为可执行工具：

1. **今日执行清单**——按报告规则（10w = 8w 股 + 2w 债，Top 8，单票 ≤10%）展示当日该买/该持的票、股数、止损止盈、金额占用
2. **实际持仓录入**——用户手工录入真实持仓（股票 + 转债）
3. **执行对照表**——计划 vs 实际的偏差自动高亮（漏买/多买/超配/缺止损/跌破止损未执行/达止盈未卖…）

**来源**：报告第 4 章（仓位规则）、第 5 章（退出规则）、第 7.2 节 P0 固化清单。

**非目标**：
- ❌ 不改 plan 生成逻辑（10W 档位已存在，只是展示层按报告规则解读）
- ❌ 不做转债的股债搭配回测（无可靠历史 K 线）
- ❌ 不做择时 / regime 对照实验

---

## 二、现状与关键约束

| 现状 | 影响 |
|---|---|
| `trade_plan` 10W 档位 2026-09-11 有 434 行，去重后仅 31 只（8 hold + 23 buy） | **所有读取必须按 code 去重**，否则"买入 23 只"是假的 |
| 10W 档位 `size_pct = 0.11875`（11.875%），是 8 只等权 | 报告建议单票 ≤10%，展示层需标注"超出上限"作为偏差 |
| `trade_plan` 最新到 2026-09-11（10W 档位）；`default` 档位到 2026-10-23 | 10W 档位需重新生成才能有最新数据（CI workflow 会做） |
| `pick_outcomes` 空（无真实成交记录） | "实际持仓"必须由用户录入，新表 |
| 静态站无 POST 能力 | 静态模式隐藏写按钮，对照用导出快照 |

---

## 三、方案（方案 A：Web 表单录入 + 自动对照）

### 3.1 数据模型

新表 `real_positions`（股票实际持仓）：

```
code          TEXT     PRIMARY KEY
name          TEXT
shares        INTEGER NOT NULL
entry_price   REAL    NOT NULL
stop_price    REAL              -- 可空 → 判定"缺止损"
entry_date    TEXT
note          TEXT
updated_at    TEXT
```

新表 `real_cb_positions`（转债实际持仓）：

```
code, name, shares, entry_price, stop_price, entry_date, note, updated_at
```

> 股票与转债分表：两者都是 6 位代码，理论上可能撞号，分表最安全。

迁移文件：`db/migrations/2026_10_25_real_positions.sql`（遵循 CLAUDE.md：新 schema 走迁移文件，不改 `db_schema.py`）。

### 3.2 API 路由

| 路由 | 方法 | 说明 |
|---|---|---|
| `/api/decide/plan?date=` | GET | 当日 10W 档位 plan（按 code 去重） |
| `/api/decide/positions` | GET / POST | 读 / 写实际持仓（股票 + 转债） |
| `/api/decide/positions/<code>` | DELETE | 删除一条 |
| `/api/decide/comparison` | GET | 对照结果（偏差列表） |

### 3.3 对照判定规则

```
输入： plan（去重后当日 buy/hold）、actual（股票+转债录入）、config=PORTFOLIO_10W
输出： deviations: [{severity, code, name, kind, message, actual, expected}]

┌──────────────────────┬─────────┬──────────────────────────────┐
│ 偏差类型             │ 级别    │ 判定                          │
├──────────────────────┼─────────┼──────────────────────────────┤
│ 漏买                 │ info    │ plan 有 buy，actual 无         │
│ 多买（计划外）        │ warn    │ actual 有，plan 无             │
│ 持仓数超限            │ danger  │ 股票只数 > 8                  │
│ 单票超上限            │ danger  │ 单票金额/总股本 > 10%          │
│ 缺止损                │ warn    │ actual 有该票但 stop_price 空  │
│ 跌破止损未执行        │ danger  │ 最新收盘价 ≤ stop_price        │
│ 总仓位超限            │ danger  │ 股票占用/10w > 75%            │
│ 转债仓超配            │ warn    │ 转债占用 > 2w                 │
│ 达到止盈未卖          │ info    │ 最新收盘价 ≥ plan.tp_price     │
│ 计划股数不符          │ info    │ actual.shares ≠ plan.shares    │
└──────────────────────┴─────────┴──────────────────────────────┘
```

**config.py 新增（纯新增，不动现有常量）**：

```python
PORTFOLIO_10W = {
    "total_capital": 100000.0,
    "stock_ratio": 0.8,
    "cb_ratio": 0.2,
    "max_positions": 8,
    "max_single_weight": 0.10,
    "max_stock_exposure": 0.75,
    "cb_capital": 20000.0,
    "max_cb_positions": 3,
}
```

**决策**：
1. 对照判定在**服务端**（`decide_service.py` 纯函数）——规则是业务逻辑，静态导出可复用同一实现
2. 股数差异只 `info` 不阻断（用户可能主动加减仓）
3. "跌破止损"用最新收盘价判定（与 `get_open_positions_with_unrealized` 口径一致）
4. **去重是硬要求**，所有 plan 读取按 code 去重

### 3.4 页面与前端

```
templates/decide.html        新建
static/js/decide.js          新建（ES6 module）
```

页面结构：

```
┌────────────────────────────────────────────────────────┐
│ 10w 决策台                    [日期 ▾]  [规则说明 ?]  │
├────────────────────────────────────────────────────────┤
│ [股票占用][转债占用][现金余额][持仓数][偏差数]  5 摘要卡 │
├────────────────────────────────────────────────────────┤
│ ① 今日执行清单                                          │
│   买入/持有 · 计划价 · 建议股数 · 止损 · 止盈 · 金额     │
├────────────────────────────────────────────────────────┤
│ ② 执行对照                                              │
│   [严重度][类型][代码 名称][说明][实际 vs 期望]           │
├────────────────────────────────────────────────────────┤
│ ③ 我的实际持仓                    [＋ 添加持仓]          │
│   可编辑表格：代码·名称·股数·成本价·止损·删除             │
│   我的转债持仓                      [＋ 添加转债]         │
└────────────────────────────────────────────────────────┘
```

**三段顺序**：今日该做什么 → 我做对了吗 → 记录我的实际（录入是对照的输入，放最后）。

**静态模式**：
- 隐藏「添加持仓 / 删除」按钮，保留清单 + 对照 + 只读持仓表
- 对照数据由 `export_json.py` 导出时计算快照 → `data/decide-<date>.json`

**复用**：`api.js` 加 4 个 `fetchDecide*`；`dom.js` 的 `$`/`fmt`/`esc`；`app.css` 现有 `.app-card`/`.app-table`/`.empty-state`/`.kv-grid`。

---

## 四、文件变更

### 新增

| 文件 | 职责 |
|---|---|
| `db/migrations/2026_10_25_real_positions.sql` | 真实持仓表（股票 + 转债） |
| `real_position_repository.py` | 读写实际持仓（SAVEPOINT 原子化，同 `cb_repository.py` 风格） |
| `decide_service.py` | 对照判定（纯函数） |
| `templates/decide.html` | 页面模板 |
| `static/js/decide.js` | 页面逻辑 |
| `tests/test_decide.py` | 判定逻辑单元测试 |
| `tests/test_decide_web.py` | API + 模板测试 |

### 修改

| 文件 | 变更 |
|---|---|
| `config.py` | 新增 `PORTFOLIO_10W` 块（纯新增） |
| `app.py` | `/decide` 路由 + 4 个 `/api/decide/*` |
| `static/js/api.js` | 4 个 `fetchDecide*`（双模式） |
| `export_json.py` | 导出 `data/decide-<date>.json` + `decide.html` |
| `templates/components/nav.html` | 加"决策台"入口 |

---

## 五、测试策略

### `tests/test_decide.py`（纯逻辑，最关键）

| 测试 | 验证点 |
|---|---|
| `test_no_deviations_when_perfect_match` | 计划=实际 → 无偏差 |
| `test_detects_missing_buy` | 漏买 |
| `test_detects_unplanned_position` | 多买 |
| `test_detects_too_many_positions` | 持仓数 >8 |
| `test_detects_single_weight_over_limit` | 单票 >10% |
| `test_detects_missing_stop_loss` | 缺止损 |
| `test_detects_stop_loss_breached` | 跌破止损 |
| `test_detects_total_exposure_over_limit` | 总仓位 >75% |
| `test_detects_cb_over_allocation` | 转债超 2w |
| `test_detects_take_profit_hit` | 达止盈 |
| `test_duplicate_plan_rows_deduped` | 重复行去重（用 2026-09-11 真实重复形态造数据） |
| `test_cash_remaining_calculation` | 现金 = 10w − 股票 − 转债 |

### `tests/test_decide_web.py`

| 测试 | 验证点 |
|---|---|
| `test_decide_page_renders` | 200 + 无 jQuery/CDN + ES6 module |
| `test_nav_has_decide_link` | 导航入口 |
| `test_api_decide_plan_returns_deduped` | plan 去重 |
| `test_api_decide_positions_crud` | 增删查 |
| `test_api_decide_comparison` | 对照返回偏差 |
| `test_export_includes_decide_files` | 静态导出产物 |

---

## 六、风险控制

| 风险 | 缓解 |
|---|---|
| `trade_plan` 重复行导致买入只数虚高 | 判定前强制去重 + 专门测试 |
| 静态站无法录入 | 静态模式隐藏写按钮（平台既有约定） |
| `real_positions` 与 `trade_plan` 语义混淆 | 表名/模块名明确带 `real_`；文档说明 |
| 对照规则与报告漂移 | 规则集中在 `config.PORTFOLIO_10W` + `decide_service.py` 单一实现 |
| 迁移在存量库失败 | 新表无外键、无 NOT NULL 冲突；`open_db` 自动应用迁移 |
| 10W 档位数据陈旧（最新 2026-09-11） | CI workflow 会重建 10 档位；面板显示所选日期 |

---

## 七、验证计划

1. 单元测试全绿（判定规则 12 项 + API/模板 6 项）
2. `/decide` 页面 200，含清单/对照/录入三段
3. 手工录入 3 只真实持仓 → 对照表正确高亮偏差
4. 静态导出生成 `data/decide-*.json` + `decide.html`，静态站只读可用
5. 全量测试无回归

---

## 八、评审 / 实施

- 决策已确认：录入真实持仓 / 新建 `/decide` 页 / 只做展示层 / 方案 A
- 设计四节（数据模型与 API / 判定规则 / 页面与前端 / 文件清单与测试）逐节获用户确认
- 接着调 writing-plans 拆实施计划
