# 决策台登录改造 + 整页优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 决策台登录改为 Bootstrap Modal（用户名+密码双因子），并修复错误提示静默失效、消除空仓内容重复、加骨架屏与响应式。

**Architecture:** Worker 端 `/login` 校验 `DECIDE_USER` + `DECIDE_PASSWORD`，`/health` 回传 `user` 让刷新后仍显示登录态；前端用 `templates/components/decide_login_modal.html` + `window.bootstrap.Modal`，彻底移除 `prompt()`；渲染逻辑收敛到 `renderAll()` 统一处理空仓/有持仓两条路径。

**Tech Stack:** Cloudflare Workers (ESM, Web Crypto), Bootstrap 5.3.3 Modal, Vanilla ES6 modules

**Spec:** `docs/superpowers/specs/2026-10-25-decide-login-ui-design.md`

---

### Task 1: Worker 双因子认证

**Files:**
- Modify: `worker/auth.js`
- Modify: `worker/index.js`
- Test: `tests/test_worker_auth.mjs`

- [ ] **Step 1: 扩充 auth 单测（先失败）**

在 `tests/test_worker_auth.mjs` 末尾追加：

```javascript
import { readAuth } from '../worker/auth.js';

function fakeRequest(cookieHeader) {
  return {
    headers: {
      get(name) {
        return name.toLowerCase() === 'cookie' ? cookieHeader : null;
      },
    },
  };
}

test('readAuth 无 cookie 返回 null', async () => {
  assert.equal(await readAuth(fakeRequest(null), { DECIDE_SECRET: SECRET }), null);
});

test('readAuth 有效 cookie 返回 payload', async () => {
  const token = await signToken({ user: 'alice', exp: Date.now() + 60000 }, SECRET);
  const payload = await readAuth(fakeRequest(`decide_auth=${token}`), { DECIDE_SECRET: SECRET });
  assert.ok(payload);
  assert.equal(payload.user, 'alice');
});

test('readAuth 过期 token 返回 null', async () => {
  const token = await signToken({ user: 'alice', exp: Date.now() - 1000 }, SECRET);
  assert.equal(await readAuth(fakeRequest(`decide_auth=${token}`), { DECIDE_SECRET: SECRET }), null);
});
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /Users/shichaopeng/Work/self-dir/projects/a-finder-cli
node --test tests/test_worker_auth.mjs 2>&1 | grep -E "^# (pass|fail)|SyntaxError" | head -4
```

Expected: FAIL — `readAuth` 未导出（`SyntaxError: The requested module does not provide an export named 'readAuth'`）

- [ ] **Step 3: 在 auth.js 增加 readAuth**

在 `worker/auth.js` 末尾追加：

```javascript
const COOKIE_NAME = 'decide_auth';

/** 读取并校验请求的登录态，返回 payload（含 user）或 null。 */
export async function readAuth(request, env) {
  if (!env || !env.DECIDE_SECRET) return null;
  const token = parseCookie(request.headers.get('Cookie'), COOKIE_NAME);
  if (!token) return null;
  return verifyToken(token, env.DECIDE_SECRET);
}
```

- [ ] **Step 4: 跑测试确认通过**

```bash
node --test tests/test_worker_auth.mjs 2>&1 | grep -E "^# (pass|fail)"
```

Expected: `# pass 9` / `# fail 0`

- [ ] **Step 5: index.js 改双因子 + health 回传 user**

把 `worker/index.js` 的 `isAuthed` 改为复用 `readAuth`，并更新 import：

```javascript
import {
  buildCookie, clearCookie, parseCookie, readAuth, signToken, verifyToken,
} from './auth.js';

const COOKIE_NAME = 'decide_auth';
const MAX_AGE = 7 * 24 * 3600;           // 7 天（秒）
```

删除原来的 `isAuthed` 函数，替换为：

```javascript
async function isAuthed(request, env) {
  return (await readAuth(request, env)) !== null;
}
```

