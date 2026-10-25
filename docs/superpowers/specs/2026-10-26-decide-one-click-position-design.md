# 决策台「一键建仓」— 设计文档

**日期**：2026-10-25
**状态**：设计已确认（方案 A，三节逐节通过）

---

## 一、目标

在决策台「待建仓清单」加操作按钮，支持**一键建仓**：把计划中的票直接写入实际持仓（D1）。

**决策（用户确认）**：
- 成本价来源：**弹窗预填计划价，可改**
- 按钮粒度：**行内「建仓」+ 顶部「全部建仓」**
- 已建仓处理：**显示「已持有」并禁用**，「全部建仓」只建未持有的
- 全部建仓交互：**一次确认后批量提交**（不逐只弹窗）

**非目标**：
- ❌ 不做加仓累加（已持有一律禁用，需先删除再重建）
- ❌ 不改 Worker（零后端改动）
- ❌ 不做成交价自动获取（需实时行情，静态站没有）
- ❌ 不做部分止盈/减仓

---

## 二、方案（方案 A：纯前端批量，复用现有 API）

```
行内「建仓」按钮
  → 通用持仓表单（预填 plan 行，成本价/股数可改）
  → postDecidePosition() 单笔

顶部「全部建仓 (N)」
  → 确认 Modal（列出未持有的 N 只 + 合计金额）
  → Promise.allSettled 逐笔 POST
  → 汇总「成功 N 笔 / 失败 M 笔」，失败的列出可重试

已持有判定：前端用已加载的 POSITIONS 按 code 比对
```

**为什么不用原子批量 API**：持仓写入是低频少量（≤8 笔），`Promise.allSettled` + 结果汇总足以覆盖部分失败；而 Worker 当前处于「待部署」状态，不宜再堆改动。Worker 稳定后如需原子性再升级。

---

## 三、交互与状态判定

### 3.1 页面变化

```
待建仓清单
┌────┬────────┬──────┬──────┬─────┬──────┬──────┬──────┬──────┬────────┐
│排名│ 代码   │ 名称 │ 策略 │计划价│股数 │ 金额 │ 止损 │ 止盈 │ 操作   │  ← 新增
├────┼────────┼──────┼──────┼─────┼──────┼──────┼──────┼──────┼────────┤
│ 2  │300832  │新产业│动量… │56.66│ 100 │¥5,666│47.73 │74.52 │[建仓]  │
│ 5  │600886  │国投… │回调… │15.68│ 600 │¥9,408│14.25 │18.54 │[已持有]│
└────┴────────┴──────┴──────┴─────┴──────┴──────┴──────┴──────┴────────┘

汇总条右侧：目标单票 ¥10,000 · 计划合计 ¥69,926 · [全部建仓 (6)]
                                                      ↑ 括号 = 未持有数
```

### 3.2 状态

```javascript
let POSITIONS = [];        // 实际持仓（供「已持有」判定）
let WORKER_OK = true;      // Worker 可用性（决定按钮是否可点）
let PENDING_PLAN_ROW = null;  // 未登录时记住待建仓的行
```

### 3.3 已持有判定

```javascript
const heldCodes = new Set(POSITIONS.map(p => String(p.code)));
const isHeld = r => heldCodes.has(String(r.code));
```

- 用 **code 精确匹配**
- 已持有：按钮「已持有」+ `disabled` + `title="已在持仓中，如需修改请到下方持仓区"`
- 「全部建仓 (N)」的 N 只算未持有；N=0 时禁用并显示「全部已持有」

---

## 四、单只建仓表单

把现有手写 modal（`showAddForm`）改造成**通用持仓表单**，两种入口共用：

```javascript
// openPositionForm({ isCb, prefill, readonly })
//   「＋ 添加持仓」 → openPositionForm({})
//   行内「建仓」   → openPositionForm({ prefill, readonly: ['code','name'] })
```

### 表单结构（Bootstrap Modal，与登录 Modal 统一样式）

```
┌─ 建仓：600886 国投电力 ────────────┐
│  代码    600886        （只读）     │
│  名称    国投电力       （只读）     │
│  策略    回调买入       （只读提示） │
│  ─────────────────────────────    │
│  股数    [600]        ← 可改       │
│  成本价  [15.68]      ← 可改       │
│  止损价  [14.25]      ← 可改       │
│  建仓日  [2026-10-23] ← 可改       │
│  ─────────────────────────────    │
│  金额    ¥9,408                    │
│         [取消]  [确认建仓]          │
└──────────────────────────────────┘
```

### 预填映射

| 持仓字段 | 预填来源 |
|---|---|
| `code` | `plan.code`（只读） |
| `name` | `plan.name`（只读） |
| `shares` | `plan.shares` |
| `entry_price` | `plan.plan_price` |
| `stop_price` | `plan.stop_price` |
| `entry_date` | 今天 |

### 交互细节

- 改股数/成本价时**金额实时联动**
- `<form>` 提交 → Enter 确认
- 提交中按钮禁用 + 「建仓中…」
- 成功 → 关闭 modal → `loadAll()` → 该行变「已持有」
- 失败 → **modal 内红字**（与登录 Modal 一致）
- 前端校验：股数 > 0 且为 100 整数倍、成本价 > 0；不满足直接提示不发请求
- **每次打开先 `remove()` 旧 `#pos-modal` 再插入**，避免重复 id（现有代码隐患）

