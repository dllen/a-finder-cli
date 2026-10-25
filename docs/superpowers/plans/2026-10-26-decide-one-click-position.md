# 决策台一键建仓 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 待建仓清单加行内「建仓」与顶部「全部建仓」，把计划票一键写入实际持仓（D1）；已持有一律禁用；Worker 未部署时禁用并提示。

**Architecture:** 纯前端（零 Worker 改动）。模块级 `POSITIONS` / `WORKER_OK` / `PENDING_PLAN_ROW` 三个状态；`renderPlan` 增加「操作」列；把现有手写 modal `showAddForm` 改造成通用 `openPositionForm({prefill, readonly})`；新增 `openAllPositions()` 用 `Promise.allSettled` 批量提交并汇总结果。

**Tech Stack:** Vanilla ES6 modules, Bootstrap 5.3.3 Modal, 现有 `postDecidePosition` API

**Spec:** `docs/superpowers/specs/2026-10-26-decide-one-click-position-design.md`

---

### Task 1: 状态与「已持有」判定

**Files:**
- Modify: `static/js/decide.js`
- Test: `tests/test_decide_web.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_decide_web.py` 末尾追加：

```python
class TestOneClickState:

    def _js(self, name):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "static", "js", name,
        )
        return open(path, encoding="utf-8").read()

    def test_positions_state_var(self):
        assert "let POSITIONS = []" in self._js("decide.js")

    def test_worker_ok_state_var(self):
        assert "let WORKER_OK" in self._js("decide.js")

    def test_held_codes_judgement(self):
        src = self._js("decide.js")
        assert "heldCodes" in src
        assert "isHeld" in src

    def test_positions_synced_from_loadall(self):
        src = self._js("decide.js")
        # POSITIONS 必须在数据加载后被赋值
        assert "POSITIONS = posRes.rows" in src or "POSITIONS = (posRes.rows" in src
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /Users/shichaopeng/Work/self-dir/projects/a-finder-cli
python3 -m pytest tests/test_decide_web.py::TestOneClickState -q 2>&1 | tail -6
```

Expected: FAIL（4 项）

- [ ] **Step 3: 加模块级状态**

在 `static/js/decide.js` 的 `let AUTHED = false;` 附近（登录态区块）追加：

```javascript
let POSITIONS = [];              // 实际持仓（供「已持有」判定）
let WORKER_OK = true;            // Worker 可用性（决定建仓按钮是否可点）
let PENDING_PLAN_ROW = null;     // 未登录时记住待建仓的 plan 行
```

- [ ] **Step 4: 加判定辅助函数**

在 `renderPlan` 之前插入：

```javascript
function heldCodeSet() {
  return new Set((POSITIONS || []).map(p => String(p.code)));
}

function isHeldRow(planRow, heldSet) {
  return heldSet.has(String(planRow.code));
}

function todayISO() {
  return new Date().toISOString().slice(0, 10);
}
```

- [ ] **Step 5: loadAll 同步 POSITIONS 与 WORKER_OK**

把 `loadAll()` 里取 auth 的那行改为 try/catch，并在拿到 posRes 后赋值 `POSITIONS`。

当前这段：

```javascript
    const [planRes, posRes, cfgRes, authRes] = await Promise.all([
      fetchDecidePlan().catch(() => ({ date: '', rows: [], dates: [] })),
      fetchDecidePositionsRemote().catch(() => ({ rows: [], cb_rows: [] })),
      fetchDecideConfig().catch(() => null),
      fetchDecideAuthState().catch(() => ({ authed: false })),
    ]);
```

替换为：

```javascript
    const [planRes, posRes, cfgRes, authRes] = await Promise.all([
      fetchDecidePlan().catch(() => ({ date: '', rows: [], dates: [] })),
      fetchDecidePositionsRemote().catch(() => ({ rows: [], cb_rows: [] })),
      fetchDecideConfig().catch(() => null),
      fetchDecideAuthState().then(
        r => { WORKER_OK = true; return r; },
        () => { WORKER_OK = false; return { authed: false }; },
      ),
    ]);
    POSITIONS = posRes.rows || [];
```

- [ ] **Step 6: 跑测试**

```bash
python3 -m pytest tests/test_decide_web.py::TestOneClickState -q 2>&1 | tail -4
```

Expected: PASS（4 项）

- [ ] **Step 7: 提交**