`/api/decide/health` 分支改为：

```javascript
    // ---- health ----
    if (path === '/api/decide/health' && method === 'GET') {
      const payload = await readAuth(request, env);
      let d1 = 'ok';
      try {
        await env.DB.prepare('SELECT 1').first();
      } catch (e) {
        d1 = 'error: ' + (e && e.message ? e.message : e);
      }
      return json({
        ok: d1 === 'ok',
        d1,
        authed: payload !== null,
        user: payload ? payload.user || null : null,
      }, 200, origin);
    }
```

`/api/decide/login` 分支改为：

```javascript
    // ---- login ----
    if (path === '/api/decide/login' && method === 'POST') {
      const body = await request.json().catch(() => ({}));
      const userOk = !!env.DECIDE_USER && body.user === env.DECIDE_USER;
      const passOk = !!env.DECIDE_PASSWORD && body.password === env.DECIDE_PASSWORD;
      if (!userOk || !passOk) {
        // 不区分用户名错还是密码错，避免探测
        return json({ error: '用户名或密码错误' }, 401, origin);
      }
      const token = await signToken(
        { user: env.DECIDE_USER, exp: Date.now() + MAX_AGE * 1000 },
        env.DECIDE_SECRET,
      );
      return json({ ok: true, user: env.DECIDE_USER }, 200, origin, {
        'Set-Cookie': buildCookie(token, MAX_AGE),
      });
    }
```

> `parseCookie` / `verifyToken` 若在 index.js 中已不再直接使用，从 import 中移除避免未使用告警（Node 不报错，但保持整洁）。

- [ ] **Step 6: 语法检查 + 全部 node 测试**

```bash
cd /Users/shichaopeng/Work/self-dir/projects/a-finder-cli
node --check worker/index.js && node --check worker/auth.js && echo "语法 OK"
node --test tests/test_worker_auth.mjs 2>&1 | grep -E "^# (pass|fail)"
```

Expected: `语法 OK` + `# pass 9` / `# fail 0`

- [ ] **Step 7: 提交**

```bash
git add worker/auth.js worker/index.js tests/test_worker_auth.mjs
git commit -m "feat(worker): 登录改双因子（用户名+密码），health 回传 user"
```

---

### Task 2: `dom.setStatus` 支持容器参数 + 补 `#decide-status`

**Files:**
- Modify: `static/js/dom.js`
- Modify: `templates/decide.html`
- Test: `tests/test_decide_web.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_decide_web.py` 末尾追加：

```python
class TestSetStatusContainer:

    def _js(self, name):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "static", "js", name,
        )
        return open(path, encoding="utf-8").read()

    def test_dom_setstatus_has_container_param(self):
        src = self._js("dom.js")
        assert "containerId" in src, "setStatus 需支持容器参数"
        assert "containerId = 'status'" in src, "默认值应保持 'status'（向后兼容）"

    def test_decide_html_has_status_div(self, client):
        body = client.get("/decide").data.decode("utf-8")
        assert 'id="decide-status"' in body

    def test_decide_js_uses_decide_status(self):
        src = self._js("decide.js")
        assert "'decide-status'" in src, "decide.js 的 setStatus 应指向 #decide-status"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /Users/shichaopeng/Work/self-dir/projects/a-finder-cli
python3 -m pytest tests/test_decide_web.py::TestSetStatusContainer -q 2>&1 | tail -6
```

Expected: FAIL（3 项）

- [ ] **Step 3: 改 dom.js**

把 `static/js/dom.js` 的 `setStatus` 替换为：

```javascript
export function setStatus(html, cls, containerId = 'status') {
  const el = $(containerId);
  if (!el) return;
  el.innerHTML = html ? `<div class="alert ${cls}" role="status">${html}</div>` : '';
  if (html) {
    const plain = String(html).replace(/<[^>]+>/g, '');
    setTimeout(() => {
      if (el.textContent && el.textContent.includes(plain)) el.innerHTML = '';
    }, 6000);
  }
}
```

