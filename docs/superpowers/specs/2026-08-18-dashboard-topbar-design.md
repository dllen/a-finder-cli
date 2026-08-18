# Dashboard 顶部条设计

日期：2026-08-18
状态：已批准

## 目标

在 `/`（每日选股结果）和 `/plan`（每日 Plan）两个页面顶部加入 dashboard 顶部条，包含 4 块摘要卡片，让用户在不滚动主表的情况下看到运行状态、今日计划、持仓、最近收益。

## 范围

- 单端点 `/api/dashboard` 返回 4 块卡片 JSON。
- 共享 `DASHBOARD_HTML` 片段，在两个页面顶部嵌入。
- 前端每 15s 轮询一次，标签页不可见时暂停。
- 新增 `daily_picks.updated_at` 列（迁移），用于"运行状态"卡。

不在范围内：服务端 SSE、WebSocket、折叠/拖拽、暗色主题。

## 卡片定义

| 卡片 | 数据源 | 关键字段 |
|---|---|---|
| 运行状态 | `daily_picks.updated_at` 的最大值 | `date`、`ago_hours`、`freshness` |
| 今日 plan | `trade_plan` WHERE `plan_date = today()` | `buy/hold/exit` 计数、`size_total`、`failed` |
| 持仓概览 | `open_positions` WHERE `status='open'` | `count`、`size_total`、`avg_unrealized_pct`、`items[]` |
| 最近 5 日收益 | `trade_events` WHERE `event_type='close'`，最近 5 个交易日 | `[{date, pnl_pct}, ...]` |

`freshness` 阈值：`< 24h` = fresh（绿）/ `< 72h` = warm（黄）/ 否则 stale（红）。

## 架构

```
Browser
  │  GET /api/dashboard (每 15s)
  │  GET /api/picks · GET /api/plan/<date>
  ▼
app.py (Flask)
  │  DASHBOARD_HTML 片段 (4 张 Bootstrap card)
  │  /api/dashboard → 一次 SQL 聚合 4 块
  ▼
hs300.db
  ├── daily_picks (+ updated_at)
  ├── trade_plan
  ├── open_positions
  └── trade_events
```

## 接口

### `GET /api/dashboard`

返回：

```json
{
  "last_refresh":   {"date": "2026-08-18", "ago_hours": 1.4, "freshness": "fresh"},
  "today_plan":     {"date": "2026-08-18", "buy": 5, "hold": 3, "exit": 2, "size_total": 0.42, "failed": 1},
  "open_positions": {"count": 3, "size_total": 0.45, "avg_unrealized_pct": 1.2, "items": [{"code":"600519","entry_date":"2026-08-10","entry_price":1500.0,"size_pct":0.10,"stop_price":1380.0,"tp_price":1740.0,"unrealized_pct":-0.5}]},
  "pnl_5d":         [{"date": "2026-08-14", "pnl_pct": 0.5}, {"date": "2026-08-15", "pnl_pct": -0.3}, {"date": "2026-08-17", "pnl_pct": 0.8}, {"date": "2026-08-18", "pnl_pct": 0.2}]
}
```

- `today_plan.size_total`：当日所有 `action='buy' AND status='ok'` 行的 `size_pct` 之和。
- `open_positions.avg_unrealized_pct`：每条 position 需关联当日 close price（取 `daily_prices` 表最新 close）；未实现涨幅用 `(current_price - entry_price) / entry_price * 100` 计算；无行情时填 `null`。
- `pnl_5d`：从 `trade_events` 取 `event_type='close'`，按 `plan_date` DESC 排序取最近 5 个不同 plan_date，每个 plan_date 取所有 close 行的 `pnl_pct` 之和（事件已逐条记录，直接 SUM，不重新计算）。无数据时返回 `[]`。

### 现有接口

`/api/dates`、`/api/picks`、`/api/refresh`、`/api/jobs/<id>`、`/api/plan/*` 全部保留，行为不变。

## 数据流

1. 页面加载 → 注入 `DASHBOARD_HTML` → `fetch('/api/dashboard')` 填卡片 → 各页面继续走原有 `loadDates` / `render` 流程。
2. 每 15s → `fetch('/api/dashboard')` → 替换卡片 innerHTML（无全页重绘）。
4. `document.visibilitychange`：标签页隐藏时清 `setInterval`，显示时立刻拉一次再启动。
5. 在 `/` 上点「同步行情并重算」完成 → 调 `refresh('/api/dashboard')`；在 `/plan` 上点「生成 plan」完成 → 同上。
6. `/api/dashboard` 出错 → 卡片区域显示「刷新失败」文字，不抛红 alert。

## 后端实现

### 新增迁移

`db/migrations/2026_08_18_daily_picks_updated_at.sql`：

```sql
ALTER TABLE daily_picks ADD COLUMN updated_at TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_daily_picks_updated_at ON daily_picks(updated_at);
```