```bash
git add static/js/decide.js tests/test_decide_web.py
git commit -m "feat(decide): 一键建仓的状态与已持有判定（POSITIONS/WORKER_OK）"
```

---

### Task 2: 清单加「操作」列与「全部建仓」按钮

**Files:**
- Modify: `static/js/decide.js`
- Test: `tests/test_decide_web.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_decide_web.py`：

```python
class TestPlanActionColumn:

    def _js(self, name):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "static", "js", name,
        )
        return open(path, encoding="utf-8").read()

    def test_action_column_header(self):
        assert "<th>操作</th>" in self._js("decide.js")

    def test_held_button_disabled(self):
        src = self._js("decide.js")
        assert 'data-open' in src
        assert "已持有" in src
        assert "disabled" in src

    def test_all_positions_button(self):
        src = self._js("decide.js")
        assert "全部建仓" in src
        assert "id=\"btn-open-all\"" in src

    def test_worker_down_disables(self):
        src = self._js("decide.js")
        # Worker 不可用时按钮禁用
        assert "WORKER_OK" in src
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python3 -m pytest tests/test_decide_web.py::TestPlanActionColumn -q 2>&1 | tail -6
```

Expected: FAIL（4 项）

- [ ] **Step 3: 汇总条加「全部建仓」按钮**

把 `renderPlan` 里的汇总条那段（`// 汇总条：目标单票 / 计划合计 / 剩余股票现金` 起，到 `</div>\`;` 止）替换为：

```javascript
  // 汇总条：目标单票 / 计划合计 / 剩余股票现金 + 全部建仓
  const heldSet = heldCodeSet();
  const toOpen = rows.filter(r => !isHeldRow(r, heldSet));
  const workerReady = WORKER_OK;
  const allBtn = toOpen.length
    ? `<button id="btn-open-all" class="btn btn-sm btn-success ms-auto"`
      + `${workerReady ? '' : ' disabled title="写入服务未部署"'}`
      + `>全部建仓 (${toOpen.length})</button>`
    : '<button class="btn btn-sm btn-outline-secondary ms-auto" disabled>全部已持有</button>';

  let html = `<div class="d-flex flex-wrap gap-3 align-items-center mb-2 small">
    <span>目标单票 <strong>${money(plan.target_amount)}</strong></span>
    <span>计划合计 <strong>${money(plan.planned_amount)}</strong></span>
    <span>股票仓 <strong>${money(plan.stock_capital)}</strong></span>
    <span class="${(plan.remaining_stock_cash || 0) < 0 ? 'text-danger' : 'text-muted'}">
      剩余 <strong>${money(plan.remaining_stock_cash)}</strong></span>
    ${allBtn}
  </div>`;
```

- [ ] **Step 4: 桌面表格加「操作」列**

把桌面表头那行：

```javascript
    '<th class="num">金额</th><th class="num">止损</th><th class="num">止盈</th><th class="num">评分</th>' +
```

替换为：

```javascript
    '<th class="num">金额</th><th class="num">止损</th><th class="num">止盈</th><th class="num">评分</th><th>操作</th>' +
```

并把桌面数据行末尾的 `</tr>` 前插入操作单元格。当前行尾是：

```javascript
      `<td class="num">${r.score == null ? '—' : r.score.toFixed(1)}</td></tr>`;
```

替换为：

```javascript
      `<td class="num">${r.score == null ? '—' : r.score.toFixed(1)}</td>` +
      `<td>${openButton(r, heldSet || heldCodeSet(), WORKER_OK)}</td></tr>`;
```

> 注意：`rows.forEach(r => {` 的闭包能访问 Step 3 定义的 `heldSet`；若因作用域问题取不到，直接用 `heldCodeSet()` 重建一次亦可（性能可忽略）。

- [ ] **Step 5: 移动端卡片加建仓按钮**

在移动端卡片的 `</div></div>` 结束前（`kv-grid` 之后）追加一行按钮。找到移动端卡片循环里 `+ \`</div></div>\`;` 的位置，改为：

```javascript
      `</div>` +
      `<div class="mt-2">${openButton(r, heldCodeSet(), WORKER_OK)}</div>` +
      `</div>`;
```

- [ ] **Step 6: 实现 openButton 辅助函数**

在 `heldCodeSet` 附近插入：

