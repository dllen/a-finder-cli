# 持仓组合 / Portfolio Tiers — 多档位组合跟踪

**日期**：2026-09-07
**状态**：设计中（待批准后实施）

---

## 一、目标

在顶部导航新增"持仓组合"入口，针对**每个资金档位**（5W–50W 共 10 档）独立跟踪 paper-trade 组合：

- **维度**：按资金档位（10 档）× 全局单策略（linyuan 默认，可切 ma-picks / buy-signals）
- **页面**：每个 tier 独立组合、独立 P&L、可点击切换
- **收益对比**：横向看各 tier 累计收益
- **自动化**：workflow 每日定时为所有 tier 生成 plan（无需人工触发）

**非目标**：

- ❌ 每个 tier 独立选策略（已确认全局单策略）
- ❌ 跨 portfolio 自动再平衡
- ❌ 新增技术指标 / 新策略
- ❌ 实时盘中监控
- ❌ 仓位风险预算改造（沿用 `RiskManager`）

---

## 二、整体架构

```
┌──────────────────────────────────────────────────────────────────┐
│                     数据底座（一次迁移）                            │
├──────────────────────────────────────────────────────────────────┤
│  trade_plan / open_positions / trade_events                       │
│    + portfolio TEXT DEFAULT 'default'                             │
│    trade_plan UNIQUE → (plan_date, portfolio, code, action)        │
│    open_positions: 加 portfolio + 唯一索引                         │
│    trade_events  : 加 portfolio                                   │
└──────────────────────────────────────────────────────────────────┘
                │
                ▼
┌──────────────────────────────────────────────────────────────────┐
│                 计划层（plan_builder 改造）                         │
├──────────────────────────────────────────────────────────────────┤
│  build_plan(db, date, params, *, portfolio="default")             │
│    → 单 tier plan + portfolio 写入                                │
│  build_all_portfolios(db, date, params, *, strategy, tiers)       │
│    → 共享 picks 读取，按 tiers 逐档调 build_plan                   │
└──────────────────────────────────────────────────────────────────┘
                │
                ▼
┌──────────────────────────────────────────────────────────────────┐
│                 CLI 暴露                                            │
├──────────────────────────────────────────────────────────────────┤
│  uv run a-finder plan build --capital 100000 --portfolio 10W      │
│  uv run a-finder plan build-all --strategy linyuan                │
│  uv run a-finder plan build-all --strategy linyuan --backfill     │
└──────────────────────────────────────────────────────────────────┘
                │
                ▼
┌──────────────────────────────────────────────────────────────────┐
│                 Web / 静态导出                                      │
├──────────────────────────────────────────────────────────────────┤
│  /portfolio 页面 + /api/portfolio/summary + /api/portfolio/<label> │
│  export_portfolio(db, out_dir) → site/data/portfolio/             │
└──────────────────────────────────────────────────────────────────┘
```

---

## 三、Schema 迁移

新文件 `db/migrations/2026_09_07_portfolio_tier.sql`：

```sql
-- trade_plan: 加 portfolio 列；UNIQUE 改 (plan_date, portfolio, code, action)
-- 用 table-swap 重建
CREATE TABLE trade_plan_new (
    plan_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio        TEXT NOT NULL DEFAULT 'default',
    plan_date        TEXT NOT NULL,
    code             TEXT NOT NULL,
    action           TEXT NOT NULL CHECK(action IN ('buy','hold','exit')),
    plan_price       REAL NOT NULL,
    size_pct         REAL NOT NULL,
    stop_price       REAL NOT NULL,
    tp_price         REAL NOT NULL,
    rr_ratio         REAL NOT NULL,
    status           TEXT NOT NULL CHECK(status IN ('ok','failed')),
    reason           TEXT DEFAULT '',
    rationale_json   TEXT NOT NULL,
    params_hash      TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    shares           INTEGER,
    UNIQUE(plan_date, portfolio, code, action)
);
INSERT INTO trade_plan_new
  SELECT plan_id, 'default', plan_date, code, action, plan_price, size_pct,
         stop_price, tp_price, rr_ratio, status, reason, rationale_json,
         params_hash, created_at, shares
  FROM trade_plan;
DROP TABLE trade_plan;
ALTER TABLE trade_plan_new RENAME TO trade_plan;

CREATE INDEX IF NOT EXISTS idx_trade_plan_portfolio_date
  ON trade_plan(portfolio, plan_date);

-- open_positions: 加 portfolio 列；同 code 多 portfolio 需要去重约束
-- 现状 status='open' 行每个 code 一条；改为 (portfolio, code) open 唯一
ALTER TABLE open_positions ADD COLUMN portfolio TEXT NOT NULL DEFAULT 'default';

CREATE UNIQUE INDEX IF NOT EXISTS uq_open_positions_portfolio_code_open
  ON open_positions(portfolio, code)
  WHERE status = 'open';

CREATE INDEX IF NOT EXISTS idx_open_positions_portfolio
  ON open_positions(portfolio);

-- trade_events: 加 portfolio 列；事件流按 portfolio 隔离
ALTER TABLE trade_events ADD COLUMN portfolio TEXT NOT NULL DEFAULT 'default';

CREATE INDEX IF NOT EXISTS idx_trade_events_portfolio
  ON trade_events(portfolio);
```

