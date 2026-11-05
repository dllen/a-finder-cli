// DOM helpers (replaces the jQuery common.js)

export function $(id) { return document.getElementById(id); }

export function fmt(v) { return v == null ? '无' : Number(v).toFixed(2); }

export function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]
  );
}

export function fillDateSelect(dates, selectId = 'd') {
  const sel = $(selectId);
  if (!sel) return null;
  const prev = sel.value;
  sel.innerHTML = '';
  (dates || []).forEach(dt => {
    sel.appendChild(new Option(dt, dt));
  });
  if (dates && dates.length) {
    sel.value = (prev && dates.includes(prev)) ? prev : dates[0];
  }
  return sel.value;
}

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

export function setProgress(pct) {
  const el = $('prog');
  if (!el) return;
  if (pct == null) { el.innerHTML = ''; return; }
  const width = Math.max(0, Math.min(100, pct));
  const label = pct >= 100 ? '完成' : pct + '%';
  el.innerHTML =
    '<div class="meter">' +
    '<div class="meter__track">' +
    `<span class="meter__fill" style="width:${width}%"></span>` +
    '</div>' +
    `<span class="meter__label">${label}</span>` +
    '</div>';
}

export function setLog(lines) {
  const el = $('log');
  if (!el) return;
  if (!lines || !lines.length) { el.innerHTML = ''; return; }
  const items = lines.map(l => `<div class="log-line">${l}</div>`).join('');
  el.innerHTML = `<div class="logpanel"><div class="log-scroll">${items}</div></div>`;
}

let __loadingSince = 0;

export function showBoardLoading() {
  __loadingSince = Date.now();
  let rows = '';
  for (let i = 0; i < 5; i++) {
    rows += '<div class="skeleton skeleton-row"></div>';
  }
  const board = $('board');
  if (board) {
    board.innerHTML =
      `<div class="skeleton-panel" aria-busy="true" aria-label="加载中">${rows}</div>`;
  }
}

export function whenBoardReady(fn, minMs) {
  const elapsed = Date.now() - __loadingSince;
  const wait = Math.max(0, (minMs || 300) - elapsed);
  setTimeout(fn, wait);
}