```javascript
function openButton(planRow, heldSet, workerReady) {
  const code = esc(planRow.code);
  if (heldSet.has(String(planRow.code))) {
    return `<button class="btn btn-sm btn-outline-secondary" disabled ` +
      `title="已在持仓中，如需修改请到下方持仓区">已持有</button>`;
  }
  const dis = workerReady ? '' : ' disabled title="写入服务未部署"';
  return `<button class="btn btn-sm btn-outline-success" data-open="${code}"${dis}>建仓</button>`;
}
```

- [ ] **Step 7: 跑测试**

```bash
node --check static/js/decide.js && echo "语法 OK"
python3 -m pytest tests/test_decide_web.py::TestPlanActionColumn -q 2>&1 | tail -4
```

Expected: `语法 OK` + PASS（4 项）

- [ ] **Step 8: 提交**

```bash
git add static/js/decide.js tests/test_decide_web.py
git commit -m "feat(decide): 待建仓清单加「操作」列与「全部建仓」按钮（已持有禁用）"
```

---

### Task 3: 通用持仓表单 `openPositionForm`

**Files:**
- Modify: `static/js/decide.js`
- Test: `tests/test_decide_web.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_decide_web.py`：

```python
class TestPositionForm:

    def _js(self, name):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "static", "js", name,
        )
        return open(path, encoding="utf-8").read()

    def test_form_function_exists(self):
        assert "function openPositionForm" in self._js("decide.js")

    def test_prefill_mapping(self):
        src = self._js("decide.js")
        # plan_price -> entry_price 预填
        assert "entry_price: prefill" in src or "entry_price: planRow.plan_price" in src

    def test_removes_stale_modal(self):
        src = self._js("decide.js")
        # 打开前先移除旧 modal，避免重复 id
        assert "getElementById('pos-modal')" in src
        seg = src.split("function openPositionForm")[1].split("\nfunction ")[0]
        assert "remove()" in seg

    def test_validates_lot_size(self):
        src = self._js("decide.js")
        assert "100" in src
        assert "整手" in src or "% 100" in src

    def test_no_prompt_still(self):
        assert "prompt(" not in self._js("decide.js")
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python3 -m pytest tests/test_decide_web.py::TestPositionForm -q 2>&1 | tail -6
```

Expected: FAIL（5 项）

- [ ] **Step 3: 用 openPositionForm 替换 showAddForm**

把 `static/js/decide.js` 里整个 `function showAddForm(isCb) { ... }`（从 `function showAddForm` 到其闭合 `}`）替换为：

