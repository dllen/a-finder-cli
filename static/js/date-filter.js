// 日期筛选组件增强：前后交易日步进 + URL 状态（?d=）+ 键盘方向键 + 加载/错误/成功态。
// 不接管 select 的填充逻辑——各页面（picks/plan/cb/decide）照旧填选项，
// 这里用 MutationObserver 侦测选项变化后同步控件状态，因此无需改动各页面的取数代码。

const URL_PARAM = 'd';
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

function readUrlDate() {
  try {
    const v = new URLSearchParams(window.location.search).get(URL_PARAM);
    return v && DATE_RE.test(v) ? v : null;
  } catch {
    return null;
  }
}

function writeUrlDate(date) {
  try {
    const url = new URL(window.location.href);
    if (date) url.searchParams.set(URL_PARAM, date);
    else url.searchParams.delete(URL_PARAM);
    window.history.replaceState(null, '', url);
  } catch {
    /* file:// 或受限环境下忽略 */
  }
}

function optionsOf(sel) {
  return Array.from(sel.options).map(o => o.value);
}

/** 同步步进按钮可用性、“回到最新”显隐。 */
function syncControls(wrap, sel) {
  const values = optionsOf(sel);
  const idx = values.indexOf(sel.value);
  // 选项按新→旧排列：index 0 是最新。data-step=-1（‹ 前一交易日=更早）→ index+1；
  // data-step=1（› 后一交易日=更新）→ index-1。
  const olderBtn = wrap.querySelector('[data-step="-1"]');
  const newerBtn = wrap.querySelector('[data-step="1"]');
  const latest = wrap.querySelector('.date-filter__latest');
  if (olderBtn) olderBtn.disabled = idx < 0 || idx >= values.length - 1;
  if (newerBtn) newerBtn.disabled = idx <= 0;
  if (latest) latest.hidden = !(idx > 0);
}

/** 设置组件状态：loading | error | success | ''（清空）。msg 可选，显示在 status 区。 */
export function setDateFilterState(selectId, state, msg) {
  const sel = document.getElementById(selectId);
  const wrap = sel && sel.closest('[data-date-filter]');
  if (!wrap) return;
  wrap.classList.remove('is-loading', 'is-error', 'is-success');
  if (state) wrap.classList.add('is-' + state);
  const status = wrap.querySelector('.date-filter__status');
  if (status) status.textContent = msg || '';
  if (sel) sel.disabled = state === 'loading';
}

/** 按"时间方向"步进：n = -1 更早，n = +1 更新。选项是倒序（新→旧），索引方向相反。 */
function stepBy(wrap, sel, n) {
  const values = optionsOf(sel);
  const idx = values.indexOf(sel.value);
  if (idx < 0) return;
  const target = Math.min(Math.max(idx - n, 0), values.length - 1);
  if (target === idx) return;
  sel.value = values[target];
  sel.dispatchEvent(new Event('change', { bubbles: true }));
}

/** 跳到最旧 / 最新 */
function goEdge(wrap, sel, edge) {
  const values = optionsOf(sel);
  if (!values.length) return;
  const target = edge === 'oldest' ? values[values.length - 1] : values[0];
  if (target === sel.value) return;
  sel.value = target;
  sel.dispatchEvent(new Event('change', { bubbles: true }));
}

function enhance(wrap) {
  const selectId = wrap.getAttribute('data-select-id');
  const sel = selectId ? document.getElementById(selectId) : wrap.querySelector('select');
  if (!sel || wrap.dataset.dfReady === '1') return;
  wrap.dataset.dfReady = '1';

  const wantFromUrl = readUrlDate();
  let restored = false;

  const apply = () => {
    syncControls(wrap, sel);
    // 选项就绪后：优先恢复 URL 指定的日期（仅一次）
    if (!restored && sel.options.length) {
      restored = true;
      wrap.classList.remove('is-loading');
      sel.disabled = false;
      if (wantFromUrl && optionsOf(sel).includes(wantFromUrl) && sel.value !== wantFromUrl) {
        sel.value = wantFromUrl;
        sel.dispatchEvent(new Event('change', { bubbles: true }));
        return;
      }
      if (wantFromUrl && !optionsOf(sel).includes(wantFromUrl)) {
        const status = wrap.querySelector('.date-filter__status');
        if (status) status.textContent = '该日期无数据';
      }
    }
  };

  // 初始：无选项即进入 loading
  if (!sel.options.length) setDateFilterState(selectId || sel.id, 'loading');

  wrap.addEventListener('click', (e) => {
    const step = e.target.closest('[data-step]');
    if (step && !step.disabled) { stepBy(wrap, sel, Number(step.dataset.step)); return; }
    const latest = e.target.closest('.date-filter__latest');
    if (latest) goEdge(wrap, sel, 'newest');
  });

  sel.addEventListener('change', () => {
    syncControls(wrap, sel);
    const values = optionsOf(sel);
    writeUrlDate(values.length && sel.value === values[0] ? null : sel.value);
    wrap.classList.remove('is-loading', 'is-error');
    if (wrap.classList.contains('is-success')) wrap.classList.remove('is-success');
  });

  // 方向键步进：焦点在 select 上时 ←/→ 切换交易日，Home/End 到两端
  sel.addEventListener('keydown', (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    // ←/PageUp 更早，→/PageDown 更新，Home 最新，End 最旧
    if (e.key === 'ArrowLeft' || e.key === 'PageUp') { e.preventDefault(); stepBy(wrap, sel, -1); }
    else if (e.key === 'ArrowRight' || e.key === 'PageDown') { e.preventDefault(); stepBy(wrap, sel, 1); }
    else if (e.key === 'Home') { e.preventDefault(); goEdge(wrap, sel, 'newest'); }
    else if (e.key === 'End') { e.preventDefault(); goEdge(wrap, sel, 'oldest'); }
  });

  // 选项由页面代码异步填充：观察后同步
  const mo = new MutationObserver(apply);
  mo.observe(sel, { childList: true });
  apply();
}

export function initDateFilters() {
  document.querySelectorAll('[data-date-filter]').forEach(enhance);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initDateFilters);
} else {
  initDateFilters();
}
