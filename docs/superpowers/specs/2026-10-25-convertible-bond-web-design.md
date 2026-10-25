# 可转债 Web 页面 — 设计文档

**日期**：2026-10-25
**状态**：设计中

---

## 一、背景与问题

可转债策略已实现完整 CLI（`cb-picks` / `cb-alerts` / `cb-add` / `cb-positions`）与完整数据层（`cb_universe` / `cb_factors` / `cb_picks` / `cb_positions` 四张表 + `cb_repository.py` / `cb_data_provider.py` / `cb_pipeline.py` / `strategies/convertible.py`），但 **Web 层完全没有接入**：

| 层 | 状态 |
|---|---|
| CLI | ✅ 4 个子命令 |
| 数据表 | ✅ 4 张表（`cb_universe` 1065 行已填充） |
| Web 模板 | ❌ 无 `templates/cb.html` |
| Web API | ❌ 无 `/api/cb/*` 路由 |
| 前端 JS | ❌ 无 `static/js/cb.js` |
| 静态导出 | ❌ `export_json.py` 不导出转债数据 |
| 导航 | ❌ `templates/components/nav.html` 无转债入口 |

结果：线上站点（ycu.space）用户完全看不到转债数据。

另注：`cb_factors` / `cb_picks` / `cb_positions` 当前均为 0 行 —— 转债榜单还没真正跑过落库。

---

## 二、目标

1. 新建独立转债页面 `/cb`，展示**转债榜单**、**持仓**、**强赎预警**三个板块
2. 新增 `/api/cb/*` 路由供 Flask 模式使用
3. `export_json.py` 导出转债数据，静态站（gh-pages / ycu.space）同样可见
4. 首页 dashboard 增加转债摘要卡片，点击跳转 `/cb`
5. 导航栏增加"可转债"入口

**非目标**：
- ❌ 不做转债回测（历史 K 线不可靠，见 spec 可转债部分）
- ❌ 不改 `cb_repository.py` 的数据模型
- ❌ 不改现有 4 个 CLI 命令的行为
- ❌ 不做转债的 TUI

---

## 三、架构

```
                    ┌──────────────────────────────┐
                    │  app.py  (/cb, /api/cb/*)     │
                    └───────────────┬──────────────┘
                                    │ render_template("cb.html")
                                    ▼
                    ┌──────────────────────────────┐
                    │  templates/cb.html           │
                    │  + components/cb_cards.html  │
                    └───────────────┬──────────────┘
                                    │ <script type="module">
                                    ▼
                    ┌──────────────────────────────┐
                    │  static/js/cb.js              │
                    │  → import { fetchCbPicks,     │
                    │           fetchCbPositions,   │
                    │           fetchCbAlerts }      │
                    │    from './api.js'             │
                    └───────────────┬──────────────┘
                                    │
                    ┌───────────────┴──────────────┐
                    ▼                              ▼
        ┌────────────────────┐        ┌────────────────────┐
        │  API 模式           │        │  静态模式           │
        │  /api/cb/*          │        │  data/cb-*.json     │
        │  → cb_repository    │        │  （export_json 生成）│
        └────────────────────┘        └────────────────────┘
```

**关键设计**：`api.js` 已实现 API/静态双模式抽象（`isStatic()` + `dsPath()`），转债只需在其上加 4 个函数，即可同时服务 Flask 和静态站。

---

## 四、数据流

### 4.1 榜单数据

```
cb_picks (DB)  ──get_cb_picks(conn, trade_date)──▶  /api/cb/picks?date=
                                                        │
                          export_json ──▶ data/cb-picks-<date>.json
```

榜单**只读 DB**，不实时计算（转债全市场扫描 1065 只很慢，页面不能等）。

### 4.2 持仓数据

```
cb_positions (DB) ──get_cb_positions(conn)──▶  /api/cb/positions
```

### 4.3 预警数据 ⚠️ 特殊

`evaluate_alerts()` 需要**实时行情**（现价、强赎状态、强赎触发价、溢价率），这些不在 DB 里，只在 `cb_data_provider` 实时抓取。

**设计决策**：
- `/api/cb/alerts` 实时抓取（仅针对持仓的少数几只，秒级返回）
- 静态模式导出 `data/cb-alerts.json`（导出时点抓一次，标注 `generated_at`）
- 无持仓时直接返回空数组，不触发任何网络请求

### 4.4 摘要卡片（首页 dashboard）

