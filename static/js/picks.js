// 每日机会页入口

import { fetchPicks, fetchDates, fetchStats, postRefresh, pollJob } from './api.js?v=fa665a3';
import { $, fillDateSelect, setStatus, setProgress, setLog, showBoardLoading, whenBoardReady, esc, fmt } from './dom.js?v=fa665a3';
import { setDateFilterState } from './date-filter.js?v=fa665a3';
import { renderPicksBoard } from './table.js?v=fa665a3';
import { startDashboard } from './dashboard.js?v=fa665a3';

const PICKS_COLS = [
  { key: 'code',     label: '代码', type: 'str',  cls: '' },
  { key: 'name',     label: '名称', type: 'str',  cls: '' },
  { key: 'strategy', label: '策略', type: 'str',  cls: '' },
  { key: 'buy',      label: '买入', type: 'num',  cls: 'num' },
  { key: 'stop',     label: '止损', type: 'num',  cls: 'num' },
  { key: 'target',   label: '目标', type: 'num',  cls: 'num' },
  { key: 'ret_pct',  label: '涨跌%', type: 'num', cls: 'num' },
  { key: 'score',    label: '评分', type: 'num',  cls: 'num' },
];

const state = { data: null, sort: { key: 'score', dir: -1 }, filter: '' };

function drawBoard() {
  const groups = (state.data && state.data.groups) || {};
  const board = $('board');
  if (!board) return;
  renderPicksBoard(board, groups, PICKS_COLS, state.sort, state.filter);
}

function drawStats(d) {
  const el = $('stats');
  if (!el) return;
  let html = '';
  const rows = d.strategies || [];
  if (rows.length) {
    html += '<div class="table-responsive app-card d-none d-md-block mb-3"><table class="app-table"><thead><tr>' +
      '<th>策略</th><th class="num">样本</th><th class="num">胜率%</th><th class="num">平均收益%</th></tr></thead><tbody>';
    rows.forEach(r => {
      html += `<tr><td>${esc(r.strategy)}</td><td class="num">${r.n}</td>` +
        `<td class="num">${r.win_rate}</td>` +
        `<td class="num ${r.expectancy >= 0 ? 'text-success' : 'text-danger'}">${r.expectancy >= 0 ? '+' : ''}${r.expectancy}</td></tr>`;
    });
    html += '</tbody></table></div>';
  }
  const mo = d.monthly || [];
  if (mo.length) {
    html += '<h2 class="section-title">月度胜率</h2>';
    html += '<div class="table-responsive app-card"><table class="app-table"><thead><tr>' +
      '<th>月份</th><th class="num">样本</th><th class="num">胜率%</th><th class="num">平均收益%</th></tr></thead><tbody>';
    mo.forEach(m => {
      html += `<tr><td>${esc(m.month)}</td><td class="num">${m.n}</td>` +
        `<td class="num">${m.win_rate}</td>` +
        `<td class="num ${m.avg_ret >= 0 ? 'text-success' : 'text-danger'}">${m.avg_ret >= 0 ? '+' : ''}${m.avg_ret}</td></tr>`;
    });
    html += '</tbody></table></div>';
  }
  el.innerHTML = html || '<div class="empty-state">暂无历史标注样本</div>';
}

function render(date) {
  showBoardLoading();
  fetchPicks(date).then(data => {
    whenBoardReady(() => {
      state.data = data;
      state.sort = { key: 'score', dir: -1 };
      drawBoard();
    });
  }).catch(() => {
    const board = $('board');
    if (board) board.innerHTML = '<div class="empty-state">数据加载失败</div>';
  });
}

function loadDates() {
  fetchDates().then(d => {
    const v = fillDateSelect(d.dates);
    setDateFilterState('d', '', '');
    if (v) render(v);
    else {
      const board = $('board');
      if (board) board.innerHTML = '<div class="empty-state">暂无选股数据</div>';
    }
  }).catch(() => {
    setDateFilterState('d', 'error', '日期加载失败');
    const board = $('board');
    if (board) board.innerHTML = '<div class="empty-state">日期加载失败</div>';
  });
}

function refresh(sync) {
  const btnRecalc = $('btn-recalc');
  const btnSync = $('btn-sync');
  if (btnRecalc) btnRecalc.disabled = true;
  if (btnSync) btnSync.disabled = true;
  setStatus('任务已提交…', 'alert-info');

  postRefresh(sync).then(data => {
    pollJob(data.job_id,
      (d) => { setProgress(d.progress); setLog(d.log); setStatus('任务进行中…', 'alert-info'); },
      (d) => {
        setStatus(d.message, 'alert-success');
        if (btnRecalc) btnRecalc.disabled = false;
        if (btnSync) btnSync.disabled = false;
        if (window.refreshDashboard) window.refreshDashboard();
        loadDates();
      },
      (d) => {
        setStatus('任务失败：' + (d.message || '未知错误'), 'alert-danger');
        if (btnRecalc) btnRecalc.disabled = false;
        if (btnSync) btnSync.disabled = false;
      }
    );
  }).catch(err => {
    if (err.status === 409) setStatus('已有任务进行中', 'alert-warning');
    else setStatus('提交失败', 'alert-danger');
    if (btnRecalc) btnRecalc.disabled = false;
    if (btnSync) btnSync.disabled = false;
  });
}

// Init
document.addEventListener('DOMContentLoaded', () => {
  const dateSelect = $('d');
  if (dateSelect) dateSelect.addEventListener('change', () => render(dateSelect.value));

  const searchInput = $('q');
  if (searchInput) searchInput.addEventListener('input', () => {
    state.filter = searchInput.value.trim();
    drawBoard();
  });

  const btnRecalc = $('btn-recalc');
  if (btnRecalc) btnRecalc.addEventListener('click', () => refresh(false));

  const btnSync = $('btn-sync');
  if (btnSync) btnSync.addEventListener('click', () => refresh(true));

  const board = $('board');
  if (board) board.addEventListener('click', e => {
    const th = e.target.closest('th.sortable');
    if (!th) return;
    const key = th.dataset.sort;
    if (state.sort.key === key) {
      state.sort.dir = -state.sort.dir;
    } else {
      state.sort.key = key;
      state.sort.dir = -1;
    }
    drawBoard();
  });

  startDashboard();
  loadDates();
  fetchStats().then(drawStats).catch(() => {});
});
