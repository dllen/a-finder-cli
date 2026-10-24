# UI 架构重构 — Jinja2 模板 + Vanilla JS + CDN 本地化

**日期**：2026-10-24
**状态**：设计中（待批准后实施）

---

## 一、目标

将 `app.py`（1499 行单文件巨石）重构为职责分离的现代 Web 架构：

- **HTML**：从 Python 字符串抽离到 Jinja2 模板（`templates/` 目录）
- **CSS**：从 `_APP_CSS` 内联抽离到 `static/css/app.css`
- **JS**：去掉 jQuery，用原生 ES6+ module 重写，按功能拆分
- **CDN**：Bootstrap / Chart.js 下载到 `static/vendor/`，去掉 bootcdn.net 外网依赖

**非目标（本期不做）**：
- ❌ 新增功能页面（可转债 Web 页面等留到后续）
- ❌ 暗色模式
- ❌ 信息密度/交互体验优化
- ❌ 移动端适配改进
- ❌ 构建工具（Webpack/Vite 等）

**核心约束**：重构不改功能。所有现有页面行为、API 接口、静态部署模式必须保持一致。

---

## 二、现状分析

### app.py 构成（1499 行）

| 内容 | 行数 | 问题 |
|---|---|---|
| `_APP_CSS` 内联样式 | ~125 | CSS 混在 Python 里，无法独立编辑/lint |
| `_SHELL` / `_nav` / `_footer` | ~80 | HTML 骨架用字符串拼接，无复用 |
| `PAGE_BODY` + `PAGE_SCRIPT` | ~230 | 每日机会页 HTML+JS 内联 |
| `PLAN_BODY` + `PLAN_SCRIPT` | ~350 | 交易计划页 HTML+JS 内联 |
| `PORTFOLIO_BODY` + `PORTFOLIO_SCRIPT` | ~250 | 持仓组合页 HTML+JS 内联 |
| `_page()` / helpers | ~60 | 手动字符串替换，Jinja2 可原生替代 |
| Python 路由 + API | ~400 | **唯一应留在 app.py 的内容** |

### 外部依赖

| 依赖 | 来源 | 大小 | 问题 |
|---|---|---|---|
| jQuery 3.7.1 | bootcdn.net CDN | ~90KB | 仅用于 DOM 操作和 AJAX，Vanilla JS 完全可替代 |
| Bootstrap 5.3.3 CSS | bootcdn.net CDN | ~230KB | 外网依赖，离线不可用 |
| Bootstrap 5.3.3 JS | bootcdn.net CDN | ~80KB | 同上 |
| Chart.js 4.4.1 | bootcdn.net CDN | ~200KB | 同上，仅 portfolio 页使用 |

### 静态部署模式

当前 `data-source.js` 支持双模式：Flask API (`APP_MODE='api'`) 和静态 JSON (`APP_MODE='static'`)。`export_json.py` 生成 JSON 数据文件供 gh-pages 使用。**此能力必须保留**。

---

## 三、目标文件结构

```
a-finder-cli/
├── app.py                          # ~400行，纯路由+API
├── templates/                      # Jinja2 模板（新建）
│   ├── base.html                   # shell: doctype/head/nav/footer/scripts
│   ├── picks.html                  # 每日机会页面 body
│   ├── plan.html                   # 交易计划页面 body
│   ├── portfolio.html              # 持仓组合页面 body
│   └── components/                 # 可复用片段
│       ├── nav.html                # 导航栏（接收 active_page 变量）
│       ├── dashboard_cards.html    # 顶部4卡片容器
│       ├── filter_toolbar.html     # 筛选工具栏（picks/plan 共用）
│       └── status_bar.html         # 状态/进度/日志区（picks/plan 共用）
├── static/
│   ├── css/
│   │   └── app.css                 # 从 _APP_CSS 抽出，CSS 变量保留
│   ├── js/
│   │   ├── api.js                  # 数据源抽象（替代 data-source.js）
│   │   ├── dom.js                  # DOM 辅助（替代 common.js）
│   │   ├── table.js                # 通用表格渲染/排序/筛选
│   │   ├── picks.js                # 每日机会页入口
│   │   ├── plan.js                 # 交易计划页入口
│   │   ├── portfolio.js            # 持仓组合页入口
│   │   └── dashboard.js            # 顶部卡片刷新逻辑
│   └── vendor/                     # 本地化第三方库（新建）
│       ├── bootstrap.min.css       # Bootstrap 5.3.3
│       ├── bootstrap.bundle.min.js # Bootstrap JS（含 Popper）
│       └── chart.umd.min.js        # Chart.js 4.4.1
```