```
/api/dashboard 增加 cb 字段：
{
  "cb_picks_count": 20,          # 最新榜单条数
  "cb_positions_count": 0,       # 持仓数
  "cb_alerts_count": 0,          # 预警数
  "cb_latest_date": "2026-10-25" # 最新榜单日期
}
```

只读 DB，不触发网络。预警数用 DB 中 `cb_positions` 的静态判断（无持仓则为 0），避免首页每次都抓网络。

---

## 五、API 路由设计

| 路由 | 方法 | 返回 | 说明 |
|---|---|---|---|
| `/cb` | GET | HTML | 转债页面 |
| `/api/cb/picks?date=YYYY-MM-DD` | GET | `{date, rows: [...]}` | 榜单，默认取最新日期 |
| `/api/cb/positions` | GET | `{rows: [...]}` | 持仓 |
| `/api/cb/alerts` | GET | `{alerts: [...], generated_at}` | 预警（实时抓取） |
| `/api/cb/dates` | GET | `{dates: [...]}` | 有榜单的日期列表 |

### 返回结构

**`/api/cb/picks`**
```json
{
  "date": "2026-10-25",
  "rows": [
    {
      "rank": 1, "code": "113046", "name": "金田转债",
      "price": 101.17, "total_score": 83.1,
      "a_score": 79, "b_score": 85, "c_score": 87,
      "size_pct": 0.15, "shares": 140,
      "stop_price": 93.08, "tp_price": 121.4,
      "reason": "债底保护充分 + 溢价率偏低 + 距强赎安全",
      "risk_note": null, "factor_counts": "{\"a\":3,\"b\":4,\"c\":5}"
    }
  ]
}
```

**`/api/cb/positions`**
```json
{
  "rows": [
    {"code": "113059", "name": "..." , "stock_code": "601...", "stock_name": "...",
     "entry_date": "2026-10-20", "entry_price": 108.5, "shares": 50,
     "stop_price": 99.82, "tp_price": 130.2, "note": ""}
  ]
}
```

**`/api/cb/alerts`**
```json
{
  "generated_at": "2026-10-25T13:00:00",
  "alerts": [
    {"code": "113059", "name": "...", "shares": 50,
     "level": "emergency", "level_label": "🔴 紧急",
     "price": 120.5, "message": "已公告强赎，立即清仓"}
  ]
}
```

---

## 六、前端页面设计

### 6.1 `templates/cb.html`

```
┌──────────────────────────────────────────────────┐
│ 可转债                        [日期 ▾] [刷新]     │
├──────────────────────────────────────────────────┤
│ ┌──────────┐ ┌──────────┐ ┌──────────┐          │
│ │ 榜单 20  │ │ 持仓 0   │ │ 预警 0   │  ← 摘要卡 │
│ └──────────┘ └──────────┘ └──────────┘          │
├──────────────────────────────────────────────────┤
│ 持仓与预警                                      │
│ ┌──────────────────────────────────────────┐    │
│ │ [预警列表] 或 [持仓表格]                  │    │
│ └──────────────────────────────────────────┘    │
├──────────────────────────────────────────────────┤
│ 转债榜单                                        │
│ ┌──────────────────────────────────────────┐    │
│ │ 排名 代码 名称 现价 评分 债底 股性 条款     │    │
│ │ 仓位 股数 止损 止盈 理由 风险              │    │
│ └──────────────────────────────────────────┘    │
└──────────────────────────────────────────────────┘
```

### 6.2 三段式信息层级

1. **摘要卡**（3 个）：榜单数 / 持仓数 / 预警数 —— 一眼看到状态
2. **持仓与预警**（上下）：预警优先（有预警时置顶显示，红/橙/黄/灰四色），其次持仓表格
3. **转债榜单**：主表，桌面端表格 + 移动端卡片

### 6.3 榜单表格列

| 列 | 内容 | 来源 |
|---|---|---|
| 排名 | 徽章 | `rank` |
| 代码 / 名称 | | `code` / `name` |
| 现价 | 元 | `price` |
| **总评分** | 数字 + 评分分档色 | `total_score` |
| 债底 / 股性 / 条款 | 三个分项分 | `a_score` / `b_score` / `c_score` |
| 仓位 / 股数 | % / 张 | `size_pct` / `shares` |
| 止损 / 止盈 | 元 | `stop_price` / `tp_price` |
| 理由 | 文本 | `reason` |
| 风险 | 文本（可折叠） | `risk_note` |

