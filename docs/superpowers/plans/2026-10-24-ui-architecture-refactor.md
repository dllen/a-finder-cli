# UI Architecture Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor app.py from a 1499-line monolith into a clean Jinja2 + Vanilla JS + local vendor architecture, preserving all existing functionality including static deployment mode.

**Architecture:** Extract inline HTML to Jinja2 templates with inheritance, extract CSS to standalone file, rewrite JS from jQuery to ES6 modules, download Bootstrap/Chart.js locally. API handlers unchanged. Static export updated to render HTML via Jinja2.

**Tech Stack:** Python/Flask/Jinja2, Vanilla ES6+ JavaScript, Bootstrap 5.3.3, Chart.js 4.4.1

**Spec:** `docs/superpowers/specs/2026-10-24-ui-architecture-refactor-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `templates/base.html` | Shell: doctype, head, nav, footer, script slots |
| Create | `templates/components/nav.html` | Navigation bar with active_page highlighting |
| Create | `templates/components/dashboard_cards.html` | Dashboard cards container div |
| Create | `templates/components/filter_toolbar.html` | Date selector + search + action buttons |
| Create | `templates/components/status_bar.html` | Status/progress/log area |
| Create | `templates/picks.html` | Picks page body (extends base) |
| Create | `templates/plan.html` | Plan page body (extends base) |
| Create | `templates/portfolio.html` | Portfolio page body (extends base) |
| Create | `static/css/app.css` | All styles (from `_APP_CSS`) |
| Create | `static/js/api.js` | Data source abstraction (fetch-based, dual-mode) |
| Create | `static/js/dom.js` | DOM helpers (replaces common.js) |
| Create | `static/js/table.js` | Generic table render/sort/filter |
| Create | `static/js/picks.js` | Picks page entry point |
| Create | `static/js/plan.js` | Plan page entry point |
| Create | `static/js/portfolio.js` | Portfolio page entry point |
| Create | `static/js/dashboard.js` | Dashboard card refresh logic |
| Create | `static/vendor/bootstrap.min.css` | Bootstrap 5.3.3 CSS (local) |
| Create | `static/vendor/bootstrap.bundle.min.js` | Bootstrap 5.3.3 JS bundle (local) |
| Create | `static/vendor/chart.umd.min.js` | Chart.js 4.4.1 (local) |
| Create | `tests/test_ui_templates.py` | Template rendering tests |
| Modify | `app.py` | Remove inline HTML/CSS/JS, use render_template |
| Modify | `export_json.py` | Generate HTML via Jinja2, copy vendor files |
| Delete | `static/common.js` | Replaced by dom.js |
| Delete | `static/data-source.js` | Replaced by api.js |
| Delete | `static/dashboard.js` | Replaced by static/js/dashboard.js |

---

### Task 1: Download vendor files + create directory structure

**Files:**
- Create: `static/vendor/bootstrap.min.css`
- Create: `static/vendor/bootstrap.bundle.min.js`
- Create: `static/vendor/chart.umd.min.js`
- Create: `static/css/.gitkeep`
- Create: `templates/.gitkeep`
- Create: `templates/components/.gitkeep`

- [ ] **Step 1: Create directories**

```bash
mkdir -p static/vendor static/css templates/components
```

- [ ] **Step 2: Download Bootstrap 5.3.3 CSS**

```bash
curl -fSL -o static/vendor/bootstrap.min.css \
  https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css
```

Verify: `wc -c static/vendor/bootstrap.min.css` → ~230KB

- [ ] **Step 3: Download Bootstrap 5.3.3 JS bundle**

```bash
curl -fSL -o static/vendor/bootstrap.bundle.min.js \
  https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js
```

Verify: `wc -c static/vendor/bootstrap.bundle.min.js` → ~80KB

- [ ] **Step 4: Download Chart.js 4.4.1**

```bash
curl -fSL -o static/vendor/chart.umd.min.js \
  https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js
