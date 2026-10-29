// 决策台持仓导出 CSV：纯函数 + 下载封装。
// 为什么放前端：持仓真相源在 D1（Worker），前端已经有一份数据，导出无需新增后端接口。

const HEADERS = ['code', 'name', 'shares', 'entry_price', 'stop_price', 'entry_date', 'note', 'updated_at'];

/** CSV 单元格转义：包含逗号/引号/换行时用双引号包裹，内部引号翻倍。 */
export function csvCell(value) {
  if (value === null || value === undefined) return '';
  const s = String(value);
  if (/[",\n\r]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
}

/**
 * 把持仓行转成 CSV 文本。
 * @param {Array<object>} stockRows   real_positions
 * @param {Array<object>} cbRows      real_cb_positions
 * @returns {string} 带 BOM 的 CSV（Excel 打开中文不乱码）
 */
export function toCsv(stockRows = [], cbRows = []) {
  const lines = [];
  lines.push(['kind', ...HEADERS].map(csvCell).join(','));
  for (const r of stockRows) {
    lines.push(['stock', ...HEADERS.map((h) => r[h])].map(csvCell).join(','));
  }
  for (const r of cbRows) {
    lines.push(['cb', ...HEADERS.map((h) => r[h])].map(csvCell).join(','));
  }
  return '\ufeff' + lines.join('\r\n') + '\r\n';
}

/** 触发浏览器下载。 */
export function downloadCsv(filename, csvText) {
  const blob = new Blob([csvText], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

export function exportPositionsCsv(stockRows, cbRows, today) {
  const stamp = today || new Date().toISOString().slice(0, 10);
  downloadCsv(`positions-${stamp}.csv`, toCsv(stockRows, cbRows));
}