**约束**：

- 历史数据自动归 `default` portfolio，回滚易
- 不动 `daily_picks` / `pick_outcomes`（与 tier 无关）

---

## 四、plan_builder 改造

### `build_plan` 签名扩展

```python
def build_plan(
    db_path: str,
    plan_date: str,
    params: PlanParams,
    *,
    portfolio: str = "default",        # 新增
    progress: Optional[Callable] = None,
    _picks: Optional[List[Dict]] = None,  # 内部 kwarg：build_all_portfolios 注入
) -> BuildSummary:
    """生成单 tier 的 plan，写入 trade_plan[portfolio=...]。
    open_positions / trade_events 按 portfolio 隔离。"""
```

`BuildSummary` 加 `portfolio: str` 字段。

### `build_all_portfolios` 新增

```python
def build_all_portfolios(
    db_path: str,
    plan_date: str,
    params: PlanParams,
    *,
    strategy: str = "linyuan",
    tiers: Sequence[int] = CAPITAL_TIERS,
    portfolio_label_fn: Callable[[int], str] = lambda c: f"{c//10000}W",
    progress: Optional[Callable] = None,
) -> List[BuildSummary]:
    """一次性为所有 tier 跑 build_plan。
    共享一次 daily_picks 读取；每 tier 独立 shares + 独立 upsert。
    Per-tier 独立事务：一档失败不影响其他档。"""
```

---

## 五、CLI 暴露

```bash
# 现有 — 加 --portfolio
uv run a-finder plan build --capital 100000 --portfolio "10W"
uv run a-finder plan build --capital 50000  --portfolio "5W"  --strategy linyuan

# 新增 — 全档
uv run a-finder plan build-all --strategy linyuan
uv run a-finder plan build-all --strategy linyuan --tiers 50000,100000,200000
uv run a-finder plan build-all --strategy linyuan --backfill
uv run a-finder plan build-all --strategy linyuan --since 2025-01-01 --backfill
```

`--tiers` 默认走 `CAPITAL_TIERS` 全 10 档；`--backfill` 全历史日期重算；`--since` 限定回算起点。

---

## 六、repo helpers

### 已有签名扩展

```python
def upsert_trade_plan(conn, rows: Iterable[TradePlanRow]) -> int:
    """rows 字段含 portfolio，
    INSERT ... ON CONFLICT(plan_date, portfolio, code, action) DO UPDATE"""

def get_open_positions_with_unrealized(
    conn,
    *, portfolio: Optional[str] = None,  # None = 全部
) -> Dict[str, Any]:
    """portfolio 过滤；None 时跨 portfolio 聚合（用于单 portfolio 详情时传 label）"""
```

### 新增

```python
def compute_portfolio_pnl(
    conn, portfolio: str, *, initial_capital: int,
) -> Dict[str, Any]:
    """返回 {
        portfolio, initial_capital,
        cash_remaining, position_value, total_value,
        realized_pnl, unrealized_pnl,
        return_rate,
    }"""
```

实现要点：

- `cash_remaining`：从 `trade_events` 倒推（initial − Σbuy + Σsell）
- `position_value`：`open_positions.shares × 最新价`
- `realized_pnl`：已 close 仓位 `sell − buy`
- `unrealized_pnl`：`position_value − cost_basis`
- `return_rate`：`total_value / initial_capital − 1`

---

## 七、UI 持仓组合页

### 顶部导航

`_nav` 在 "每日机会" 与 "交易计划" 之间插入：

```html
<a class="nav-link" href="/portfolio">持仓组合</a>
```

### 页面结构

