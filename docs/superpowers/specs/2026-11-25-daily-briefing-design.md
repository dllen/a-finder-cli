# 每日简报（时间流）设计

> 日期：2026-11-25
> 状态：已批准
> 方案：A — 插件投递 prompt → agent 生成叙事 → 写回 JSON

## 概述

在站点新增独立页面 `/briefing`，以时间流方式呈现每日市场简报。
内容覆盖核心三块：选股信号、ETF 资金流、可转债。
同步完成后自动生成；LLM 叙事由 DSH 插件侧调模型完成；
无 LLM 时前端降级为纯数据 + 规则短句版。

## 需求确认

| 维度 | 决定 |
|---|---|
| 受众 | 自己看，嵌在站点里 |
| 页面位置 | 独立 `/briefing`，导航加入口 |
| 时间流含义 | 混合：当日盘中节奏 + 近期事件回顾 |
| 内容范围 | 选股信号 + ETF 资金流 + 可转债 |
| 生成时机 | 同步完成后自动 |
| 叙事风格 | LLM 生成 |
| LLM 接入 | 插件侧调模型（DSH 会话） |

## §1 数据流与生成管线

```
a-finder.sh site（同步流水线）
  ├─ [1/5] 增量同步 + 选股
  ├─ [2/5] 可转债榜单
  ├─ [3/5] ETF 看板抓取
  ├─ [4/5] export_json.py → site/data/*.json
  └─ [5/5] generate_briefing_input.py → data/briefing-input.json
                ↓
DSH 插件（performSync 完成后）
  ├─ 检测 briefing-input.json 存在且日期 == 最新交易日
  ├─ 向会话投递 prompt（附带数据摘要 JSON）
  └─ Agent 生成叙事 → 写入 site/data/briefing-YYYY-MM-DD.json
                ↓
前端 /briefing 页面
  ├─ 读 briefing-YYYY-MM-DD.json（LLM 叙事版）
  └─ 若不存在 → 读 briefing-input.json 渲染纯数据兜底版
```

### 关键决策

- `briefing-input.json` 由 Python 侧产出（确定性、可测试），是 LLM 的输入原料
- LLM 叙事由 agent 在 DSH 会话里完成，输出写回 `site/data/`
- 兜底版不需要 LLM，前端直接用 input JSON 的结构化数据 + 规则短句
- 非交易日不生成简报（input 里带 `is_trading_day` 标记）

### briefing-input.json schema

```json
{
  "date": "2026-11-24",
  "is_trading_day": true,
  "intraday": {
    "peak_minute": {"time": "14:57", "amount_yi": 24.84},
    "hourly_flow": [{"hour": "09:30-10:30", "netflow_yi": 120.5}],
    "total_amount_yi": 856.21,
    "total_netflow_yi": -118.7
  },
  "sectors": {
    "top_inflow": [{"name": "中证1000", "netflow_yi": 46.96}],
    "top_outflow": [{"name": "沪深300", "netflow_yi": -36.13}]
  },
  "picks": {
    "buy_signals": [{"code": "002179", "name": "中航光电", "strategy": "均线突破", "day_pct": 1.83}],
    "high_winrate": [],
    "count_by_group": {"买入信号": 20, "信号策略": 20}
  },
  "cb": {
    "alerts_count": 3,
    "top_picks": [{"code": "113059", "name": "隆22转债", "score": 85}]
  },
  "recent_events": [
    {"date": "2026-11-20", "type": "放量日", "detail": "净流入 903.87 亿"}
  ]
}
```

## §2 前端时间流页面

### 页面结构

- 标题区：日期 + 星期 + 版本标记（LLM 叙事版 / 数据兜底版）
- 当日盘中节奏：左侧竖线 + 圆点节点，右侧文字（5-7 个时间节点）
- 近期事件回顾：最近 5 个交易日里 3-4 件关键事件，倒序排列
- 今日选股亮点：3 张卡片（代码/名称/策略/当日涨跌）
- 转债预警：badge + 数量（无预警时省略）

### 视觉风格

- 沿用 Hallmark Console 暗色主题
- 时间轴：左侧竖线 + 圆点，类似 git log / changelog
- LLM 叙事用 ink-2 色；兜底规则短句用 muted italic
- 移动端时间轴缩窄，卡片单列

