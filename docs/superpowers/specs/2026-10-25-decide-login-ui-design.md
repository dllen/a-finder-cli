# 决策台登录改造 + 整页优化 — 设计文档

**日期**：2026-10-25
**状态**：设计已确认（方案 A，三节逐节通过）

---

## 一、目标

1. **登录改为内嵌表单**：用户名 + 密码，用 Bootstrap Modal，**不用浏览器原生弹窗**（`prompt()`）
2. **整页优化**：修复错误提示静默失效、消除内容重复、响应式与骨架屏

**决策（用户确认）**：
- UI 范围：**登录区 + 整页优化**（不含大规模视觉重构）
- 用户名角色：**双因子校验**（用户名与密码都要对；单用户，持仓不隔离）
- 登录形式：**Bootstrap Modal**

**非目标**：
- ❌ 不做多用户隔离（D1 表不加 user 字段）
- ❌ 不做配色/图标/动效的全面视觉升级
- ❌ 不做"记住我"、找回密码、注册
- ❌ 不加登录速率限制（免费额度下可选，本次不做）

---

## 二、现状与问题

| 项 | 现状 |
|---|---|
| 登录方式 | `prompt('输入决策台密码')` — 浏览器原生弹窗 |
| 认证字段 | Worker 只校验 `DECIDE_PASSWORD`，**无用户名** |
| 错误提示 | **完全静默失效**：`decide.html` 没有 `#status`，`setStatus()` 直接 return |
| 内容重复 | 空仓时 `#decide-plan` 与 `#decide-comparison` 各列一遍同样 8 只票 |
| 摘要卡 | `row-cols-2 row-cols-md-5`，移动端偏挤 |
| 加载态 | 无骨架屏，切日期白屏 |

**问题清单与严重度**：

| # | 问题 | 严重度 |
|---|---|---|
| 1 | 错误提示完全不显示（缺 `#status`） | 🔴 严重 |
| 2 | 登录用 `prompt()` 原生弹窗 | 🟡 待改 |
| 3 | 「今日执行清单」与「待建仓清单」内容重复 | 🟡 体验 |
| 4 | 5 个摘要卡移动端拥挤 | 🟢 视觉 |
| 5 | 无加载骨架屏 | 🟢 视觉 |
| 6 | 登录态用整条 alert，视觉权重过高 | 🟢 视觉 |

---

## 三、方案（方案 A：结构性优化）

修 bug + 去重 + 响应式 + Modal 登录，不追求视觉升级。

---

## 四、信息架构重排（消除重复）

### 4.1 新结构

```
标题 · 日期选择 · 刷新
[#decide-auth]        ← 精简单行：未登录「只读模式 [登录]」/ 已登录「👤 用户名 [登出]」
[#decide-status]      ← 新增：错误/成功提示（修复静默失效）
[#decide-summary]     ← 5 卡，响应式

┌─ 空仓（actual_count === 0）──────────────────────┐
│ 【待建仓清单】= #decide-plan                      │
│   排名 代码 名称 计划价 股数 金额 止损 止盈        │
│   ────────────────────────────────                │
│   合计 ¥69,926 / 股票仓 ¥80,000 / 剩余 ¥10,074    │
│   ⚠ 跳过 2 只（一手超单票上限）                    │
│ #decide-comparison = 隐藏（避免重复）              │
└──────────────────────────────────────────────────┘

┌─ 有持仓（actual_count > 0）──────────────────────┐
│ 【今日执行清单】= #decide-plan（仅未持有的买入建议）│
│ 【执行对照】= #decide-comparison（偏差列表）        │
└──────────────────────────────────────────────────┘

我的实际持仓 #decide-positions
我的转债持仓 #decide-cb-positions
```

### 4.2 职责划分