- [ ] **Step 4: decide.html 加状态区**

在 `templates/decide.html` 的 `<div id="decide-auth" class="mb-3"></div>` 之后插入：

```html
  <div id="decide-status" class="mb-3"></div>
```

- [ ] **Step 5: decide.js 改用 decide-status**

把 `static/js/decide.js` 中所有 `setStatus(` 调用的第三参补上 `'decide-status'`。当前共 5 处（`加载中…` / `''` / `加载失败` / `登录失败` / `保存失败` / `删除失败`），逐处改为例如：

```javascript
setStatus('加载中…', 'alert-info', 'decide-status');
setStatus('', '', 'decide-status');
setStatus('加载失败：' + msg, 'alert-danger', 'decide-status');
```

- [ ] **Step 6: 跑测试**

```bash
python3 -m pytest tests/test_decide_web.py -q 2>&1 | tail -4
```

Expected: all PASS

- [ ] **Step 7: 提交**

```bash
git add static/js/dom.js static/js/decide.js templates/decide.html tests/test_decide_web.py
git commit -m "fix(decide): 补 #decide-status 修复错误提示静默失效；setStatus 支持容器参数"
```

---

### Task 3: 登录 Modal（替换 prompt）

**Files:**
- Create: `templates/components/decide_login_modal.html`
- Modify: `templates/decide.html`
- Modify: `static/js/api.js`
- Modify: `static/js/decide.js`
- Test: `tests/test_decide_web.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_decide_web.py` 末尾追加：

```python
class TestLoginModal:

    def _js(self, name):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "static", "js", name,
        )
        return open(path, encoding="utf-8").read()

    def test_no_prompt_in_decide_js(self):
        """禁用浏览器原生弹窗。"""
        src = self._js("decide.js")
        assert "prompt(" not in src
        assert "alert(" not in src

    def test_login_modal_fields(self, client):
        body = client.get("/decide").data.decode("utf-8")
        assert 'id="login-modal"' in body
        assert 'id="login-user"' in body
        assert 'id="login-pass"' in body
        assert 'id="login-error"' in body

    def test_login_passes_both_credentials(self):
        src = self._js("decide.js")
        assert "loginDecide(user" in src or "loginDecide(user," in src

    def test_api_login_signature(self):
        src = self._js("api.js")
        assert "export function loginDecide(user, password)" in src
        assert "body: JSON.stringify({ user, password })" in src
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python3 -m pytest tests/test_decide_web.py::TestLoginModal -q 2>&1 | tail -6
```

Expected: FAIL（4 项）

- [ ] **Step 3: 创建登录 Modal 组件**

创建 `templates/components/decide_login_modal.html`：

```html
<div class="modal fade" id="login-modal" tabindex="-1" aria-hidden="true">
  <div class="modal-dialog modal-dialog-centered modal-sm">
    <div class="modal-content">
      <form id="login-form" autocomplete="on">
        <div class="modal-header py-2">
          <h6 class="modal-title">登录决策台</h6>
          <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="关闭"></button>
        </div>
        <div class="modal-body">
          <div id="login-error" class="alert alert-danger py-2 mb-2 d-none" role="alert"></div>
          <div class="mb-2">
            <label class="form-label mb-1 small" for="login-user">用户名</label>
            <input id="login-user" class="form-control form-control-sm"
                   autocomplete="username" required>
          </div>
          <div class="mb-2">
            <label class="form-label mb-1 small" for="login-pass">密码</label>
            <input id="login-pass" type="password" class="form-control form-control-sm"
                   autocomplete="current-password" required>
          </div>
        </div>
        <div class="modal-footer py-2">
          <button type="button" class="btn btn-sm btn-secondary" data-bs-dismiss="modal">取消</button>
          <button type="submit" class="btn btn-sm btn-primary" id="login-submit">登录</button>
        </div>
      </form>
    </div>
  </div>
</div>
```