### 交互

- 日期选择器复用 `date-filter` 组件（compact=true）
- 默认最新交易日
- 无自动刷新（日频产物）

### 技术实现

- `templates/briefing.html` 继承 `base.html`
- `static/js/briefing.js`：读 JSON → 渲染时间轴 DOM（vanilla ES6）
- `static/css/briefing.css`：时间轴样式
- 不需要 ECharts

## §3 插件侧 LLM 叙事生成

### 触发机制

`performSync` 完成后检查 `data/briefing-input.json`：
- 存在且 `is_trading_day == true` 且 `date` == DB 最新交易日 → 投递 prompt
- 不存在或非交易日 → 跳过

复用现有 Schedule 服务的 `new_once` target（与每日提醒同构）。

### Prompt 要求

1. 当日盘中节奏：5-7 个时间节点，每个一句话解读（方向、量级、与常态对比）
2. 近期事件回顾：3-4 件事，每件一句话
3. 选股亮点：3 只最有代表性的票，说明为什么值得注意
4. 语气：客观、简洁、有洞察。不用感叹号，不用空话
5. 输出严格 JSON（schema 与前端消费格式一致）

### Agent 执行流程

1. 收到 prompt → 调模型生成 JSON
2. 校验输出 schema（date 匹配、必填字段齐全）
3. 写入 `site/data/briefing-YYYY-MM-DD.json`
4. 站点推送等下次同步时一并带上（不单独 push）

### 容错

- 生成失败/超时/schema 不合法 → 不写文件，前端降级数据版
- 非交易日 → 不投递
- 同一天重复同步 → 覆盖（幂等）
- GUI 离线 → 数据版永远可用

### 新增文件

- `plugins/dsh-a-finder-sync/lib/briefing.js`：prompt 构造 + 投递 + 校验
- `scripts/generate_briefing_input.py`：从 islands/picks/cb 提取摘要

### 不改的

- 现有 schedule.js 每日提醒不动
- a-finder.sh site 只加一步 Python 调用

## §4 测试与边界

### 测试矩阵

| 层 | 测什么 | 怎么测 |
|---|---|---|
| generate_briefing_input.py | 摘要结构正确、字段齐全 | pytest fixture |
| briefing.js prompt | 包含必要指令；非交易日不投递 | node 契约测试 |
| briefing.js 校验 | 合法通过；缺字段拒绝 | node 单元测试 |
| Flask /briefing | 200 + 渲染；API 返回 JSON | pytest test_client |
| 静态导出 | briefing JSON 拷贝；无数据不生成页面 | pytest |
| 前端 briefing.js | 语法合法；DOM 结构 | node --check + headless |
| 兜底降级 | 无 LLM 文件时仍渲染 | headless Chrome |

### 边界情况

- 选股缺失 → picks 空数组，标注「选股数据缺失」
- ETF 快照落后 → 用最近可用日期，标注实际数据日
- 转债表空 → cb 段省略
- 多次同步 → 覆盖写（幂等）
- 历史回看 → date-filter 列出所有有 briefing 的日期

### YAGNI（不做）

- PDF/图片导出
- 推送通知（ntfy/邮件）
- 多语言
- 用户自定义模板
- 实时盘中更新

## 文件清单

### 新增

- `scripts/generate_briefing_input.py`
- `plugins/dsh-a-finder-sync/lib/briefing.js`
- `templates/briefing.html`
- `static/js/briefing.js`
- `static/css/briefing.css`
- `tests/test_briefing.py`

### 修改

- `a-finder.sh`：site 流水线加第 5 步
- `app.py`：/briefing 路由 + /api/briefing/* 接口
- `export_json.py`：拷贝 briefing JSON + 渲染 briefing.html
- `templates/components/nav.html`：加「每日简报」入口
- `static/js/api.js`：fetchBriefing 系列
- `plugins/dsh-a-finder-sync/lib/index.js`：performSync 后触发 briefing
- `plugins/dsh-a-finder-sync/package.json`：version bump
- `CLAUDE.md` / `README.md`：文档