```

Verify: `wc -c static/vendor/chart.umd.min.js` → ~200KB

- [ ] **Step 5: Verify no jQuery downloaded**

```bash
grep -ri "jquery" static/vendor/ && echo "FAIL: jQuery found" || echo "OK: No jQuery"
```

- [ ] **Step 6: Commit**

```bash
git add static/vendor/ static/css/.gitkeep templates/.gitkeep templates/components/.gitkeep
git commit -m "chore(ui): add vendor libs (Bootstrap 5.3.3, Chart.js 4.4.1) + directory structure"
```

---

### Task 2: Extract CSS to static/css/app.css

**Files:**
- Create: `static/css/app.css`

- [ ] **Step 1: Copy _APP_CSS content to app.css**

Read `app.py` lines 231-355 (the `_APP_CSS = """..."""` block). Extract the CSS content between the triple quotes and write it to `static/css/app.css`.

The content starts with `:root{` and ends with `.dashboard-card .card-body{min-height:4.5rem}` followed by `"""`.

Write exactly that CSS content (without the Python variable assignment or triple quotes) to `static/css/app.css`.

- [ ] **Step 2: Verify CSS is valid**

```bash
head -5 static/css/app.css   # Should start with :root{
tail -3 static/css/app.css   # Should end with min-height:4.5rem}
wc -l static/css/app.css     # Should be ~125 lines
```

- [ ] **Step 3: Commit**

```bash
git add static/css/app.css
git commit -m "refactor(ui): extract CSS to static/css/app.css"
```

---

### Task 3: Create Jinja2 base template + components

**Files:**
- Create: `templates/base.html`
- Create: `templates/components/nav.html`
- Create: `templates/components/dashboard_cards.html`
- Create: `templates/components/filter_toolbar.html`
- Create: `templates/components/status_bar.html`

- [ ] **Step 1: Create base.html**

This replaces `_SHELL` (app.py:390-416), `_nav()` (app.py:370-388), `_footer()` (app.py:358-368), and `_FAVICON_*` (app.py:220-229).

Create `templates/base.html`:

```html
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}每日机会{% endblock %}</title>
  <link rel="icon" href="{{ url_for('static', filename='favicon.svg') }}" type="image/svg+xml">
  <link rel="stylesheet" href="{{ url_for('static', filename='vendor/bootstrap.min.css') }}">
  <link rel="stylesheet" href="{{ url_for('static', filename='css/app.css') }}">
</head>
<body>
  {% include 'components/nav.html' %}
  {% block body %}{% endblock %}
  <footer class="app-footer py-3 mt-auto">
    <div class="container text-center small text-muted">&copy; {{ year }} 每日机会</div>
  </footer>
  <script>window.APP_MODE={{ app_mode|tojson }};window.DATA_PREFIX={{ data_prefix|tojson }};</script>
  {% block scripts %}{% endblock %}
</body>
</html>
```

Note: The favicon currently uses an inline SVG data URI (`_FAVICON_HREF`). For simplicity in this refactor, change it to reference a static file. Create `static/favicon.svg` by extracting the SVG content from `_FAVICON_SVG` in app.py (lines 220-228) and writing it as a standalone SVG file. Then update the `<link rel="icon">` to point to it.

- [ ] **Step 2: Create static/favicon.svg**

Extract the SVG string from `_FAVICON_SVG` in app.py (the multi-line string starting with `<svg` and ending with `</svg>`). Write it to `static/favicon.svg`.

- [ ] **Step 3: Create components/nav.html**

Translate `_nav()` function (app.py:370-388) to a Jinja2 template. Uses `active_page` variable:

```html
<nav class="navbar navbar-expand-md app-nav">
  <div class="container-fluid">
    <a class="navbar-brand" href="/">
      <img src="{{ url_for('static', filename='favicon.svg') }}" alt="" class="brand-logo">
      <span>每日机会</span>
    </a>
    <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#mainNav" aria-controls="mainNav" aria-expanded="false" aria-label="Toggle navigation">
      <span class="navbar-toggler-icon"></span>
    </button>
    <div class="collapse navbar-collapse" id="mainNav">
      <ul class="navbar-nav ms-auto">
        <li class="nav-item">
          <a class="nav-link {% if active_page == 'picks' %}active{% endif %}" data-nav="picks" href="/">每日机会</a>
        </li>
        <li class="nav-item">
          <a class="nav-link {% if active_page == 'portfolio' %}active{% endif %}" data-nav="portfolio" href="/portfolio">持仓组合</a>
        </li>
        <li class="nav-item">
          <a class="nav-link {% if active_page == 'plan' %}active{% endif %}" data-nav="plan" href="/plan">交易计划</a>
        </li>
      </ul>
    </div>
  </div>
</nav>
```

- [ ] **Step 4: Create components/dashboard_cards.html**

```html
<div id="dashboard"></div>
```

(Simple container — the JS populates it. This component exists for consistency and future expansion.)

- [ ] **Step 5: Create components/filter_toolbar.html**

Translate the filter toolbar from PAGE_BODY (app.py:445-453) and PLAN_BODY (app.py:699-740). This is the picks-page version; plan page has additional controls that stay in plan.html:

```html
<div class="filter-toolbar">
  <div class="filter-group">
    <label class="form-label" for="d">日期</label>
    <select id="d" class="form-select"></select>
  </div>
  <input id="q" type="search" class="form-control" placeholder="筛选：代码 / 名称 / 策略" aria-label="快速筛选">
  {% if show_write_controls|default(true) %}
  <button id="btn-recalc" class="btn btn-outline-primary write-control">重算榜单</button>
  <button id="btn-sync" class="btn btn-primary write-control">同步行情并重算</button>
  {% endif %}
</div>
```

- [ ] **Step 6: Create components/status_bar.html**

```html
<div id="status" class="mb-3"></div>
<div id="prog" class="mb-3"></div>
<div id="log" class="mb-3"></div>
```

- [ ] **Step 7: Verify all templates parse**

```bash
python3 -c "
from jinja2 import Environment, FileSystemLoader
env = Environment(loader=FileSystemLoader('templates'))
for t in ['base.html', 'components/nav.html', 'components/dashboard_cards.html',
          'components/filter_toolbar.html', 'components/status_bar.html']:
    env.get_template(t)
    print(f'  {t} OK')
print('All templates parse OK')
"
```

- [ ] **Step 8: Commit**

```bash
git add templates/ static/favicon.svg
git commit -m "feat(ui): create Jinja2 base template + reusable components"
```

---

### Task 4: Create page templates (picks / plan / portfolio)

**Files:**
- Create: `templates/picks.html`
- Create: `templates/plan.html`
- Create: `templates/portfolio.html`

- [ ] **Step 1: Create picks.html**

Translate PAGE_BODY (app.py:437-461) to Jinja2:

```html
{% extends "base.html" %}
{% set active_page = "picks" %}
{% block title %}每日机会{% endblock %}

{% block body %}
<main class="container py-4">
  <div class="page-header">
    <h1 class="h3 mb-0 page-title">每日机会</h1>
    <span class="text-muted small" id="page-date">日期：{{ today }}</span>
  </div>

  {% include 'components/dashboard_cards.html' %}
  {% include 'components/filter_toolbar.html' %}
  {% include 'components/status_bar.html' %}
  <div id="board"></div>

  <h2 class="section-title mt-4">策略胜率统计 <small class="text-muted">基于历史标注样本</small></h2>
  <div id="stats"></div>
</main>
{% endblock %}

{% block scripts %}
<script type="module" src="{{ url_for('static', filename='js/dashboard.js') }}"></script>
<script type="module" src="{{ url_for('static', filename='js/picks.js') }}"></script>
{% endblock %}
```

- [ ] **Step 2: Create plan.html**

Translate PLAN_BODY (app.py:692-748). Note: plan page has its own extended filter toolbar (capital buttons, tier filter dropdown, checkboxes) that is NOT shared with picks:

```html
{% extends "base.html" %}
{% set active_page = "plan" %}
{% block title %}每日 Plan{% endblock %}

{% block body %}
<main class="container py-4">
  <div class="page-header">
    <h1 class="h3 mb-0 page-title">交易计划 <span class="badge bg-secondary align-middle">paper</span></h1>
  </div>

  {% include 'components/dashboard_cards.html' %}

  <div class="filter-toolbar">
    <div class="filter-group">
      <label class="form-label" for="d">日期</label>
      <select id="d" class="form-select"></select>
    </div>
    <div class="filter-group">
      <label class="form-label" for="capital-group">资金</label>
      <div id="capital-group" class="btn-group btn-group-sm flex-wrap" role="group" aria-label="资金档位">
        {% for tier in capital_tiers %}
        <button type="button" class="btn btn-outline-primary capital-btn" data-capital="{{ tier }}">{{ (tier/10000)|int }}W</button>
        {% endfor %}
      </div>
    </div>
    <div class="filter-group">
      <label class="form-label d-inline-block align-baseline mb-0">档位筛选（交集）</label>
      <button type="button" id="tier-filter-help" class="btn btn-link btn-sm p-0 ms-1 align-baseline"
              data-bs-toggle="popover" data-bs-trigger="hover focus" data-bs-html="true"
              data-bs-placement="right"
              data-bs-title="档位筛选（交集）"
              data-bs-content="勾选多档时，只保留在 <b>所有</b> 选中档位下都能建仓的票。空选 = 不限。"
              aria-label="档位筛选说明">?</button>
      <div class="dropdown d-inline-block ms-1" id="tier-filter-dd">
        <button id="tier-filter-btn" class="btn btn-outline-secondary btn-sm dropdown-toggle" type="button" data-bs-toggle="dropdown" aria-expanded="false">
          <span id="tier-filter-label">不限</span>
        </button>
        <ul class="dropdown-menu p-2" aria-labelledby="tier-filter-btn">{{ tier_filter_options|safe }}</ul>
      </div>
    </div>
    <input id="q" type="search" class="form-control" placeholder="筛选：代码 / 名称" aria-label="快速筛选">
    <button id="btn-build" class="btn btn-primary write-control">生成 plan</button>
    <div class="form-check write-control"><input id="include-failed" type="checkbox" class="form-check-input">
      <label for="include-failed" class="form-check-label">含 failed</label></div>
    <div class="form-check"><input id="only-affordable" type="checkbox" class="form-check-input">
      <label for="only-affordable" class="form-check-label">只显示可建仓</label></div>
  </div>

  {% include 'components/status_bar.html' %}
  <div id="board"></div>

  <h2 class="section-title mt-4">持仓跟踪</h2>
  <div id="holdings"></div>
</main>
{% endblock %}

{% block scripts %}
<script type="module" src="{{ url_for('static', filename='js/dashboard.js') }}"></script>
<script type="module" src="{{ url_for('static', filename='js/plan.js') }}"></script>
{% endblock %}
```

Template variables needed: `capital_tiers` (list of ints), `tier_filter_options` (pre-rendered HTML string of `<li>` items with checkboxes).

- [ ] **Step 3: Create portfolio.html**

Translate PORTFOLIO_BODY (app.py:20-49):

```html
{% extends "base.html" %}
{% set active_page = "portfolio" %}
{% block title %}持仓组合{% endblock %}

{% block body %}
<main class="container py-4">
  <h2 class="mb-3">持仓组合 <small class="text-muted fs-6">[paper]</small></h2>

  <div class="row g-2 align-items-end mb-3">
    <div class="col-auto">
      <label class="form-label mb-0">日期</label>
      <input type="date" id="pf-date" class="form-control form-control-sm" value="{{ today }}">
    </div>
    <div class="col-auto">
      <label class="form-label mb-0">策略</label>
      <select id="pf-strategy" class="form-select form-select-sm">
        <option value="" selected>全部</option>
        {{ strategy_options|safe }}
      </select>
    </div>
    <div class="col-auto ms-auto small text-muted">
      <span class="me-2">基准：所有 tier 共用同一组 daily_picks；按 capital 等权分配</span>
    </div>
  </div>

  <h5 class="mt-4">资金档位</h5>
  <div class="d-flex flex-row overflow-auto pb-2 gap-2" id="pf-tier-cards"></div>

  <h5 class="mt-4">累计收益对比</h5>
  <div style="position:relative; height:280px;">
    <canvas id="pf-returns-chart"></canvas>
  </div>

  <div id="pf-detail" class="mt-4"></div>
</main>
{% endblock %}

{% block scripts %}
<script src="{{ url_for('static', filename='vendor/chart.umd.min.js') }}"></script>
<script type="module" src="{{ url_for('static', filename='js/portfolio.js') }}"></script>
{% endblock %}
```

Note: Chart.js loaded via regular `<script>` (not module) because it's a UMD global. Portfolio JS accesses `window.Chart`.

- [ ] **Step 4: Verify all page templates parse**

```bash
python3 -c "
from jinja2 import Environment, FileSystemLoader
env = Environment(loader=FileSystemLoader('templates'))
for t in ['picks.html', 'plan.html', 'portfolio.html']:
    tmpl = env.get_template(t)
    print(f'  {t} OK (extends: {tmpl.extends})')
print('All page templates parse OK')
"
```

- [ ] **Step 5: Commit**

```bash
git add templates/picks.html templates/plan.html templates/portfolio.html
git commit -m "feat(ui): create Jinja2 page templates (picks/plan/portfolio)"
```

---

### Task 5: Rewrite JS modules (api.js + dom.js + table.js + dashboard.js)

**Files:**
- Create: `static/js/api.js`
- Create: `static/js/dom.js`
- Create: `static/js/table.js`
- Create: `static/js/dashboard.js`

These four are the foundation modules. Page-specific modules (picks.js, plan.js, portfolio.js) come in Task 6.

- [ ] **Step 1: Create static/js/api.js**

Translate `static/data-source.js` (67 lines) from jQuery `$.getJSON` to native `fetch`. Preserve exact same function names and dual-mode logic. Key changes:
- `$.getJSON(url, params)` → `fetch(url + '?' + new URLSearchParams(params)).then(r => r.json())`
- Return Promises (same as $.getJSON)
- POST functions use `fetch` with `method: 'POST'`

Refer to spec section 六 for the full translation table.

Export all functions as ES6 named exports.

- [ ] **Step 2: Create static/js/dom.js**

Translate `static/common.js` (64 lines) from jQuery to Vanilla JS. Key changes:
- `$('#id')` → `document.getElementById('id')`
- `$(sel).html(x)` → `el.innerHTML = x`
- `$(sel).empty()` → `el.innerHTML = ''`
- Keep `fmt()`, `esc()`, `setStatus()`, `setProgress()`, `setLog()`, `showBoardLoading()`, `whenBoardReady()`, `fillDateSelect()` with same signatures

Export all functions as ES6 named exports.

- [ ] **Step 3: Create static/js/table.js**

Extract the table rendering logic from PAGE_SCRIPT (app.py:473-686) into a reusable module. This includes:
- `renderTable(container, cols, rows, opts)` — renders sortable table with score bands
- `sortRows(rows, key, dir)` — sort helper
- Score band classes (`row-hot`, `row-warm`, etc.)

Export as ES6 named exports.

- [ ] **Step 4: Create static/js/dashboard.js**

Translate the dashboard rendering from `static/dashboard.js` (90 lines) to Vanilla JS + ES6 module. Import from `./api.js` and `./dom.js`. Same logic: fetch dashboard data, render 4 cards, auto-refresh every 15s.

Export `startDashboard()` as named export.

- [ ] **Step 5: Verify modules parse**

```bash
node --check static/js/api.js && echo "api.js OK"
node --check static/js/dom.js && echo "dom.js OK"
node --check static/js/table.js && echo "table.js OK"
node --check static/js/dashboard.js && echo "dashboard.js OK"
```

(Note: these may fail if they reference browser globals like `window`/`document`. That's expected — `--check` only validates syntax. If syntax errors appear, fix them.)

- [ ] **Step 6: Commit**

```bash
git add static/js/
git commit -m "feat(ui): create core JS modules (api/dom/table/dashboard) in Vanilla ES6"
```

---

### Task 6: Rewrite page-specific JS modules

**Files:**
- Create: `static/js/picks.js`
- Create: `static/js/plan.js`
- Create: `static/js/portfolio.js`

- [ ] **Step 1: Create static/js/picks.js**

Translate PAGE_SCRIPT (app.py:463-686) to ES6 module. Import from `./api.js`, `./dom.js`, `./table.js`, `./dashboard.js`.

Key responsibilities:
- Load dates, fill select, load picks on date change
- Render picks table with sorting/filtering
- Handle recalc/sync buttons (POST to /api/refresh, poll job)
- Render stats section
- Call `startDashboard()`

Entry point: runs on module load (ES6 modules are deferred).

- [ ] **Step 2: Create static/js/plan.js**

Translate PLAN_SCRIPT (app.py:750-1175) to ES6 module. Import from `./api.js`, `./dom.js`, `./table.js`, `./dashboard.js`.

Key responsibilities:
- Load plan dates, capital tier selection, tier filter
- Render plan table with sorting/filtering
- Handle build-plan button (POST to /api/plan/build, poll job)
- Render holdings section
- Initialize Bootstrap Popover for tier-filter-help
- Call `startDashboard()`

- [ ] **Step 3: Create static/js/portfolio.js**

Translate PORTFOLIO_SCRIPT (app.py:52-217) to ES6 module. Import from `./api.js`, `./dom.js`.

Key responsibilities:
- Load portfolio summary, render tier cards
- Draw returns chart using `window.Chart` (loaded globally via vendor script)
- Handle date/strategy filter changes
- Render detail view on tier card click

- [ ] **Step 4: Verify syntax**

```bash
node --check static/js/picks.js 2>&1 || true
node --check static/js/plan.js 2>&1 || true
node --check static/js/portfolio.js 2>&1 || true
```

- [ ] **Step 5: Commit**

```bash
git add static/js/picks.js static/js/plan.js static/js/portfolio.js
git commit -m "feat(ui): create page-specific JS modules (picks/plan/portfolio)"
```

---

### Task 7: Refactor app.py routes to use render_template

**Files:**
- Modify: `app.py`

This is the critical task. Transform app.py from 1499 lines to ~400 lines.

- [ ] **Step 1: Add Jinja2 imports and remove old helpers**

At the top of `create_app()`, configure Flask with template_folder and static_folder. Remove `_page()`, `_esc_html()`, `_nav()`, `_footer()` functions. Remove `_APP_CSS`, `_SHELL`, `_FAVICON_SVG`, `_FAVICON_HREF` constants.

- [ ] **Step 2: Update page routes**

Replace the three page routes:

```python
@app.get("/")
def index():
    return render_template("picks.html",
        today=date.today().isoformat(),
        app_mode="api", data_prefix="",
        year=date.today().year)

@app.get("/plan")
def plan_page():
    return render_template("plan.html",
        today=date.today().isoformat(),
        capital_tiers=CAPITAL_TIERS,
        tier_filter_options=_TIER_FILTER_OPTIONS,
        app_mode="api", data_prefix="",
        year=date.today().year)

@app.get("/portfolio")
def portfolio_page():
    return render_template("portfolio.html",
        today=date.today().isoformat(),
        strategy_options=_STRATEGY_OPTIONS,  # extract from existing code
        app_mode="api", data_prefix="",
        year=date.today().year)
```

- [ ] **Step 3: Remove all inline HTML/CSS/JS constants**

Delete: `PORTFOLIO_BODY`, `PORTFOLIO_SCRIPT`, `_FAVICON_SVG`, `_FAVICON_HREF`, `_APP_CSS`, `_SHELL`, `PAGE_BODY`, `PAGE_SCRIPT`, `PLAN_BODY`, `PLAN_SCRIPT`, `_TIER_FILTER_OPTIONS` (move generation logic inline or to a helper if still needed by export_json.py).

Keep `_TIER_FILTER_OPTIONS` generation if `export_json.py` still needs it (see Task 8).

- [ ] **Step 4: Verify app starts and pages render**

```bash
# Start Flask briefly and test
python3 -c "
from app import create_app
app = create_app()
client = app.test_client()
for path in ['/', '/plan', '/portfolio']:
    resp = client.get(path)
    assert resp.status_code == 200, f'{path} returned {resp.status_code}'
    html = resp.data.decode()
    assert 'bootstrap.min.css' in html, f'{path} missing bootstrap'
    assert 'app.css' in html, f'{path} missing app.css'
    assert 'type=\"module\"' in html, f'{path} missing ES6 module'
    assert 'jquery' not in html.lower(), f'{path} still has jQuery'
    print(f'  {path} OK ({len(html)} bytes)')
print('All pages render correctly')
"
```

- [ ] **Step 5: Run full test suite**

```bash
python3 -m pytest tests/ -q
```

Expected: all existing tests pass (some web tests may need updating if they check for specific HTML patterns).

- [ ] **Step 6: Commit**

```bash
git add app.py
git commit -m "refactor(ui): app.py routes use Jinja2 render_template, remove inline HTML/CSS/JS"
```

---

### Task 8: Update export_json.py for Jinja2 static rendering

**Files:**
- Modify: `export_json.py`

- [ ] **Step 1: Replace _page() calls with Jinja2 rendering**

The current `export()` function uses `_page()` with string replacement. Replace with Jinja2 `Environment`:

```python
from jinja2 import Environment, FileSystemLoader

def _render_static_page(template_name, **kwargs):
    env = Environment(loader=FileSystemLoader("templates"))
    # In static mode, url_for doesn't work — provide a custom function
    env.globals['url_for'] = lambda endpoint, **kw: f"static/{kw.get('filename', '')}"
    return env.get_template(template_name).render(
        app_mode="static",
        data_prefix="",
        **kwargs
    )
```

Update the `export()` function to use this for index.html, plan.html, and add portfolio.html generation.

- [ ] **Step 2: Update static asset copying**

Replace the old `shutil.copy` of individual JS files with copying the entire new structure:

```python
# Copy JS modules
src_js = Path(__file__).parent / "static" / "js"
dst_js = static_dir / "js"
dst_js.mkdir(exist_ok=True)
for f in src_js.glob("*.js"):
    shutil.copy(f, dst_js / f.name)

# Copy CSS
dst_css = static_dir / "css"
dst_css.mkdir(exist_ok=True)
shutil.copy(Path(__file__).parent / "static" / "css" / "app.css", dst_css / "app.css")

# Copy vendor
dst_vendor = static_dir / "vendor"
dst_vendor.mkdir(exist_ok=True)
for f in (Path(__file__).parent / "static" / "vendor").glob("*"):
    shutil.copy(f, dst_vendor / f.name)

# Copy favicon
shutil.copy(Path(__file__).parent / "static" / "favicon.svg", static_dir / "favicon.svg")
```

- [ ] **Step 3: Remove old dependencies**

Remove imports of `PAGE_BODY`, `PAGE_SCRIPT`, `PLAN_BODY`, `PLAN_SCRIPT`, `_page`, `_strip_write_controls` from app.py. These are no longer needed since templates handle everything.

If `_strip_write_controls` logic is still needed (hiding write buttons in static mode), implement it in the template via `{% if show_write_controls|default(true) %}` conditional, controlled by the template context.

- [ ] **Step 4: Test static export**

```bash
python3 export_json.py hs300.db /tmp/static_test
ls /tmp/static_test/*.html
ls /tmp/static_test/static/js/
ls /tmp/static_test/static/vendor/
# Verify no jQuery in generated HTML
grep -ri jquery /tmp/static_test/*.html && echo "FAIL" || echo "OK: No jQuery"
rm -rf /tmp/static_test
```

- [ ] **Step 5: Commit**

```bash
git add export_json.py
git commit -m "refactor(ui): export_json.py uses Jinja2 for static HTML generation"
```

---

### Task 9: Write template rendering tests + cleanup

**Files:**
- Create: `tests/test_ui_templates.py`
- Delete: `static/common.js`
- Delete: `static/data-source.js`
- Delete: `static/dashboard.js`

- [ ] **Step 1: Create tests/test_ui_templates.py**

```python
"""UI template rendering tests — verify Jinja2 migration correctness."""
import pytest


@pytest.fixture
def client():
    from app import create_app
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def test_picks_renders(client):
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert 'id="board"' in html
    assert 'id="dashboard"' in html
    assert 'app.css' in html
    assert 'vendor/bootstrap.min.css' in html
    assert 'jquery' not in html.lower()
    assert 'type="module"' in html


def test_plan_renders(client):
    resp = client.get("/plan")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert 'capital-btn' in html
    assert 'id="board"' in html
    assert 'jquery' not in html.lower()


def test_portfolio_renders(client):
    resp = client.get("/portfolio")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert 'pf-returns-chart' in html
    assert 'chart.umd.min.js' in html
    assert 'jquery' not in html.lower()


def test_no_jquery_in_any_page(client):
    for path in ["/", "/plan", "/portfolio"]:
        html = client.get(path).data.decode()
        assert 'jquery' not in html.lower(), f"{path} still references jQuery"


def test_no_cdn_references(client):
    for path in ["/", "/plan", "/portfolio"]:
        html = client.get(path).data.decode()
        assert 'bootcdn' not in html, f"{path} still references bootcdn"
        assert 'cdn.' not in html or 'chart.umd' in html, f"{path} still references external CDN"


def test_static_config_injected(client):
    html = client.get("/").data.decode()
    assert 'window.APP_MODE=' in html
    assert 'window.DATA_PREFIX=' in html


def test_nav_active_highlighting(client):
    html = client.get("/").data.decode()
    assert 'class="nav-link active"' in html  # picks is active
    html = client.get("/plan").data.decode()
    assert 'href="/plan"' in html
```

- [ ] **Step 2: Run tests**

```bash
python3 -m pytest tests/test_ui_templates.py -v
```

Expected: all pass.

- [ ] **Step 3: Delete old JS files**

```bash
rm static/common.js static/data-source.js static/dashboard.js
```

- [ ] **Step 4: Run full test suite**

```bash
python3 -m pytest tests/ -q
```

Expected: all pass (baseline 362 + new template tests).

- [ ] **Step 5: Final verification**

```bash
# No jQuery anywhere
grep -ri 'jquery' static/ templates/ app.py && echo "FAIL" || echo "OK: No jQuery"
# No CDN references
grep -r 'bootcdn\|cdn\.jsdelivr\|cdn\.bootcss' templates/ app.py && echo "FAIL" || echo "OK: No CDN"
# All vendor files present
ls -la static/vendor/
# app.py line count
wc -l app.py  # Should be ~400-500
```

- [ ] **Step 6: Commit**

```bash
git add tests/test_ui_templates.py
git rm static/common.js static/data-source.js static/dashboard.js
git add -u
git commit -m "test(ui): add template rendering tests + remove old jQuery-based JS files"
```

---

### Task 10: Documentation update

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update CLAUDE.md Architecture section**

Update the Web bullet point:

```markdown
- **Web** — `app.py` (Flask, ~400 lines) serves Jinja2 templates from `templates/` and static assets from `static/`. Pages: picks (`/`), plan (`/plan`), portfolio (`/portfolio`). JS is Vanilla ES6 modules (no jQuery). Bootstrap 5.3.3 and Chart.js 4.4.1 are vendored locally in `static/vendor/`. Dual deployment: Flask API mode and static JSON mode (gh-pages via `export_json.py`).
```

- [ ] **Step 2: Update README.md**

Add a note about the UI architecture in the Web 看板 section, mentioning the local vendor files and no-jQuery setup.

- [ ] **Step 3: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs(ui): update architecture docs for Jinja2 + Vanilla JS refactor"
```

---

## Self-Review Checklist

**Spec coverage:**
- ✅ §三 Target file structure → Tasks 1-6
- ✅ §四 app.py slimming → Task 7
- ✅ §五 Jinja2 inheritance → Tasks 3-4
- ✅ §六 JS de-jQuery + ES6 modules → Tasks 5-6
- ✅ §七 CDN localization → Task 1
- ✅ §八 Static deployment compat → Task 8
- ✅ §九 Testing → Task 9
- ✅ §十 Risk control → Verified throughout

**Placeholder scan:** No TBD/TODO/FIXME found.

**Type/function consistency:**
- `url_for('static', filename=...)` used consistently in templates
- `app_mode` / `data_prefix` / `year` passed to all templates
- ES6 `export` / `import` consistent across JS modules
- `isStatic()` function name preserved from original data-source.js
