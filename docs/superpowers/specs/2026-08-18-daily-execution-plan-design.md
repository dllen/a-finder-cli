# 每日可执行交易计划（Paper Trading）设计

**日期**：2026-08-18
**状态**：已批准

---

## 一、目标

每天产出一份可落库、可查看、可追溯、可与回测对齐的纸面交易计划：

- 基于已有 `daily_picks`，生成包含**计划价 / 仓位 / 止损 / 止盈 / RR** 的完整 plan 行
- 维护**持仓生命周期**（hold / exit），不是单日快照
- 与 `ma_backtest` 共用同一套选股与仓位逻辑，杜绝双份实现 → 策略"鲁棒"
- 看板新增"今日 Plan"页签，可查看历史 plan

**非目标**：
- 不接真实券商（保留接口，后续可扩展）
- 不做实时盘中撮合（按次日 open 模拟成交）
- 不做完整 portfolio 优化（仅仓位 sum 上限 + 个股上限）

---

## 二、整体架构

```
┌──────────────────────────────────────────────────────────────┐
│                每日流水线                                       │
├──────────────────────────────────────────────────────────────┤
│  sync-hs300-range  → 更新 kline DB                            │
│           ↓                                                    │
│  picks / ma-picks → 写入 daily_picks                           │
│           ↓                                                    │
│  plan (NEW)       → plan_builder 调用 shared_lib               │
│           ↓                       ↓                            │
│     trade_plan (NEW)         open_positions (NEW)              │
│           ↓                                                    │
│  Flask /plan tab (NEW)    +    /plan/<date> (NEW)             │
└──────────────────────────────────────────────────────────────┘

shared_lib/ (NEW) ← 纯函数层，无 IO
   - select_picks()     ← 抽自 ma_backtest
   - score_signal()     ← 抽自 ma_backtest
   - position_size()    ← 抽自 ma_backtest / risk_manager
   - stop_loss()
   - take_profit()
   - regime_factor()     ← 抽自 market_regime.py
```

**核心约束**：`ma_backtest.py` 与 `plan_builder.py` 必须 import `shared_lib`，不允许复制实现。任何参数修改只需改一处。

---

## 三、`shared_lib/`（新增）

**路径**：`shared_lib/__init__.py`, `shared_lib/strategy.py`

**原则**：纯函数，输入 = 数据 + 参数，输出 = 决策字段；不读 DB，不写 DB，不发起 IO。

### 3.1 接口签名

```python
@dataclass
class PlanRow:
    code: str
    action: str              # buy | hold | exit
    plan_price: float        # 次日 open，无 open 则今日 close × (1+slippage)
    size_pct: float          # 0~1，单只占组合比例
    stop_price: float
    tp_price: float
    rr_ratio: float          # (tp - plan_price) / (plan_price - stop)
    rationale: dict          # 评分分解 + regime + 触发条件
    status: str              # ok | failed
    reason: str              # failed 时填 sanity gate 失败原因

def select_picks(daily_picks: pd.DataFrame,
                 regime: MarketRegime,
                 params: dict) -> pd.DataFrame:
    """与 ma_backtest 同源：从 daily_picks 按 regime 过滤、配形态配额"""

def score_signal(row: pd.Series,
                 params: dict) -> dict:
    """返回 {score, components: {slope200, momentum20, ...}}"""

def position_size(code: str,
                  regime: MarketRegime,
                  risk_cfg: dict) -> float:
    """调 risk_manager.position_size 并按 regime 调整"""

def stop_loss(plan_price: float,
              atr: float,
              params: dict) -> float:
    """ATR 倍数止损，默认 2×ATR"""

def take_profit(plan_price: float,
                stop_price: float,
                rr_target: float) -> float:
    """按 RR 比推止盈"""
```

### 3.2 抽取规则