- [ ] **Step 4: decide.html include 该组件**

在 `templates/decide.html` 的 `{% block body %}` 内、`</main>` 之前插入：

```html
  {% include 'components/decide_login_modal.html' %}
```

- [ ] **Step 5: api.js 改 loginDecide 签名**

把 `static/js/api.js` 的 `loginDecide` 替换为：

```javascript
export function loginDecide(user, password) {
  return reqJSON(API_BASE + '/api/decide/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user, password }),
  });
}
```

- [ ] **Step 6: decide.js 用 Modal 替换 prompt**

把 `static/js/decide.js` 的登录态部分（`let AUTHED` 到 `showLoginForm` 结束）替换为：

```javascript
let AUTHED = false;
let CURRENT_USER = '';
let CFG = null;

function renderAuthBar() {
  const el = $('decide-auth');
  if (!el) return;
  if (AUTHED) {
    el.innerHTML = `<div class="d-flex align-items-center gap-2 small">
      <span class="text-success">👤 <strong>${esc(CURRENT_USER || '已登录')}</strong></span>
      <span class="text-muted">可录入 / 编辑持仓</span>
      <button id="btn-logout" class="btn btn-sm btn-outline-secondary ms-auto">登出</button>
    </div>`;
  } else {
    el.innerHTML = `<div class="d-flex align-items-center gap-2 small">
      <span class="text-muted">只读模式 · 持仓存于云端</span>
      <button id="btn-login" class="btn btn-sm btn-primary ms-auto">登录</button>
    </div>`;
  }
}

function _modal() {
  const el = document.getElementById('login-modal');
  if (!el) return null;
  if (window.bootstrap && window.bootstrap.Modal) {
    return window.bootstrap.Modal.getOrCreateInstance(el);
  }
  return null;
}

function openLoginModal() {
  const errEl = $('login-error');
  if (errEl) { errEl.classList.add('d-none'); errEl.textContent = ''; }
  const pass = $('login-pass');
  if (pass) pass.value = '';
  const m = _modal();
  if (m) {
    m.show();
    setTimeout(() => { const u = $('login-user'); if (u) u.focus(); }, 250);
  }
}

function closeLoginModal() {
  const m = _modal();
  if (m) m.hide();
}

function showLoginError(msg) {
  const errEl = $('login-error');
  if (!errEl) return;
  errEl.textContent = msg;
  errEl.classList.remove('d-none');
}

async function submitLogin(ev) {
  if (ev) ev.preventDefault();
  const userEl = $('login-user');
  const passEl = $('login-pass');
  const btn = $('login-submit');
  if (!userEl || !passEl) return;
  const user = userEl.value.trim();
  const password = passEl.value;
  const errEl = $('login-error');
  if (errEl) { errEl.classList.add('d-none'); errEl.textContent = ''; }
  if (btn) { btn.disabled = true; btn.textContent = '登录中…'; }
  try {
    const res = await loginDecide(user, password);
    AUTHED = true;
    CURRENT_USER = (res && res.user) || user;
    closeLoginModal();
    renderAuthBar();
    await loadAll();
  } catch (err) {
    const msg = err && err.status === 401
      ? '用户名或密码错误'
      : ('无法连接服务器' + (err && err.status ? `（HTTP ${err.status}）` : '') + '——Worker 可能尚未部署');
    showLoginError(msg);
    passEl.value = '';
    passEl.focus();
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '登录'; }
  }
}
```

- [ ] **Step 7: 绑定表单与按钮事件**

把 `static/js/decide.js` 的 `DOMContentLoaded` 回调替换为：