---

## 四、app.py 瘦身

### 删除的内容

- `_APP_CSS` 字符串 → `static/css/app.css`
- `_SHELL` / `_FAVICON_SVG` / `_FAVICON_HREF` → `templates/base.html`
- `_nav()` / `_footer()` / `_page()` / `_esc_html()` → Jinja2 原生能力
- `PAGE_BODY` / `PAGE_SCRIPT` → `templates/picks.html` + `static/js/picks.js`
- `PLAN_BODY` / `PLAN_SCRIPT` → `templates/plan.html` + `static/js/plan.js`
- `PORTFOLIO_BODY` / `PORTFOLIO_SCRIPT` → `templates/portfolio.html` + `static/js/portfolio.js`
- `_TIER_FILTER_OPTIONS` 字符串拼接 → Jinja2 循环

### 保留的内容

- `create_app()` 工厂函数
- 所有 `/api/*` 路由 handler（逻辑零改动）
- 3 个页面路由（改为 `render_template()`）
- `_start_job()` / 后台任务逻辑
- Flask 配置

### 页面路由改造

```python
# 旧
@app.get("/")
def index():
    today = _date.today().isoformat()
    return _page("每日机会", "picks",
                 PAGE_BODY.replace("{{today}}", today),
                 PAGE_SCRIPT)

# 新
@app.get("/")
def index():
    return render_template("picks.html",
        today=date.today().isoformat(),
        app_mode="api",
        data_prefix="",
        year=date.today().year,
    )
```

---

## 五、Jinja2 模板继承

### base.html

```html
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}每日机会{% endblock %}</title>
  <link rel="icon" href="{{ url_for('static', filename='favicon.svg') }}">
  <link rel="stylesheet" href="{{ url_for('static', filename='vendor/bootstrap.min.css') }}">
  <link rel="stylesheet" href="{{ url_for('static', filename='css/app.css') }}">
</head>
<body>
  <nav class="navbar app-nav">
    {% include 'components/nav.html' %}
  </nav>
  {% block body %}{% endblock %}
  <footer class="app-footer py-3 mt-auto">
    <div class="container text-center small text-muted">© {{ year }} 每日机会</div>
  </footer>
  <script>window.APP_MODE={{ app_mode|tojson }};window.DATA_PREFIX={{ data_prefix|tojson }};</script>
  {% block scripts %}{% endblock %}
</body>
</html>
```

### 子页面（以 picks.html 为例）

```html
{% extends "base.html" %}
{% set active_page = "picks" %}
{% block title %}每日机会{% endblock %}
{% block body %}
<main class="container py-4">
  <div class="page-header">...</div>
  {% include 'components/dashboard_cards.html' %}
  {% include 'components/filter_toolbar.html' %}
  {% include 'components/status_bar.html' %}
  <div id="board"></div>
  <h2 class="section-title mt-4">策略胜率统计</h2>
  <div id="stats"></div>
</main>
{% endblock %}
{% block scripts %}
<script type="module" src="{{ url_for('static', filename='js/picks.js') }}"></script>
{% endblock %}
```

### 组件复用

- `components/nav.html` — 导航栏，用 `active_page` 变量控制高亮
- `components/filter_toolbar.html` — 日期选择器 + 搜索框 + 操作按钮（picks/plan 共用）
- `components/status_bar.html` — 状态/进度条/日志区（picks/plan 共用）
- `components/dashboard_cards.html` — 顶部4卡片容器 div（picks/plan 共用）

---

## 六、JS 去 jQuery + ES6 模块化

### jQuery → Vanilla 翻译对照