- 从 `ma_backtest.py` 复制函数体到 `shared_lib/strategy.py`
- `ma_backtest.py` 改为 `from shared_lib.strategy import ...`
- 单元测试必须验证：`shared_lib` 与原 `ma_backtest` 内嵌逻辑在相同入参下输出 bit-identical

---

## 四、`plan_builder.py`（新增）

**路径**：`plan_builder.py`（与 `pick_history.py`、`ma_backtest.py` 同级）

### 4.1 主流程

```python
def build_plan(plan_date: date,
               db_path: str,
               params: dict,
               slippage: float = 0.001) -> PlanResult:
    """主入口；返回 PlanResult，包含成功/失败行数 + sanity gate 报告"""
```

### 4.2 步骤

1. `conn` 读最新 `daily_picks`（`pick_date == plan_date`）
2. 读 `open_positions WHERE status='open'`
3. 对每条 pick：
   - `score = score_signal(row, params)`
   - `size = position_size(code, regime, risk_cfg)`
   - `stop = stop_loss(plan_price, atr, params)`
   - `tp   = take_profit(plan_price, stop, rr_target)`
   - 组装 `PlanRow(action='buy', ...)`
4. 对每条 open_position：
   - 若 `current_price ≤ stop_price` → `action='exit'`
   - 否则 → `action='hold'`
5. **Sanity gate**（每行独立 + 全局）：
   - 单行：`status='ok'`
   - 全局：`Σ size_pct ≤ 0.95`、`max(size_pct) ≤ risk_cfg['max_single']`
   - 任一失败 → 该行/全局标记 `status='failed'` + `reason`
6. 写入 `trade_plan`（**insert only**，触发器或代码层禁 UPDATE/DELETE）
7. 纸面撮合：当日 close 时，若该行 `action='buy' AND status='ok'`：
   - 用次日 open（次日 plan 时回填）或 fallback `今日 close × (1+slippage)`
   - 写入 `open_positions`（新一行）+ `trade_events`

### 4.3 不变量

- `trade_plan` 是只插入表，schema 不带 UPDATE 触发器语义；代码层 + 文档双重声明不可变
- 每次 plan 写入带 `params_hash`（sha256 of params dict），便于后续追身对比
- `open_positions.status` 由 paper-trader 维护：`open` → `closed`
- **重跑幂等**：`UNIQUE(plan_date, code, action)` + `INSERT OR IGNORE` 语义。同日重跑 plan 已存在的 ok 行不会被覆盖；failed 行可通过显式 `--force` 重新评估（默认禁用）

**默认参数**（写在 `config.py` 常量 + 看板展示，便于审计）：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `rr_target` | 2.0 | 止盈/止损比 |
| `max_single` | 0.15 | 单只最大仓位 |
| `max_total` | 0.95 | 组合最大仓位 |
| `slippage` | 0.001 | paper 撮合滑点（0.1%） |
| `stop_atr_mult` | 2.0 | 止损 ATR 倍数 |

---

## 五、数据模型

### 5.1 `trade_plan`（新增）

```sql
CREATE TABLE trade_plan (
    plan_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_date        TEXT NOT NULL,                -- ISO date
    code             TEXT NOT NULL,
    action           TEXT NOT NULL CHECK(action IN ('buy','hold','exit')),
    plan_price       REAL NOT NULL,
    size_pct         REAL NOT NULL,
    stop_price       REAL NOT NULL,
    tp_price         REAL NOT NULL,
    rr_ratio         REAL NOT NULL,
    status           TEXT NOT NULL CHECK(status IN ('ok','failed')),
    reason           TEXT DEFAULT '',
    rationale_json   TEXT NOT NULL,                -- JSON: 评分 + regime + 触发条件
    params_hash      TEXT NOT NULL,                -- sha256(params)
    created_at       TEXT NOT NULL,
    UNIQUE(plan_date, code, action)                -- 同日同票同方向幂等
);
CREATE INDEX idx_trade_plan_date ON trade_plan(plan_date);
```