```javascript
document.addEventListener('DOMContentLoaded', () => {
  const sel = $('decide-date');
  if (sel) sel.addEventListener('change', loadAll);
  const btn = $('decide-refresh');
  if (btn) btn.addEventListener('click', loadAll);

  const form = $('login-form');
  if (form) form.addEventListener('submit', submitLogin);

  document.addEventListener('click', async e => {
    if (e.target.closest('#btn-login')) { openLoginModal(); return; }
    if (e.target.closest('#btn-logout')) {
      await logoutDecide().catch(() => {});
      AUTHED = false;
      CURRENT_USER = '';
      renderAuthBar();
      loadAll();
      return;
    }
    const add = e.target.closest('#add-position');
    if (add) { showAddForm(false); return; }
    const addCb = e.target.closest('#add-cb-position');
    if (addCb) { showAddForm(true); return; }
    const del = e.target.closest('[data-del]');
    if (del) {
      const code = del.dataset.del;
      const isCb = del.dataset.cb === '1';
      try {
        await deleteDecidePosition(code, isCb);
      } catch (err) {
        setStatus('删除失败：' + (err.error || err.status), 'alert-danger', 'decide-status');
        return;
      }
      loadAll();
    }
  });

  loadAll();
});
```

- [ ] **Step 8: loadAll 里同步 user**

把 `loadAll()` 里设置登录态的两行替换为：

```javascript
    if (authRes && typeof authRes.authed === 'boolean') {
      AUTHED = authRes.authed;
      CURRENT_USER = authRes.user || '';
    }
```

- [ ] **Step 9: 语法检查 + 跑测试**

```bash
cd /Users/shichaopeng/Work/self-dir/projects/a-finder-cli
node --check static/js/decide.js && node --check static/js/api.js && echo "语法 OK"
python3 -m pytest tests/test_decide_web.py -q 2>&1 | tail -4
```

Expected: `语法 OK` + all PASS

- [ ] **Step 10: 提交**

```bash
git add templates/components/decide_login_modal.html templates/decide.html static/js/api.js static/js/decide.js tests/test_decide_web.py
git commit -m "feat(decide): 登录改 Bootstrap Modal（用户名+密码），移除 prompt 原生弹窗"
```

---

### Task 4: renderAll 分流（消除空仓重复）+ 骨架屏 + 响应式

**Files:**
- Modify: `templates/decide.html`
- Modify: `static/js/decide.js`
- Test: `tests/test_decide_web.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_decide_web.py` 末尾追加：

```python
class TestRenderAllSplit:

    def _js(self, name):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "static", "js", name,
        )
        return open(path, encoding="utf-8").read()

    def test_render_all_exists(self):
        src = self._js("decide.js")
        assert "function renderAll" in src

    def test_comparison_hidden_when_empty(self):
        """空仓时执行对照区应被隐藏（避免与待建仓清单重复）。"""
        src = self._js("decide.js")
        seg = src.split("function renderAll")[1].split("\nfunction ")[0]
        assert "decide-comparison-wrap" in seg
        assert "style.display" in seg or "classList" in seg

    def test_plan_title_switches(self):
        src = self._js("decide.js")
        assert "待建仓清单" in src
        assert "今日执行清单" in src

    def test_skeleton_loading(self):
        src = self._js("decide.js")
        assert "function showLoading" in src
        assert "skeleton" in src

    def test_summary_responsive_classes(self, client):
        body = client.get("/decide").data.decode("utf-8")
        assert "row-cols-md-3" in body
        assert "row-cols-xl-5" in body
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python3 -m pytest tests/test_decide_web.py::TestRenderAllSplit -q 2>&1 | tail -6
```

Expected: FAIL（5 项）

- [ ] **Step 3: decide.html 加包裹层与标题 id**

把 `templates/decide.html` 的这两段：

```html
  <h5 class="mt-4">今日执行清单</h5>
  <div id="decide-plan"></div>

  <h5 class="mt-4">执行对照</h5>
  <div id="decide-comparison"></div>
```

替换为：