| 元素 | 空仓 | 有持仓 |
|---|---|---|
| `#decide-plan` | 完整待建仓清单（含合计/跳过提示） | 执行清单（表格） |
| `#decide-comparison` | **隐藏** | 偏差列表 |
| `#decide-summary` | 第 5 卡显示「待建仓 N」 | 第 5 卡显示「偏差 N」 |

**关键决策**：空仓/有持仓的分支判定集中在 `renderAll(cmp, plan)` 一个函数里，
`renderPlan` / `renderComparison` 只负责渲染，不各自判断。这样"内容只出现一次"
是结构保证，不靠分散的 if。

---

## 五、登录 Modal

### 5.1 前端（`templates/components/decide_login_modal.html`）

```html
<div class="modal fade" id="login-modal" tabindex="-1" aria-hidden="true">
  <div class="modal-dialog modal-dialog-centered modal-sm">
    <div class="modal-content">
      <form id="login-form">
        <div class="modal-header">
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

**交互细节**：
- 打开时自动聚焦用户名框
- `<form>` + `type=submit` → 支持 **Enter 提交**
- 提交中按钮禁用 + 文案「登录中…」，防重复点击
- 失败时**行内红字**（不 alert、不清空用户名、密码清空）
- 成功 → 关闭 Modal → 刷新数据 → 顶部变「👤 用户名」
- **错误信息不区分**"用户名错"还是"密码错"（统一「用户名或密码错误」，避免探测）

### 5.2 后端（`worker/index.js`）

```javascript
if (path === '/api/decide/login' && method === 'POST') {
  const body = await request.json().catch(() => ({}));
  const userOk = env.DECIDE_USER && body.user === env.DECIDE_USER;
  const passOk = env.DECIDE_PASSWORD && body.password === env.DECIDE_PASSWORD;
  if (!userOk || !passOk) {
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

`/api/decide/health` 返回当前用户名，让刷新后仍能显示：

```javascript
if (path === '/api/decide/health' && method === 'GET') {
  const payload = await readAuth(request, env);
  let d1 = 'ok';
  try { await env.DB.prepare('SELECT 1').first(); }
  catch (e) { d1 = 'error: ' + (e && e.message ? e.message : e); }
  return json({
    ok: d1 === 'ok', d1,
    authed: payload !== null,
    user: payload ? payload.user : null,
  }, 200, origin);
}
```

`worker/auth.js` 新增 `readAuth()`（返回 payload 或 null），`isAuthed()` 改为复用它。

### 5.3 部署影响

Worker **尚未部署过**，新增 `DECIDE_USER` secret 不涉及迁移，只需多跑一条：
`npx wrangler secret put DECIDE_USER`（`scripts/deploy-worker.sh` 里对应加一步）。

---

## 六、错误提示 / 骨架屏 / 响应式

### 6.1 修复静默失效

`decide.html` 新增 `<div id="decide-status" class="mb-3"></div>`；
`dom.setStatus()` 支持自定义容器（保持向后兼容）：

```javascript
export function setStatus(html, cls, containerId = 'status') {
  const el = $(containerId);
  if (!el) return;
  el.innerHTML = html ? `<div class="alert ${cls}" role="status">${html}</div>` : '';
  if (html) setTimeout(() => {
    if (el.textContent.includes(String(html).replace(/<[^>]+>/g, ''))) el.innerHTML = '';
  }, 6000);
}
```

decide.js 中所有 `setStatus(...)` 改为 `setStatus(..., 'decide-status')`。
其它页面（picks/plan/cb）都有 `#status`，行为不变。

### 6.2 骨架屏

切日期/首屏时先渲染骨架，避免白屏：

```javascript
function showLoading() {
  ['decide-summary', 'decide-plan', 'decide-comparison', 'decide-positions'].forEach(id => {
    const el = $(id);
    if (el) el.innerHTML = '<div class="app-card skeleton-table" aria-busy="true">' +
      '<div class="skeleton skeleton-row"></div>'.repeat(3) + '</div>';
  });
}
```

### 6.3 响应式

| 元素 | 现在 | 改为 |
|---|---|---|
| 摘要卡 | `row-cols-2 row-cols-md-5` | `row-cols-2 row-cols-md-3 row-cols-xl-5` |
| 登录条 | 整条 alert | 单行 flex：左文本 + 右按钮，`small` 字号 |
| 表格 | 已有 `d-none d-md-block` + 移动卡片 | 保持 |

---

## 七、文件清单

| 文件 | 类型 | 改动 |
|---|---|---|
| `templates/components/decide_login_modal.html` | 新建 | 登录 Modal（用户名+密码+行内错误） |
| `templates/decide.html` | 修改 | 加 `#decide-status`、include 登录 Modal、摘要卡响应式类 |
| `static/js/decide.js` | 修改 | Modal 登录替换 `prompt()`；`renderAll` 统一空仓/有持仓分支；骨架屏；`setStatus` 指定容器 |
| `static/js/api.js` | 修改 | `loginDecide(user, password)` 签名变更 |
| `static/js/dom.js` | 修改 | `setStatus` 支持 `containerId` + 自动淡出 |
| `worker/index.js` | 修改 | 登录双因子校验；`/health` 返回 `user` |
| `worker/auth.js` | 修改 | 新增 `readAuth()`，`isAuthed()` 复用之 |
| `scripts/deploy-worker.sh` | 修改 | 加 `DECIDE_USER` secret 步骤 |
| `tests/test_decide_web.py` | 修改 | 登录 UI / 双因子 / 状态区测试 |
| `tests/test_worker_auth.mjs` | 修改 | `readAuth` 单测 |

---

## 八、测试策略

| 测试 | 验证点 |
|---|---|
| `test_decide_html_has_status` | `#decide-status` 存在（防回归） |
| `test_decide_html_has_login_modal` | Modal 含 `login-user` / `login-pass` / `login-error` |
| `test_login_js_no_prompt` | `decide.js` 不含 `prompt(`（禁用原生弹窗） |
| `test_login_js_passes_username` | `loginDecide` 调用传两个参数 |
| `test_worker_login_requires_both` | node 单测：只对密码 / 只对用户名 / 都对 / 都错 四种情况 |
| `test_health_returns_user` | node 单测：登录后 `/health` 返回 `user` |
| `test_setstatus_container` | `dom.setStatus(html, cls, 'decide-status')` 落到正确容器 |
| 回归 | 全量 pytest + 现有 6 个 parity 测试 |

---

## 九、验证计划

1. 本地 `npx wrangler dev` 起 Worker（若可行）或直接 node 单测 `auth.js`
2. 静态导出后浏览器验证：点登录 → Modal 弹出 → 输错有红字 → 输对关闭并显示用户名
3. 空仓时确认「待建仓清单」只出现一次
4. 断网 / Worker 未部署时确认错误提示可见（不再静默）
5. 全量 pytest 通过

---

## 十、风险控制

| 风险 | 缓解 |
|---|---|
| `prompt()` 被其它地方残留 | 测试断言 `decide.js` 不含 `prompt(` |
| `setStatus` 改签名破坏其它页面 | 默认参数 `'status'` 保持向后兼容；跑全量测试 |
| `renderAll` 重构引入渲染顺序 bug | 空仓/有持仓两条路径都加测试 |
| 双因子校验上线后旧 Cookie 失效 | token payload 加了 `user`，旧 token 无该字段但验签仍通过；UI 显示「已登录」即可（不强制 user 存在） |
| Worker 未部署时登录必然失败 | Modal 内明确提示"无法连接服务器"，并说明需先部署 Worker |

---

## 十一、评审 / 实施

- 决策已确认：登录区+整页优化 / 双因子校验 / Bootstrap Modal / 方案 A
- 设计三节（信息架构 / 登录 Modal / 错误提示+骨架屏+响应式）逐节获用户确认
- 接着调 writing-plans 拆实施计划
