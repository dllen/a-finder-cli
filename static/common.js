// 共享 UI 辅助函数（fmt / setStatus / setProgress / setLog）。
// 依赖：jQuery（在页面底部先于本文件加载）。
function fmt(v) { return v == null ? '—' : Number(v).toFixed(2); }

// HTML 转义，用于把数据字段插入拼接的 HTML 字符串
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  });
}

// 用可选日期填充 #d 下拉框；保留当前选中（若仍在列表），否则选中最新（列表第一条）
function fillDateSelect(dates) {
  var sel = $('#d');
  var prev = sel.val();
  sel.empty();
  (dates || []).forEach(function (dt) { sel.append(new Option(dt, dt)); });
  if (dates && dates.length) {
    sel.val((prev && dates.indexOf(prev) >= 0) ? prev : dates[0]);
  }
  return sel.val();
}

function setStatus(html, cls) {
  $('#status').html(html ? '<div class="alert ' + cls + '" role="status">' + html + '</div>' : '');
}

function setProgress(pct) {
  if (pct == null) { $('#prog').empty(); return; }
  var color = pct >= 100 ? 'bg-success' : 'bg-primary';
  var label = pct >= 100 ? '完成' : pct + '%';
  $('#prog').html(
    '<div class="progress" style="height:1.2rem">' +
    '<div class="progress-bar progress-bar-striped progress-bar-animated ' + color +
    '" role="progressbar" aria-valuenow="' + pct + '" aria-valuemin="0" aria-valuemax="100" style="width:' + pct + '%">' + label + '</div></div>'
  );
}

function setLog(lines) {
  if (!lines || !lines.length) { $('#log').empty(); return; }
  var items = lines.map(function (l) {
    return '<div class="border-bottom py-1 text-muted small">' + l + '</div>';
  });
  $('#log').html('<div class="card"><div class="card-body log-scroll">' + items.join('') + '</div></div>');
}

var __loadingSince = 0;
function showBoardLoading() {
  __loadingSince = Date.now();
  var rows = '';
  for (var i = 0; i < 5; i++) {
    rows += '<div class="skeleton skeleton-row"></div>';
  }
  $('#board').html(
    '<div class="app-card skeleton-table" aria-busy="true" aria-label="加载中">' + rows + '</div>'
  );
}

// 保证骨架屏至少展示 minMs，避免本地响应太快一闪而过
function whenBoardReady(fn, minMs) {
  var elapsed = Date.now() - __loadingSince;
  var wait = Math.max(0, (minMs || 300) - elapsed);
  setTimeout(fn, wait);
}