```html
  <h5 class="mt-4" id="decide-plan-title">今日执行清单</h5>
  <div id="decide-plan"></div>

  <div id="decide-comparison-wrap">
    <h5 class="mt-4">执行对照</h5>
    <div id="decide-comparison"></div>
  </div>
```

并把摘要卡那一行改为：

```html
  <div class="row row-cols-2 row-cols-md-3 row-cols-xl-5 g-2 mb-3" id="decide-summary"></div>
```

- [ ] **Step 4: decide.js 加 showLoading 与 renderAll**

在 `static/js/decide.js` 的 `loadAll` 之前插入：

```javascript
function showLoading() {
  const ids = ['decide-summary', 'decide-plan', 'decide-positions'];
  const tpl = '<div class="app-card skeleton-table" aria-busy="true">' +
    '<div class="skeleton skeleton-row"></div>'.repeat(3) + '</div>';
  ids.forEach(id => { const el = $(id); if (el) el.innerHTML = tpl; });
}

function renderAll(cmp, planRes, posRes, date) {
  const empty = (cmp.actual_count || 0) === 0;

  renderSummary(cmp);

  // 标题随状态切换
  const titleEl = $('decide-plan-title');
  if (titleEl) titleEl.textContent = empty ? '待建仓清单' : '今日执行清单';
  renderPlan(date, planRes);

  // 空仓：待建仓清单已含全部信息，隐藏「执行对照」避免重复
  const wrap = $('decide-comparison-wrap');
  if (wrap) wrap.style.display = empty ? 'none' : '';
  if (!empty) renderComparison(cmp);

  renderPositions(posRes.rows || [], posRes.cb_rows || []);
}
```

- [ ] **Step 5: loadAll 改用 renderAll + 骨架屏**

把 `loadAll()` 整体替换为：

```javascript
async function loadAll() {
  showLoading();
  setStatus('', '', 'decide-status');
  try {
    const [planRes, posRes, cfgRes, authRes] = await Promise.all([
      fetchDecidePlan().catch(() => ({ date: '', rows: [], dates: [] })),
      fetchDecidePositionsRemote().catch(() => ({ rows: [], cb_rows: [] })),
      fetchDecideConfig().catch(() => null),
      fetchDecideAuthState().catch(() => ({ authed: false })),
    ]);
    if (cfgRes) CFG = cfgRes;
    if (authRes && typeof authRes.authed === 'boolean') {
      AUTHED = authRes.authed;
      CURRENT_USER = authRes.user || '';
    }
    renderAuthBar();

    fillDateOptions(planRes.dates || []);
    const date = ($('decide-date') && $('decide-date').value) || planRes.date || '';

    // 静态站无实时行情：用 plan 的计划价近似最新价（止损/止盈判定用）
    const prices = {};
    (planRes.rows || []).forEach(r => {
      if (r.plan_price != null) prices[r.code] = r.plan_price;
    });

    const cmp = CFG
      ? computeComparison(
          planRes.rows || [], posRes.rows || [], posRes.cb_rows || [], prices, CFG,
        )
      : {
          deviations: [], pending_build: [], stock_used: 0, cb_used: 0, cash: 0,
          actual_count: 0, cb_count: 0, plan_count: 0,
          danger_count: 0, warn_count: 0, info_count: 0,
        };

    renderAll(cmp, planRes, posRes, date);
  } catch (e) {
    setStatus('加载失败：' + (e && e.message ? e.message : e), 'alert-danger', 'decide-status');
  }
}
```

- [ ] **Step 6: 语法检查 + 跑测试**

```bash
cd /Users/shichaopeng/Work/self-dir/projects/a-finder-cli
node --check static/js/decide.js && echo "语法 OK"
python3 -m pytest tests/test_decide_web.py -q 2>&1 | tail -4
```

Expected: `语法 OK` + all PASS

- [ ] **Step 7: 全量回归**

```bash
python3 -m pytest tests/ -q 2>&1 | tail -3
```