```javascript
function openPositionForm(opts) {
  const o = opts || {};
  const isCb = !!o.isCb;
  const prefill = o.prefill || {};
  const readonlyKeys = o.readonly || [];

  // 移除上一次残留的 modal，避免重复 id
  const stale = document.getElementById('pos-modal');
  if (stale) stale.remove();

  const title = prefill.code
    ? `建仓：${esc(prefill.code)} ${esc(prefill.name || '')}`
    : (isCb ? '添加转债持仓' : '添加股票持仓');

  const defs = [
    ['code', '代码', 'text'],
    ['name', '名称', 'text'],
    ['shares', '股数', 'number'],
    ['entry_price', '成本价', 'number'],
    ['stop_price', '止损价（可空）', 'number'],
    ['entry_date', '建仓日', 'date'],
  ];

  const fields = defs.map(([k, label, type]) => {
    const ro = readonlyKeys.includes(k) ? ' readonly' : '';
    const val = prefill[k] == null ? '' : String(prefill[k]);
    return `<div class="mb-2"><label class="form-label mb-1 small">${label}</label>` +
      `<input type="${type}" class="form-control form-control-sm" id="fld-${k}" ` +
      `data-key="${k}" value="${esc(val)}"${ro}></div>`;
  }).join('');

  const hint = prefill.strategy
    ? `<div class="small text-muted mb-2">策略：${esc(prefill.strategy)}</div>`
    : '';

  document.body.insertAdjacentHTML('beforeend',
    `<div class="modal fade" id="pos-modal" tabindex="-1" aria-hidden="true">
      <div class="modal-dialog modal-dialog-centered modal-sm"><div class="modal-content">
        <form id="pos-form">
          <div class="modal-header py-2"><h6 class="modal-title">${title}</h6>
            <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="关闭"></button></div>
          <div class="modal-body">
            <div id="pos-error" class="alert alert-danger py-2 mb-2 d-none" role="alert"></div>
            ${hint}${fields}
            <div class="small text-muted" id="pos-amount"></div>
          </div>
          <div class="modal-footer py-2">
            <button type="button" class="btn btn-sm btn-secondary" data-bs-dismiss="modal">取消</button>
            <button type="submit" class="btn btn-sm btn-primary" id="pos-save">${prefill.code ? '确认建仓' : '保存'}</button>
          </div>
        </form>
      </div></div>
    </div>`);

  const modalEl = document.getElementById('pos-modal');
  const modal = (window.bootstrap && window.bootstrap.Modal)
    ? window.bootstrap.Modal.getOrCreateInstance(modalEl) : null;
  if (modal) modal.show();
  else { modalEl.classList.add('show'); modalEl.style.display = 'block'; }

  const $fld = k => document.getElementById('fld-' + k);
  const showErr = msg => {
    const e = document.getElementById('pos-error');
    if (e) { e.textContent = msg; e.classList.remove('d-none'); }
  };

  function refreshAmount() {
    const sh = Number($fld('shares').value || 0);
    const pr = Number($fld('entry_price').value || 0);
    const el = document.getElementById('pos-amount');
    if (el) el.textContent = sh > 0 && pr > 0 ? `金额：${money(sh * pr)}` : '';
  }
  ['shares', 'entry_price'].forEach(k => {
    const el = $fld(k);
    if (el) el.addEventListener('input', refreshAmount);
  });
  refreshAmount();

  const form = document.getElementById('pos-form');
  form.addEventListener('submit', async ev => {
    ev.preventDefault();
    const payload = { is_cb: isCb };
    defs.forEach(([k]) => {
      const inp = $fld(k);
      if (!inp) return;
      const v = inp.value.trim();
      payload[k] = v === '' ? null : (inp.type === 'number' ? Number(v) : v);
    });

    if (!payload.code) { showErr('缺少代码'); return; }
    if (!payload.shares || payload.shares <= 0) { showErr('股数必须大于 0'); return; }
    if (payload.shares % 100 !== 0) { showErr('股数必须是整手（100 的整数倍）'); return; }
    if (!payload.entry_price || payload.entry_price <= 0) { showErr('成本价必须大于 0'); return; }

    const btn = document.getElementById('pos-save');
    const old = btn ? btn.textContent : '';
    if (btn) { btn.disabled = true; btn.textContent = '提交中…'; }
    try {
      await postDecidePosition(payload);
    } catch (err) {
      showErr('保存失败：' + (err.error || err.status || '未知错误'));
      if (btn) { btn.disabled = false; btn.textContent = old; }
      return;
    }
    if (modal) modal.hide(); else modalEl.remove();
    loadAll();
  });
}
```

- [ ] **Step 4: 更新调用点**

把 `document.addEventListener('click', ...)` 里处理 `#add-position` / `#add-cb-position` 的两行：

```javascript
    const add = e.target.closest('#add-position');
    if (add) { showAddForm(false); return; }
    const addCb = e.target.closest('#add-cb-position');
    if (addCb) { showAddForm(true); return; }
```

替换为：

```javascript
    const add = e.target.closest('#add-position');
    if (add) { openPositionForm({}); return; }
    const addCb = e.target.closest('#add-cb-position');
    if (addCb) { openPositionForm({ isCb: true }); return; }
```

- [ ] **Step 5: 接上行内「建仓」按钮**

在同一个 click 委托里、上述两行之前插入：

```javascript
    const openBtn = e.target.closest('[data-open]');
    if (openBtn) {
      if (!AUTHED) {
        PENDING_PLAN_ROW = openBtn.dataset.open;
        openLoginModal();
        return;
      }
      const row = (LAST_PLAN_ROWS || []).find(r => String(r.code) === String(openBtn.dataset.open));
      if (row) {
        openPositionForm({
          prefill: {
            code: row.code,
            name: row.name,
            strategy: row.strategy,
            shares: row.shares,
            entry_price: row.plan_price,
            stop_price: row.stop_price,
            entry_date: todayISO(),
          },
          readonly: ['code', 'name'],
        });
      }
      return;
    }
```

- [ ] **Step 6: 记录 LAST_PLAN_ROWS**

在模块级状态处追加：

```javascript
let LAST_PLAN_ROWS = [];         // 最近一次渲染的 plan 行（供行内建仓取数）
```

并在 `renderAll` 开头赋值：

```javascript
function renderAll(cmp, planRes, posRes, date) {
  const empty = (cmp.actual_count || 0) === 0;
  LAST_PLAN_ROWS = planRes.rows || [];
```