```
┌──────────────────────────────────────────────────────────────────┐
│ 持仓组合 [paper] 日期:[2026-09-05 ▼] 策略:[linyuan ▼]   │
├──────────────────────────────────────────────────────────────────┤
│ 5W │10W │15W │20W │25W │30W │35W │40W │45W │50W  ← 卡片(可滚) │
│+1.2│+2.1│+1.8│+2.5│+2.0│+2.4│+1.9│+2.7│+2.1│+2.3  %            │
├──────────────────────────────────────────────────────────────────┤
│ 累计收益对比 [Chart.js 柱状/折线]                                 │
├──────────────────────────────────────────────────────────────────┤
│ 选中：10W                                                         │
│ 资金 100,000 │ 已用 80,590 │ 现金 19,410 │ 利用率 80.6%          │
├──────────────────────────────────────────────────────────────────┤
│ 持仓明细 (沿用 plan 页 table)                                     │
├──────────────────────────────────────────────────────────────────┤
│ 持仓跟踪 (沿用 plan 页 holdings 区块)                              │
└──────────────────────────────────────────────────────────────────┘
```

### 路由 + API

```python
@app.route("/portfolio")
def portfolio_page(): ...

@app.route("/api/portfolio/summary")
def api_portfolio_summary():
    """所有 tier 在指定日期的汇总"""

@app.route("/api/portfolio/<label>")
def api_portfolio_detail(label):
    """单个 tier 的详细持仓 + P&L 分解"""
```

### 静态导出

`export_json.py` 新增 `export_portfolio(db_path, out_dir)`：

- `site/data/portfolio/summary.json` — 10 档汇总
- `site/data/portfolio/<label>.json` — 每档详情

---

## 八、Workflow 改动

`.github/workflows/daily-sync-export.yml` 在 `plan` step 之后追加：

```yaml
- name: Build all tier portfolios
  run: python stock_cli.py plan build-all --strategy linyuan
```

CI 完整链路：

```
sync → picks → plan (default) → plan build-all (10 tiers) → export → deploy
```

---

## 九、测试覆盖

| 测试文件 | 覆盖 |
|---|---|
| `test_migration_portfolio_tier.py` | `portfolio` 列存在 / `open_positions` PK 重建 / 索引存在 / 历史数据归 `default` |
| `test_db_repository_portfolio.py` | `compute_portfolio_pnl`（0 仓 / 全仓 / 部分 close 三态）/ `get_open_positions_with_unrealized(portfolio=)` |
| `test_plan_builder_portfolio.py` | `build_plan(portfolio=...)` 行带 portfolio；open_positions 按 portfolio 隔离 |
| `test_plan_builder_build_all.py` | 共享 picks；各 tier shares 独立；portfolio 标签正确；per-tier 事务隔离 |
| `test_cli_plan_portfolio.py` | `plan build --portfolio` / `plan build-all` parser 接受 flags |
| `test_api_portfolio.py` | JSON API 返回 shape 正确（mock DB） |
| `test_export_portfolio.py` | 写出 `summary.json` + `<label>.json` |

**目标**：测试 + 25-30 passed（全量 ≥ 260）。

---

## 十、README 更新

- 常用命令加 `plan build-all` / `portfolio` 页说明
- 新增 `## 持仓组合 / Portfolio Tiers` 章节：tier × strategy 矩阵 + `plan build-all` 工作流 + 页面截图占位

---

## 十一、风险与开放问题

1. **PK 重建迁移**：必须本地 tmp DB 验证历史数据归 `default` 正确后再上线；万一失败，旧数据可回滚（迁移脚本幂等）
2. **`build_all_portfolios` 事务**：采用 per-tier 独立事务而非全局事务，一档失败不阻塞其他档
3. **静态导出体积**：10 档 × 详情可能让 `site/data/` 变大；按 tier 分文件降低单文件大小
4. **`compute_portfolio_pnl` 边界**：现金倒推公式需测试覆盖 0 仓 / 全仓 / 部分 close 三种状态，确保不重复计算
5. **`--backfill` 开销**：全档 × 365 天重算 ~3 千万次 SQL，CI 单次 10+ 分钟；加 `--since` 限定范围；考虑只在显式 trigger 时跑
6. **Cloudflare Pages 缓存**：portfolio 数据日级刷新，沿用现有 `cache-control` 策略

---

## 十二、评审 / 实施

- 评审通过后，将本文档 commit
- 接着调 `writing-plans` skill 拆 10 个 TDD 任务
- 实施顺序：schema migration → repo helpers → plan_builder → CLI → API → UI → static export → workflow → README
- 不引入新依赖（pytest / jinja2 / Chart.js 已有）