Expected: all PASS（含 6 个 parity 测试）

- [ ] **Step 8: 提交**

```bash
git add templates/decide.html static/js/decide.js tests/test_decide_web.py
git commit -m "feat(decide): renderAll 统一空仓/有持仓分支（消除重复）+ 骨架屏 + 响应式"
```

---

### Task 5: 部署脚本 + 文档 + 端到端验证

**Files:**
- Modify: `scripts/deploy-worker.sh`
- Modify: `CLAUDE.md`

- [ ] **Step 1: 部署脚本加 DECIDE_USER**

把 `scripts/deploy-worker.sh` 的第 3 步改为：

```bash
echo
echo "=== 3. 设置登录用户名 ==="
$WRANGLER secret put DECIDE_USER

echo
echo "=== 4. 设置登录密码 ==="
echo "（交互式输入，不会进 shell 历史）"
$WRANGLER secret put DECIDE_PASSWORD

echo
echo "=== 5. 设置签名密钥 ==="
echo "建议用：openssl rand -hex 32"
$WRANGLER secret put DECIDE_SECRET

echo
echo "=== 6. 部署 Worker ==="
$WRANGLER deploy

echo
echo "=== 7. 自检 ==="
sleep 3
echo "--- health ---"
curl -s https://ycu.space/api/decide/health || echo "（若失败，等 30s 让路由生效后重试）"
echo
echo "--- 未登录写入应 401 ---"
curl -s -o /dev/null -w "HTTP %{http_code}\n" -X POST https://ycu.space/api/decide/positions \
  -H 'Content-Type: application/json' \
  -d '{"code":"600519","shares":100,"entry_price":1500}'
echo
echo "--- 错误凭据登录应 401 ---"
curl -s -o /dev/null -w "HTTP %{http_code}\n" -X POST https://ycu.space/api/decide/login \
  -H 'Content-Type: application/json' \
  -d '{"user":"nobody","password":"wrong"}'
```

（原来的 3~6 步编号相应顺延，最后提示语保持不变）

- [ ] **Step 2: 脚本语法检查**

```bash
cd /Users/shichaopeng/Work/self-dir/projects/a-finder-cli
bash -n scripts/deploy-worker.sh && echo "脚本语法 OK"
grep -c "DECIDE_USER" scripts/deploy-worker.sh
```

Expected: `脚本语法 OK` + 计数 ≥ 1

- [ ] **Step 3: 更新 CLAUDE.md**

把 Architecture 里 **持仓云端化** 那条中的认证描述改为：