- [ ] **Step 7: 登录成功后自动续上 pending 建仓**

在 `submitLogin` 成功分支里（`await loadAll();` 之后）追加：

```javascript
    if (PENDING_PLAN_ROW) {
      const code = PENDING_PLAN_ROW;
      PENDING_PLAN_ROW = null;
      const row = (LAST_PLAN_ROWS || []).find(r => String(r.code) === String(code));
      if (row) {
        openPositionForm({
          prefill: {
            code: row.code, name: row.name, strategy: row.strategy,
            shares: row.shares, entry_price: row.plan_price,
            stop_price: row.stop_price, entry_date: todayISO(),
          },
          readonly: ['code', 'name'],
        });
      }
    }
```

- [ ] **Step 8: 语法检查 + 跑测试**

```bash
node --check static/js/decide.js && echo "语法 OK"
python3 -m pytest tests/test_decide_web.py -q 2>&1 | tail -4
```

Expected: `语法 OK` + all PASS

- [ ] **Step 9: 提交**

```bash
git add static/js/decide.js tests/test_decide_web.py
git commit -m "feat(decide): 通用持仓表单 openPositionForm（预填/只读/整手校验/金额联动）"
```

---

### Task 4: 全部建仓 + 结果汇总 + Worker 降级提示

**Files:**
- Modify: `static/js/decide.js`
- Test: `tests/test_decide_web.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_decide_web.py`：

```python
class TestBulkOpen:

    def _js(self, name):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "static", "js", name,
        )
        return open(path, encoding="utf-8").read()

    def test_bulk_function_exists(self):
        assert "function openAllPositions" in self._js("decide.js")

    def test_uses_allsettled(self):
        assert "Promise.allSettled" in self._js("decide.js")

    def test_bulk_confirmation_modal(self):
        src = self._js("decide.js")
        assert "bulk-modal" in src
        assert "确认全部建仓" in src

    def test_worker_down_hint(self):
        src = self._js("decide.js")
        assert "写入服务未部署" in src
        assert "deploy-worker.sh" in src

    def test_result_summary(self):
        src = self._js("decide.js")
        assert "成功" in src and "失败" in src
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python3 -m pytest tests/test_decide_web.py::TestBulkOpen -q 2>&1 | tail -6
```

Expected: FAIL（5 项）

- [ ] **Step 3: 实现 openAllPositions**

在 `openPositionForm` 之后插入：

```javascript
function openAllPositions() {
  const heldSet = heldCodeSet();
  const targets = (LAST_PLAN_ROWS || []).filter(r => !isHeldRow(r, heldSet));
  if (!targets.length) {
    setStatus('没有待建仓的标的（全部已持有）', 'alert-info', 'decide-status');
    return;
  }

  const stale = document.getElementById('bulk-modal');
  if (stale) stale.remove();

  const total = targets.reduce((s, r) => s + (r.amount || 0), 0);
  const rowsHtml = targets.map(r =>
    `<tr><td class="code">${esc(r.code)}</td><td>${esc(r.name || '—')}</td>` +
    `<td class="num">${r.shares}</td><td class="num">${fmt(r.plan_price)}</td>` +
    `<td class="num">${money(r.amount)}</td></tr>`
  ).join('');

  document.body.insertAdjacentHTML('beforeend',
    `<div class="modal fade" id="bulk-modal" tabindex="-1" aria-hidden="true">
      <div class="modal-dialog modal-dialog-centered"><div class="modal-content">
        <div class="modal-header py-2">
          <h6 class="modal-title">全部建仓：${targets.length} 只</h6>
          <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="关闭"></button>
        </div>
        <div class="modal-body">
          <div id="bulk-error" class="alert alert-danger py-2 mb-2 d-none" role="alert"></div>
          <div class="table-responsive">
            <table class="app-table"><thead><tr>
              <th>代码</th><th>名称</th><th class="num">股数</th>
              <th class="num">成本价</th><th class="num">金额</th>
            </tr></thead><tbody>${rowsHtml}</tbody></table>
          </div>
          <div class="small text-muted mt-2">
            合计 <strong>${targets.length}</strong> 只 · <strong>${money(total)}</strong>
            · 按计划价与计划股数提交（如需改价请用行内按钮）
          </div>
        </div>
        <div class="modal-footer py-2">
          <button type="button" class="btn btn-sm btn-secondary" data-bs-dismiss="modal">取消</button>
          <button type="button" class="btn btn-sm btn-success" id="bulk-confirm">确认全部建仓</button>
        </div>
      </div></div>
    </div>`);

  const modalEl = document.getElementById('bulk-modal');
  const modal = (window.bootstrap && window.bootstrap.Modal)
    ? window.bootstrap.Modal.getOrCreateInstance(modalEl) : null;
  if (modal) modal.show();
  else { modalEl.classList.add('show'); modalEl.style.display = 'block'; }

  document.getElementById('bulk-confirm').addEventListener('click', async () => {
    const btn = document.getElementById('bulk-confirm');
    if (btn) { btn.disabled = true; btn.textContent = '提交中…'; }

    const payloads = targets.map(r => ({
      code: r.code, name: r.name, is_cb: false,
      shares: r.shares, entry_price: r.plan_price,
      stop_price: r.stop_price, entry_date: todayISO(),
    }));
    const results = await Promise.allSettled(payloads.map(postDecidePosition));

    const okCount = results.filter(x => x.status === 'fulfilled').length;
    const failed = results
      .map((x, i) => ({ res: x, row: targets[i] }))
      .filter(o => o.res.status === 'rejected');

    if (modal) modal.hide(); else modalEl.remove();

    if (!failed.length) {
      setStatus(`已建仓 ${okCount} 只，合计 ${money(total)}`, 'alert-success', 'decide-status');
    } else if (okCount === 0) {
      const first = failed[0].res.reason || {};
      setStatus(`全部建仓失败：${first.error || first.status || '未知错误'}`,
        'alert-danger', 'decide-status');
    } else {
      const detail = failed.map(o => esc(o.row.code)).join('、');
      setStatus(`成功 ${okCount} 笔，失败 ${failed.length} 笔（${detail}），可重试`,
        'alert-warning', 'decide-status');
    }
    loadAll();
  });
}
```