---

## 五、全部建仓

### 确认 Modal

```
┌─ 全部建仓：6 只 ─────────────────────────────┐
│  代码     名称       股数   成本价    金额    │
│  300832  新产业      100    56.66   ¥5,666   │
│  ...                                          │
│  ────────────────────────────────────────    │
│  合计 6 只 · ¥52,000 · 占股票仓 65%          │
│  已跳过 2 只已持有                            │
│                    [取消]  [确认全部建仓]     │
└──────────────────────────────────────────────┘
```

> 明确提示"按计划价/计划股数提交"，如需逐只改价请用行内按钮。

### 提交与结果

```javascript
const results = await Promise.allSettled(
  targets.map(r => postDecidePosition({
    code: r.code, name: r.name, is_cb: false,
    shares: r.shares, entry_price: r.plan_price,
    stop_price: r.stop_price,
    entry_date: new Date().toISOString().slice(0, 10),
  }))
);
```

| 结果 | 呈现 |
|---|---|
| 全部成功 | 绿色条「已建仓 6 只，合计 ¥52,000」+ 刷新 |
| 部分失败 | 黄色条「成功 5 笔，失败 1 笔」+ 列出失败代码与原因，失败行可重试 |
| 全部失败 | 红色条 + 首条错误原因 |

---

## 六、Worker 不可用降级

`loadAll()` 区分两种失败：

```javascript
let authRes = null;
try { authRes = await fetchDecideAuthState(); WORKER_OK = true; }
catch (e) { WORKER_OK = false; }   // 404 / 网络错误 = Worker 未部署
```

| 状态 | 行内按钮 | 全部建仓 |
|---|---|---|
| Worker 正常 + 未登录 | 「建仓」可点 → 先弹登录 | 可点 → 先弹登录 |
| Worker 正常 + 已登录 | 「建仓」→ 表单 | 「全部建仓 (N)」 |
| **Worker 不可用** | 禁用 + `title="写入服务未部署"` | 禁用 + 同上 |

顶部提示（仅 Worker 不可用时）：

```
⚠ 写入服务未部署，无法建仓。请在本地终端运行 bash scripts/deploy-worker.sh
```

**关键决策**：**不隐藏按钮**，显示禁用态 + 明确原因，比"按钮消失"更易排查。

未登录时点建仓 → 先开登录 Modal，登录成功后自动打开建仓表单（用 `PENDING_PLAN_ROW` 记住）。

---

## 七、文件清单

| 文件 | 类型 | 改动 |
|---|---|---|
| `static/js/decide.js` | 修改 | `POSITIONS`/`WORKER_OK`/`PENDING_PLAN_ROW`；清单加「操作」列；`openPositionForm()` 改造；`openAllPositions()` 批量；结果汇总；Worker 降级 |
| `tests/test_decide_web.py` | 修改 | 7 个新测试 |

**零改动**：`worker/*`、`app.py`、`export_json.py`、`decide_service.py`、`decide_compare.js`、`templates/*`。

---

## 八、测试策略

| 测试 | 验证点 |
|---|---|
| `test_plan_table_has_action_column` | 表头含「操作」 |
| `test_held_rows_disabled` | 含 `heldCodes` 判定 + `disabled` |
| `test_all_positions_function` | `openAllPositions` 存在 |
| `test_uses_allsettled` | 批量用 `Promise.allSettled` |
| `test_worker_down_hint` | Worker 不可用提示含 `deploy-worker.sh` |
| `test_form_prefill_from_plan` | 预填映射（`plan_price` → `entry_price`） |
| `test_no_prompt_still` | 仍不含 `prompt(`（延续既有约束） |
| 回归 | 全量 pytest + 9 个 node 测试 |

---

## 九、验证计划

1. 静态导出后本地打开 `decide.html`，确认清单有「操作」列与「全部建仓」按钮
2. 无持仓时（当前状态）：所有行可建仓，全部建仓计数 = 8
3. **Worker 未部署时**：按钮禁用 + 顶部提示出现（当前就该是这样）
4. Worker 部署后：点行内建仓 → 表单预填 → 改价提交 → 该行变「已持有」
5. 点全部建仓 → 确认 → 结果汇总 → 清单刷新
6. 全量 pytest 通过

---

## 十、风险控制

| 风险 | 缓解 |
|---|---|
| 误点建仓写入脏数据 | 已持有一律禁用；表单可改价；失败可见 |
| 批量部分失败留下中间态 | `allSettled` 汇总 + 失败行可重试（非原子，已在方案对比中确认可接受） |
| Worker 未部署时用户困惑 | 禁用 + 明确文案指向部署脚本 |
| 重复 modal id 累积 | 每次打开前 `remove()` 旧的 |
| 前端校验绕过（非法股数） | Worker 侧已有 `shares > 0` / `entry_price > 0` 校验兜底 |

---

## 十一、评审 / 实施

- 决策已确认：弹窗预填可改 / 行内+全部 / 已持有禁用 / 一次确认批量
- 设计三节（交互与状态 / 单只表单 / 全部建仓+降级）逐节获用户确认
- 接着调 writing-plans 拆实施计划
