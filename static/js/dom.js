// DOM 辅助函数（替代 jQuery 的 common.js）

export function $(id) { return document.getElementById(id); }

export function fmt(v) { return v == null ? '—' : Number(v).toFixed(2); }

export function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]
  );
}

export function fillDateSelect(dates) {
  const sel = $('d');
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

export function setStatus(html, cls) {
  const el = $('status');
  if (!el) return;
  el.innerHTML = html ? `<div class="alert ${cls}" role="status">${html}</div>` : '';
}

export function setProgress(pct) {
  const el = $('prog');
  if (!el) return;
  if (pct == null) { el.innerHTML = ''; return; }
  const color = pct >= 100 ? 'bg-success' : 'bg-primary';
  const label = pct >= 100 ? '完成' : pct + '%';
  el.innerHTML =
    `<div class="progress" style="height:1.2rem">` +
    `<div class="progress-bar progress-bar-striped progress-bar-animated ${color}" ` +
    `role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100" ` +
    `style="width:${pct}%">${label}</div></div>`;
}

export function setLog(lines) {
  const el = $('log');
  if (!el) return;
  if (!lines || !lines.length) { el.innerHTML = ''; return; }
  const items = lines.map(l =>
    `<div class="border-bottom py-1 text-muted small">${l}</div>`
  ).join('');
  el.innerHTML = `<div class="card"><div class="card-body log-scroll">${items}</div></div>`;
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
      `<div class="app-card skeleton-table" aria-busy="true" aria-label="加载中">${rows}</div>`;
  }
}

export function whenBoardReady(fn, minMs) {
  const elapsed = Date.now() - __loadingSince;
  const wait = Math.max(0, (minMs || 300) - elapsed);
  setTimeout(fn, wait);
}