| jQuery | Vanilla ES6+ |
|---|---|
| `$.getJSON(url, params)` | `fetch(url + '?' + new URLSearchParams(params)).then(r => r.json())` |
| `$('#id')` | `document.getElementById('id')` |
| `$(sel).html(content)` | `el.innerHTML = content` |
| `$(sel).empty()` | `el.innerHTML = ''` |
| `$(sel).append(html)` | `el.insertAdjacentHTML('beforeend', html)` |
| `$(sel).val()` | `el.value` |
| `$(sel).on('click', fn)` | `el.addEventListener('click', fn)` |
| `$(sel).hide()` / `.show()` | `el.style.display = 'none'` / `''` |
| `$(fn)` (ready) | `<script type="module">` 天然 defer |
| `$.ajax({type:'POST',...})` | `fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)})` |

### Module 结构

```js
// static/js/api.js — 数据源抽象
export function isStatic() { return window.APP_MODE === 'static'; }
export async function fetchPicks(date) { ... }
export async function fetchDates() { ... }
export async function fetchDashboard() { ... }
export async function fetchPlanDates() { ... }
export async function fetchPlan(date, includeFailed) { ... }
export async function fetchHoldings() { ... }
export async function fetchPortfolioSummary(date, strategy) { ... }
export async function fetchPortfolioDetail(label, date, strategy) { ... }
export function postRefresh(sync) { ... }
export function postPlanBuild(params) { ... }
export async function pollJob(jobId, onProgress, onComplete) { ... }

// static/js/dom.js — DOM 辅助
export function $(id) { return document.getElementById(id); }
export function fmt(v) { return v == null ? '—' : Number(v).toFixed(2); }
export function esc(s) { ... }
export function setStatus(html, cls) { ... }
export function setProgress(pct) { ... }
export function setLog(lines) { ... }
export function showBoardLoading() { ... }
export function whenBoardReady(fn, minMs) { ... }
export function fillDateSelect(selectEl, dates) { ... }

// static/js/table.js — 通用表格
export function renderTable(container, cols, rows, opts) { ... }
export function sortRows(rows, key, dir) { ... }
```

### 页面入口

```html
<!-- picks.html -->
<script type="module" src="{{ url_for('static', filename='js/picks.js') }}"></script>

<!-- plan.html -->
<script type="module" src="{{ url_for('static', filename='js/plan.js') }}"></script>

<!-- portfolio.html -->
<script type="module" src="{{ url_for('static', filename='js/portfolio.js') }}"></script>
```

---

## 七、CDN 本地化

### 下载命令

```bash
mkdir -p static/vendor
curl -o static/vendor/bootstrap.min.css \
  https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css
curl -o static/vendor/bootstrap.bundle.min.js \
  https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js
curl -o static/vendor/chart.umd.min.js \
  https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js
```

### 模板引用

```html
<link rel="stylesheet" href="{{ url_for('static', filename='vendor/bootstrap.min.css') }}">
<script src="{{ url_for('static', filename='vendor/bootstrap.bundle.min.js') }}"></script>
<script src="{{ url_for('static', filename='vendor/chart.umd.min.js') }}"></script>
<!-- jQuery 完全移除 -->
```

### 版本锁定

Bootstrap 5.3.3 / Chart.js 4.4.1 — 与当前 CDN 版本一致，确保视觉零差异。vendor 文件纳入版本控制。

---

## 八、静态部署兼容

### export_json.py 扩展

新增 HTML 页面生成：

```python
def export_html_pages(out_dir, db_path):
    """用 Jinja2 渲染静态 HTML，app_mode='static'"""
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("templates"))
    pages = [
        ("index.html", "picks.html"),
        ("plan.html", "plan.html"),
        ("portfolio.html", "portfolio.html"),
    ]
    for filename, template in pages:
        html = env.get_template(template).render(
            app_mode="static",
            data_prefix="./",
            today=date.today().isoformat(),
            year=date.today().year,
            tier_filter_options="",  # 静态模式下无需动态生成
        )
        (Path(out_dir) / filename).write_text(html, encoding="utf-8")
```

### 静态资源复制

`export_json.py` 还需将 `static/` 目录复制到输出目录，确保 vendor CSS/JS 可用。

### api.js 双模式