`pick_history.py` 中所有 `INSERT INTO daily_picks` 显式写入 `updated_at`（ISO 时间戳）。迁移默认 `''` 是为了避开 SQLite ALTER TABLE ADD COLUMN 在表内有数据时拒绝非常量默认（`CURRENT_TIMESTAMP` 即便不加括号也不行），所以老数据拿不到默认时间，由 `get_last_refresh` 过滤空串只看真实更新时间。

### `db_repository.py` 新增 4 个函数

```python
def get_last_refresh(conn) -> dict | None
def get_today_plan_summary(conn, today: str) -> dict
def get_open_positions_with_unrealized(conn) -> dict
def get_recent_pnl(conn, days: int = 5) -> list[dict]
```

每个函数返回上述 JSON 子结构；`None` 或 `[]` 表示无数据。

### `app.py` 新增端点

```python
@app.get("/api/dashboard")
def dashboard():
    from datetime import date as _date, datetime as _dt
    conn = open_conn(db_path)
    try:
        last = get_last_refresh(conn)
        today = get_today_plan_summary(conn, _date.today().isoformat())
        opens = get_open_positions_with_unrealized(conn)
        pnl = get_recent_pnl(conn, days=5)
    finally:
        conn.close()
    # compute freshness
    if last:
        ago = (_dt.now() - _dt.fromisoformat(last["updated_at"])).total_seconds() / 3600
        last["ago_hours"] = round(ago, 1)
        last["freshness"] = "fresh" if ago < 24 else ("warm" if ago < 72 else "stale")
        last["date"] = last["date"]  # from daily_picks query
    return jsonify({
        "last_refresh": last,
        "today_plan": today,
        "open_positions": opens,
        "pnl_5d": pnl,
    })
```

### `DASHBOARD_HTML` 片段

4 个 Bootstrap card，行响应式：`row-cols-1 row-cols-md-2 row-cols-xl-4`。每张卡片：

- **运行状态**：标题 + 大字日期 + 小字「X 小时前」+ 三色 dot。
- **今日 plan**：标题 + 4 个数字徽章（买入 N / 持有 N / 退出 N / 失败 M）+ 合计仓位。点击跳 `/plan`。
- **持仓概览**：标题 + N 只 / 合计仓位 X% / 平均浮盈 Y% + 前 3 行小表（更多省略）。
- **最近 5 日收益**：标题 + 数字总和 + 5 个 mini bar（自绘 `<svg>`，不用第三方库）。

## 前端

新增 `dashboard.js`（嵌入页面）：

```js
function startDashboard(){
  function tick(){
    if (document.hidden) return;
    $.getJSON('/api/dashboard').done(renderDashboard).fail(function(){
      $('#dashboard').html('<div class="text-muted small">刷新失败</div>');
    });
  }
  tick();
  let h = setInterval(tick, 15000);
  document.addEventListener('visibilitychange', function(){
    if (!document.hidden) tick();
  });
}
```

`renderDashboard(json)` 把 4 块卡片 innerHTML 替换；纯字符串拼接，不引模板引擎。

## 错误处理

- `/api/dashboard` 任一块数据缺失 → 对应卡片显示「—」或「无数据」，其他块正常渲染。
- DB 锁/异常 → 返回 500，卡片区域「刷新失败」，不抛红 alert。
- `updated_at` 列在新 DB 自动加（默认 `''`），由 `pick_history` 显式写入。老 DB 通过迁移添加，老行默认 `''`；`get_last_refresh` 过滤空串，等下次 `pick_history` 写入才计入。

## 文件改动

1. `db/migrations/2026_08_18_daily_picks_updated_at.sql`（新建）：ALTER + INDEX。
2. `db_repository.py`：新增 4 个聚合函数，~80 行。
3. `pick_history.py`：INSERT 增加 `updated_at` 列，~10 处改动。
4. `app.py`：
   - 新 `DASHBOARD_HTML` 字符串，~80 行。
   - 新 `/api/dashboard` 端点，~30 行。
   - 在 `PAGE` 和 `PLAN_PAGE` 顶部插入 `<div id="dashboard"></div>` + `<script>startDashboard()</script>`。
5. `tests/test_web_plan.py`：扩 `test_dashboard` 覆盖 4 块数据形状 + freshness 计算。

## 测试

- 单元：`get_last_refresh` / `get_today_plan_summary` / `get_open_positions_with_unrealized` / `get_recent_pnl` 在临时 DB 上断言字段。
- 集成：`/api/dashboard` 端到端断言 JSON shape；`freshness` 三个边界（<24, <72, >72）。
- 手工：浏览器开 `/` 和 `/plan`，观察卡片渲染，切换标签页看轮询是否暂停。

## 非目标

- 不做服务端推送 / SSE / WebSocket。
- 不做卡片折叠、拖拽排序、暗色模式。
- 不动主表（picks / plan）结构，只增加 dashboard 区域。
- 不改 `web_server.py`。