评分分档复用 `table.js` 已有的 `scoreBand()`（≥85% hot / ≥70% warm / ≥50% mild / 其余 neutral）。

### 6.4 `static/js/cb.js`

复用现有模块：

```javascript
import { fetchCbPicks, fetchCbPositions, fetchCbAlerts, fetchCbDates } from './api.js';
import { $, fmt, esc, fillDateSelect, setStatus } from './dom.js';
import { scoreBand } from './table.js';
```

导出函数：`renderSummary()` / `renderPositions()` / `renderAlerts()` / `renderPicks()` / `startCbPage()`

---

## 七、静态导出（`export_json.py`）

新增导出物：

| 文件 | 内容 |
|---|---|
| `data/cb-dates.json` | 有榜单的日期列表 |
| `data/cb-picks-<date>.json` | 各日期榜单 |
| `data/cb-positions.json` | 持仓 |
| `data/cb-alerts.json` | 预警（导出时点抓取一次，带 `generated_at`） |

静态模式下 `api.js` 的 `fetchCbPicks(date)` 读 `data/cb-picks-<date>.json`，其余读对应文件。

---

## 八、文件变更

| 文件 | 变更 | 类型 |
|---|---|---|
| `app.py` | `/cb` 路由 + 5 个 `/api/cb/*` + dashboard 加 cb 摘要 | 修改 |
| `api.py`（`static/js/`） | 加 4 个 `fetchCb*` 函数 | 修改 |
| `static/js/cb.js` | 转债页面逻辑 | 新建 |
| `templates/cb.html` | 转债页面模板 | 新建 |
| `templates/components/nav.html` | 加"可转债"入口 | 修改 |
| `templates/components/dashboard_cards.html` | 加转债摘要卡 | 修改 |
| `export_json.py` | 导出转债数据 | 修改 |
| `tests/test_cb_web.py` | 新测试 | 新建 |

**零改动**：`cb_repository.py` / `cb_data_provider.py` / `cb_pipeline.py` / `strategies/convertible.py` / CLI。

---

## 九、测试策略

`tests/test_cb_web.py`：

| 测试 | 验证点 |
|---|---|
| `test_cb_page_renders` | `/cb` 返回 200，含"可转债"标题 |
| `test_cb_page_no_jquery_no_cdn` | 无 jQuery / 无 CDN |
| `test_cb_page_loads_module` | 引用 `js/cb.js` 模块 |
| `test_api_cb_picks_returns_rows` | 返回榜单，字段完整 |
| `test_api_cb_picks_empty_db` | 空库返回空列表不报错 |
| `test_api_cb_positions_returns_rows` | 持仓列表 |
| `test_api_cb_alerts_no_positions` | 无持仓 → 空数组且不抓网络 |
| `test_api_cb_alerts_with_position` | 有持仓 → 返回预警 |
| `test_api_cb_dates` | 日期列表 |
| `test_dashboard_includes_cb_summary` | dashboard 含 cb 字段 |
| `test_export_includes_cb_files` | 导出生成 cb-*.json |
| `test_nav_has_cb_link` | 导航含转债入口 |

---

## 十、风险控制

| 风险 | 缓解 |
|---|---|
| `/api/cb/alerts` 实时抓取慢，拖慢页面 | 仅对持仓票数抓取；无持仓直接返回空，不发请求；前端并行加载三段 |
| 转债榜单数据陈旧（`cb_picks` 0 行） | 页面显示"暂无数据"引导跑 CLI；摘要卡标注最新日期 |
| 静态模式 alerts 数据是快照 | 响应带 `generated_at`，前端显示"快照时间" |
| 新增 4 个 API 路由影响现有页面 | 只增不改，现有 API 不动；全量测试验证无回归 |
| 移动端表格过宽 | 复用 picks 页的桌面表格 + 移动卡片双模式 |

---

## 十一、实施顺序

1. `static/js/api.js` 加 4 个 `fetchCb*`（含静态路径分支）
2. `app.py` 加 5 个 `/api/cb/*` 路由 + `/cb` 页面路由
3. `templates/cb.html` + `templates/components/nav.html` + `dashboard_cards.html`
4. `static/js/cb.js` 页面逻辑
5. `export_json.py` 转债导出
6. `tests/test_cb_web.py` 全部测试
7. 全量测试 + 实跑生成转债榜单 + 部署

---

## 十二、评审 / 实施

- 范围已与用户确认：**完整转债页**（榜单 + 持仓 + 预警三个板块）
- 接着调 `writing-plans` 拆实施计划