`api.js` 保留 `isStatic()` 判断：
- API 模式：`fetch('/api/picks?date=...')`
- 静态模式：`fetch('./data/picks-2026-10-24.json')`

行为与现有 `data-source.js` 完全一致。

---

## 九、测试策略

### 1. API 回归测试（已有，不动）

现有 `tests/test_web_plan.py` / `test_api_portfolio.py` 等覆盖所有 `/api/*` 端点。API handler 逻辑零改动，这些测试天然保护后端行为。

### 2. 模板渲染测试（新增）

```python
# tests/test_ui_templates.py
import pytest

@pytest.fixture
def client(app):
    return app.test_client()

def test_picks_template_renders(client):
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert 'id="board"' in html
    assert 'id="dashboard"' in html
    assert 'app.css' in html
    assert 'vendor/bootstrap.min.css' in html
    assert 'jquery' not in html.lower()
    assert 'type="module"' in html

def test_plan_template_renders(client):
    resp = client.get("/plan")
    assert resp.status_code == 200
    assert 'capital-btn' in resp.data.decode()

def test_portfolio_template_renders(client):
    resp = client.get("/portfolio")
    assert resp.status_code == 200
    assert 'pf-returns-chart' in resp.data.decode()

def test_no_jquery_anywhere(client):
    """确认所有页面都不再引用 jQuery"""
    for path in ["/", "/plan", "/portfolio"]:
        html = client.get(path).data.decode()
        assert 'jquery' not in html.lower(), f"{path} still references jQuery"

def test_static_mode_config_injected(client):
    resp = client.get("/")
    html = resp.data.decode()
    assert 'window.APP_MODE=' in html
    assert 'window.DATA_PREFIX=' in html
```

### 3. 验收检查清单

1. ✅ 全量 pytest 通过（baseline 362 + 新增模板测试）
2. ✅ `curl localhost:8000/` 返回完整 HTML，无 jQuery 引用
3. ✅ 三个页面视觉与重构前一致（人工对比）
4. ✅ `bash a-finder.sh site --push` 生成的静态站可正常访问
5. ✅ `grep -ri 'jquery\|\\$\\(' static/js/ app.py templates/` 返回空
6. ✅ `grep -r 'bootcdn\|cdn\.' templates/ app.py` 返回空（无 CDN 引用）
7. ✅ vendor 文件存在且大小合理

---

## 十、风险控制

| 风险 | 缓解措施 |
|---|---|
| jQuery → Vanilla 翻译遗漏 | 全局 grep `$(` / `$.` 确认零残留 |
| Jinja2 转义差异 | `_esc_html()` → Jinja2 自动转义；验证特殊字符正确显示 |
| 静态模式断裂 | `export_json.py` 生成 HTML 后本地 `python3 -m http.server` 验证 |
| Bootstrap 版本差异 | 锁定 5.3.3，与当前 CDN 版本一致 |
| Chart.js 初始化时序 | `<script type="module">` 天然 defer，比 jQuery ready 更可靠 |
| ES6 module 兼容性 | 所有现代浏览器支持；项目目标用户为开发者，无 IE 需求 |
| 大提交回滚困难 | 单分支开发，合并前 squash 或保留原子提交均可 |

---

## 十一、实施顺序

1. 下载 vendor 文件到 `static/vendor/`
2. 创建 `templates/` 目录结构 + base.html + 组件
3. 抽离 CSS 到 `static/css/app.css`
4. 重写 JS 模块（api.js → dom.js → table.js → 各页面入口）
5. 改造 app.py 路由（`_page()` → `render_template()`）
6. 更新 `export_json.py` 支持 HTML 生成
7. 新增模板渲染测试
8. 端到端验收（API + 页面 + 静态部署）
9. 清理：删除 `_APP_CSS` / `_SHELL` / `PAGE_*` / `_page()` 等废弃代码
10. 更新 README / CLAUDE.md

---

## 十二、评审 / 实施

- 本文档已获用户批准（6 节设计逐节确认）
- 接着调 `writing-plans` skill 拆实施计划
- 实施按 TDD：先写模板渲染测试 → 搬迁 → 验证
- 不引入新 Python 依赖（Jinja2 是 Flask 内置依赖）
- 不引入 Node.js 构建工具