- [ ] **Step 4: 绑定「全部建仓」按钮**

在 click 委托里、`[data-open]` 分支之后插入：

```javascript
    if (e.target.closest('#btn-open-all')) {
      if (!AUTHED) { PENDING_PLAN_ROW = '__ALL__'; openLoginModal(); return; }
      openAllPositions();
      return;
    }
```

并在 `submitLogin` 成功分支里，把 pending 处理改为同时支持 `__ALL__`（替换 Task 3 Step 7 追加的那段）：

```javascript
    if (PENDING_PLAN_ROW) {
      const pending = PENDING_PLAN_ROW;
      PENDING_PLAN_ROW = null;
      if (pending === '__ALL__') {
        openAllPositions();
      } else {
        const row = (LAST_PLAN_ROWS || []).find(r => String(r.code) === String(pending));
        if (row) {
          openPositionForm({
            prefill: {
              code: row.code, name: row.name, strategy: row.strategy,
              shares: row.shares, entry_price: row.plan_price,
              stop_price: row.stop_price, entry_date: todayISO(),
            },
            readonly: ['code', 'name'],
          });
        }
      }
    }
```

- [ ] **Step 5: renderPlan 里加 Worker 降级提示**

找到 `renderPlan` 末尾的 `el.innerHTML = html;`，替换为：

```javascript
  if (!WORKER_OK) {
    html = `<div class="alert alert-warning py-2 small mb-2">
      ⚠ 写入服务未部署，无法建仓。请在本地终端运行
      <code>bash scripts/deploy-worker.sh</code>
    </div>` + html;
  }
  el.innerHTML = html;
```

- [ ] **Step 6: 语法检查 + 跑测试**

```bash
node --check static/js/decide.js && echo "语法 OK"
python3 -m pytest tests/test_decide_web.py -q 2>&1 | tail -4
python3 -m pytest tests/ -q 2>&1 | tail -2
```

Expected: `语法 OK` + all PASS

- [ ] **Step 7: 提交**

```bash
git add static/js/decide.js tests/test_decide_web.py
git commit -m "feat(decide): 全部建仓（一次确认 + allSettled 批量 + 结果汇总）+ Worker 降级提示"
```

---

### Task 5: 导出、部署与端到端验证

**Files:** None（验证）

- [ ] **Step 1: 全量测试**

```bash
cd /Users/shichaopeng/Work/self-dir/projects/a-finder-cli
python3 -m pytest tests/ -q 2>&1 | tail -2
node --test tests/test_worker_auth.mjs 2>&1 | grep -E "^# (pass|fail)"
```