### 5.2 `open_positions`（新增）

```sql
CREATE TABLE open_positions (
    pos_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    code             TEXT NOT NULL,
    entry_date       TEXT NOT NULL,
    entry_price      REAL NOT NULL,
    size_pct         REAL NOT NULL,
    stop_price       REAL NOT NULL,
    tp_price         REAL NOT NULL,
    status           TEXT NOT NULL CHECK(status IN ('open','closed')),
    close_date       TEXT,
    close_price      REAL,
    close_reason     TEXT                          -- 'stop_hit' | 'tp_hit' | 'manual' | 'plan_exit'
);
CREATE INDEX idx_open_positions_status ON open_positions(status);
CREATE INDEX idx_open_positions_code ON open_positions(code);
```

### 5.3 `trade_events`（新增，用于审计 + 追身对比）

```sql
CREATE TABLE trade_events (
    event_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_date        TEXT NOT NULL,
    code             TEXT NOT NULL,
    event_type       TEXT NOT NULL,                -- 'open' | 'close'
    price            REAL NOT NULL,
    size_pct         REAL,
    pnl_pct          REAL,                        -- close 时填：(close/entry - 1) × 100
    note             TEXT,
    created_at       TEXT NOT NULL
);
```

---

## 六、Sanity Gate

**位置**：`plan_builder.py` 内 `check_sanity(rows, regime, risk_cfg) -> list[PlanRow]`

**规则**（顺序执行，前置失败短路）：

| 检查 | 阈值 | 失败处理 |
|------|------|----------|
| 单只 size_pct ≤ 上限 | `risk_cfg['max_single']`（默认 0.15） | 该行 `status='failed'`, `reason='size_exceed_max'` |
| plan_price > 0 && stop_price > 0 | 数值合法性 | 该行 `status='failed'` |
| stop_price ≥ plan_price × 0.9 | 不允许止损在价上方过远 | 该行 `status='failed'` |
| Σ size_pct（buy 行）≤ 0.95 | 总仓位上限 | 按比例缩放所有 buy 行 size_pct 至 ≤0.95，超缩行的 `reason='scaled_to_fit'`，status 仍为 ok |

**failed 行不进入看板默认视图**，但在 `/plan/<date>` 详情页可见。

---

## 七、CLI 与命令

**新增**：

```bash
# 生成今日 plan（默认 plan_date=今日，可在 sync 流水线中自动调用）
uv run a-finder plan [--date YYYY-MM-DD] [--db hs300.db] \
                    [--rr-target 2.0] [--max-single 0.15] \
                    [--slippage 0.001] [--dry-run]

# 列出最近 N 天 plan
uv run a-finder plan list [--days 30]

# 查看某日 plan 详情（含 failed 行 + rationale_json）
uv run a-finder plan show --date YYYY-MM-DD [--include-failed]
```

**集成到现有流水线**（`sync_incremental_pick.sh` 不变；新增 `daily_plan.sh`）：

```bash
#!/usr/bin/env bash
set -euo pipefail
DB="${1:-hs300.db}"
bash sync_incremental_pick.sh "$DB" 20 picks       # 先更新 picks
uv run a-finder plan --db "$DB"                    # 再生成 plan
```

---

## 八、Web 看板扩展

**新增路由**（`web_server.py`）：

| 路径 | 方法 | 说明 |
|------|------|------|
| `/plan` | GET | 今日 plan 表格 + 30 天历史 tab |
| `/plan/<date>` | GET | 某日 plan 详情，含 failed 行 + rationale_json 弹窗 |
| `/api/plan/today` | GET | JSON，供前端刷新用 |
| `/api/plan/<date>` | GET | JSON |

**看板 UI**（在现有 Bootstrap 单页加 tab）：

- 「今日 Plan」tab：表格列 = `代码 / 方向 / 计划价 / 仓位% / 止损 / 止盈 / RR / 状态`
- 点行 → 模态框显示 `rationale_json`（评分分解 + regime）
- 「重算 plan」按钮（不联网，调用 `plan --dry-run` 预览 + 确认）

