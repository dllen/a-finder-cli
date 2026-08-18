// 共享 UI 辅助函数（fmt / setStatus / setProgress / setLog）。
// 依赖：jQuery（在页面底部先于本文件加载）。
function fmt(v) { return v == null ? '—' : Number(v).toFixed(2); }

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