```markdown
- **持仓云端化（Worker + D1）** — 持仓的唯一真相源是 **Cloudflare D1**，由 `worker/index.js` 提供 `/api/decide/*`（读公开、写需登录的 HttpOnly Cookie）。**登录是双因子**：`DECIDE_USER` + `DECIDE_PASSWORD`，签名密钥 `DECIDE_SECRET`，三者都走 `wrangler secret put`；前端用 Bootstrap Modal（`templates/components/decide_login_modal.html`），不使用浏览器原生弹窗。本地 SQLite 同名表保留但不再写入。**对比逻辑有 Python 与 JS 两份实现**：`decide_service.py`（服务端/导出）与 `static/js/decide_compare.js`（前端实时算），由 `tests/test_decide_compare_parity.py` 用 node 跑同一 fixture 保证逐项一致；配置来源 `data/decide-config.json`。D1 建表见 `worker/schema.sql`，部署见 `wrangler.toml`（路由 `ycu.space/api/*`）与 `scripts/deploy-worker.sh`。
```

- [ ] **Step 4: 全量测试**

```bash
python3 -m pytest tests/ -q 2>&1 | tail -3
node --test tests/test_worker_auth.mjs 2>&1 | grep -E "^# (pass|fail)"
```

Expected: pytest all PASS + node `# pass 9` / `# fail 0`

- [ ] **Step 5: 导出静态站并本地验证渲染**

```bash
cd /Users/shichaopeng/Work/self-dir/projects/a-finder-cli
rm -rf /tmp/site-login
python3 export_json.py --db hs300.db --out /tmp/site-login 2>&1 | tail -1
grep -c 'id="login-modal"' /tmp/site-login/decide.html
grep -c 'id="decide-status"' /tmp/site-login/decide.html
grep -c 'row-cols-xl-5' /tmp/site-login/decide.html
```

Expected: 三个计数都 ≥ 1

- [ ] **Step 6: 提交 + 推送**

```bash
git add scripts/deploy-worker.sh CLAUDE.md
git commit -m "chore(worker): 部署脚本加 DECIDE_USER；文档更新双因子登录"
git push origin main
```

- [ ] **Step 7: 部署 gh-pages（前端改动需上线）**

```bash
cd /Users/shichaopeng/Work/self-dir/projects/a-finder-cli
# 重新导出（缓存破坏版本对齐最新 sha）
rm -rf /tmp/site-login2
python3 export_json.py --db hs300.db --out /tmp/site-login2
git worktree prune; rm -rf /tmp/ghpp
git worktree add /tmp/ghpp gh-pages
rm -rf /tmp/ghpp/*
cp -R /tmp/site-login2/* /tmp/ghpp/
cd /tmp/ghpp && git add -A && git commit -m "site: 决策台登录 Modal + 整页优化" && git push origin gh-pages
cd /Users/shichaopeng/Work/self-dir/projects/a-finder-cli
git worktree remove /tmp/ghpp --force
```

- [ ] **Step 8: 浏览器端到端（需 Worker 已部署）**

打开 https://ycu.space/decide.html：
1. 顶部显示「只读模式 · 持仓存于云端 [登录]」
2. 点登录 → **Bootstrap Modal 弹出**（不是浏览器原生弹窗），光标在用户名框
3. 输错用户名或密码 → Modal 内**红字提示**，密码框清空并聚焦
4. 输对 → Modal 关闭 → 顶部变「👤 用户名」，出现录入按钮
5. 刷新页面 → 仍显示「👤 用户名」（cookie 有效）
6. 空仓时：只有「待建仓清单」一处，**「执行对照」区块隐藏**
7. 录入一笔持仓后：出现「执行对照」，标题变「今日执行清单」
8. 点登出 → 恢复只读

---

## Self-Review

**Spec coverage:**
- §四 信息架构重排 → Task 4（renderAll + comparison-wrap + 标题切换）
- §五 登录 Modal（前端）→ Task 3 Step 3-8
- §五 双因子（后端）→ Task 1
- §五 部署影响（DECIDE_USER）→ Task 5 Step 1
- §六.1 修复静默失效 → Task 2
- §六.2 骨架屏 → Task 4 Step 4
- §六.3 响应式 → Task 4 Step 3
- §七 文件清单 → Tasks 1-5 全覆盖
- §八 测试策略 → Task 1/2/3/4 各自测试
- §九 验证计划 → Task 5 Step 5/8

**Placeholder scan:** 无 TBD/TODO。所有代码块完整。

**Type consistency:**
- `readAuth(request, env)` → Task 1 定义，`isAuthed` 复用，一致
- `setStatus(html, cls, containerId='status')` → Task 2 定义，decide.js 传 `'decide-status'`，一致
- `loginDecide(user, password)` → Task 3 Step 5 定义，Step 6 调用 `loginDecide(user, password)`，一致
- `renderAll(cmp, planRes, posRes, date)` → Task 4 定义与调用一致
- `CURRENT_USER` → Task 3 定义，`renderAuthBar` 与 `loadAll` 使用一致
- health 返回 `{ok, d1, authed, user}` → Task 1 定义，Task 3 Step 8 消费一致
- `decide-comparison-wrap` / `decide-plan-title` → Task 4 Step 3 建元素，Step 4 使用一致