---

## 九、测试

### 9.1 单元测试

- `tests/test_shared_lib.py`：
  - `score_signal`、`stop_loss`、`take_profit`、`position_size` 在固定输入下输出固定值
  - 与 `ma_backtest` 旧逻辑的 golden 输出 bit-identical

### 9.2 集成测试

- `tests/test_plan_builder.py`：
  - 构造 5 天连续 daily_picks → 验证 hold/exit 分类正确
  - 验证 `trade_plan` 行数与每日 pick 数一致
  - 验证 `open_positions` 在 buy → hold → exit 全链路正确
  - 验证 sanity gate 触发后 failed 行被标记且不进默认视图

### 9.3 回归门禁

- `pytest` 必跑：
  - `test_shared_lib`（与 ma_backtest 一致性）
  - `test_plan_builder`（carryover + sanity）
  - 现有 `test_integration`（不破其它）

---

## 十、回测对齐（最小版）

`trade_events` 表记录每次纸面成交后的事实，附带 `plan_date`。
后续可加一个 `tests/test_plan_vs_backtest.py`：

- 取最近 30 天 plan 中的 buy 行
- 用同名单同时段跑一次 `ma_backtest`（paper mode）
- 断言：plan 的累计收益与 backtest 累计收益偏差 ≤ X%（X 待定，默认 5%）
- **当前实现**：仅写 `trade_events` 数据，不做断言门禁（避免早期误报）
- **门禁开启时机**：积累 ≥ 30 个 `trade_events` 后再加测试

---

## 十一、Skip / 推迟项

| 项 | 推迟原因 | 何时补 |
|----|----------|--------|
| 实盘券商适配器 | 用户先要纸面 | 用户明确要求时 |
| 完整 portfolio 优化（相关性 / 行业分散） | 当前已用 sum 上限 + 单股上限 | 单股上限不够用时 |
| 输入参数完整快照表 | 当前 `params_hash` 足够 | 需要审计合规时 |
| Plan vs Realized 自动对齐页 | 积累 ≥ 30 个 events 才有意义 | 第 30 次 plan 后 |
| 盘中动态 plan（盘中重算） | 仅日终计划 | 用户要求实时时 |

---

## 十二、文件清单

**新增**：

```
shared_lib/__init__.py
shared_lib/strategy.py
plan_builder.py
db/migrations/2026_08_18_trade_plan.sql
db/migrations/2026_08_18_open_positions.sql
db/migrations/2026_08_18_trade_events.sql
tests/test_shared_lib.py
tests/test_plan_builder.py
daily_plan.sh
```

**修改**：

```
ma_backtest.py            # 从 shared_lib import，不再内嵌实现
risk_manager.py           # 提供给 shared_lib.position_size 调用
market_regime.py          # 提供给 shared_lib.regime_factor
cli_layer.py              # 新增 plan 子命令
db_repository.py          # 新增 trade_plan / open_positions / trade_events 读写
web_server.py             # 新增 /plan 路由
templates/index.html      # 新增 Plan tab
```

---

## 十三、风险与边界

1. **A 股 T+1**：纸面撮合按次日 open 价是合规假设（与实盘一致）
2. **涨跌停 / 停牌**：`trade_events` 写入时 `note` 字段可标记（如 `note='limit_up_skip'`）；不阻断流程
3. **碎股**：A 股最小 100 股，`size_pct` 仅做组合占比，paper trade 不模拟具体手数
4. **回填**：plan 漏跑某天时，支持 `--date YYYY-MM-DD` 回填（与现有 daily_picks 一致）
5. **参数变更**：每次 plan 写入 `params_hash`；不同 hash 的 plan 行可共存于 `trade_plan`，但默认视图只展示最近一次 hash 的当日 plan