Expected: pytest all PASS + node `# pass 9` / `# fail 0`

- [ ] **Step 2: 导出并检查产物**

```bash
rm -rf /tmp/site-1c
python3 export_json.py --db hs300.db --out /tmp/site-1c 2>&1 | tail -1
grep -c "<th>操作</th>" /tmp/site-1c/static/js/decide.js
grep -c "全部建仓" /tmp/site-1c/static/js/decide.js
grep -c "deploy-worker.sh" /tmp/site-1c/static/js/decide.js
```

Expected: 三个计数都 ≥ 1

- [ ] **Step 3: 部署 gh-pages**

```bash
cd /Users/shichaopeng/Work/self-dir/projects/a-finder-cli
git worktree prune; rm -rf /tmp/ghpp3
git worktree add /tmp/ghpp3 gh-pages
rm -rf /tmp/ghpp3/*
cp -R /tmp/site-1c/* /tmp/ghpp3/
cd /tmp/ghpp3 && git add -A && git commit -m "site: 决策台一键建仓" && git push origin gh-pages
cd /Users/shichaopeng/Work/self-dir/projects/a-finder-cli
git worktree remove /tmp/ghpp3 --force
```

- [ ] **Step 4: 线上验证（Worker 未部署状态）**

```bash
sleep 35
printf "decide.js 含操作列: "
curl -sL "https://ycu.space/static/js/decide.js?r=$(date +%s)" | grep -c "th>操作"
printf "含全部建仓: "
curl -sL "https://ycu.space/static/js/decide.js?r=$(date +%s)" | grep -c "全部建仓"
printf "含降级提示: "
curl -sL "https://ycu.space/static/js/decide.js?r=$(date +%s)" | grep -c "deploy-worker.sh"
```

Expected: 三个都 ≥ 1

- [ ] **Step 5: 浏览器预期行为（Worker 未部署时）**

打开 https://ycu.space/decide.html 强制刷新：
1. 待建仓清单有「操作」列，每行按钮为**禁用**态（`title="写入服务未部署"`）
2. 顶部汇总条右侧「全部建仓 (8)」**禁用**
3. 清单上方出现黄条「⚠ 写入服务未部署，无法建仓…deploy-worker.sh」

- [ ] **Step 6: Worker 部署后的完整验证（待用户执行 deploy-worker.sh 后）**

1. 登录 → 行内「建仓」→ 表单预填（代码/名称只读，股数/成本价可改，金额联动）
2. 改成本价 → 提交 → 该行变「已持有」，摘要卡持仓数 +1
3. 点「全部建仓 (N)」→ 确认 Modal 列出 N 只 + 合计 → 确认
4. 顶部显示「已建仓 N 只，合计 ¥X」→ 清单全部变「已持有」

---

## Self-Review

**Spec coverage:**
- §三 交互与状态（POSITIONS/WORKER_OK/已持有判定）→ Task 1
- §三 清单「操作」列 + 全部建仓按钮 → Task 2
- §四 单只建仓表单（预填/只读/校验/金额联动/移除旧 modal）→ Task 3
- §五 全部建仓（确认 Modal + allSettled + 结果汇总）→ Task 4
- §六 Worker 降级（禁用 + 提示）→ Task 2/4
- §七 文件清单（零后端改动）→ 全 Task 只改 decide.js + 测试
- §八 测试策略（7 类）→ Task 1/2/3/4 各 4-5 个测试
- §九 验证计划 → Task 5

**Placeholder scan:** 无 TBD/TODO。所有代码块完整。

**Type consistency:**
- `POSITIONS` / `WORKER_OK` / `PENDING_PLAN_ROW` / `LAST_PLAN_ROWS` → Task 1/3 定义，Task 2/3/4 使用一致
- `heldCodeSet()` / `isHeldRow(row, set)` / `openButton(row, set, workerReady)` / `todayISO()` → Task 1/2 定义与使用一致
- `openPositionForm({ isCb, prefill, readonly })` → Task 3 定义，调用点传参与之一致
- `openAllPositions()` → Task 4 定义，Task 4 Step 4 绑定一致
- `PENDING_PLAN_ROW` 支持 `'__ALL__'` 哨兵 → Task 4 Step 4 同时处理单只与全部
- `postDecidePosition(payload)` 字段 `code/name/is_cb/shares/entry_price/stop_price/entry_date` → 与 Worker 校验一致
