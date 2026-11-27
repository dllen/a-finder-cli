/* 每日简报 · 时间流渲染
 * 读 briefing JSON（LLM 叙事版优先，数据兜底版降级），渲染时间轴 DOM。
 */
import { fetchBriefing, fetchBriefingDates, fetchBriefingInput } from "./api.js";

function $(id) { return document.getElementById(id); }

function fmt(v, n) { return Number(v).toFixed(n == null ? 1 : n); }
function signed(v, n) { const x = Number(v); return (x > 0 ? "+" : "") + fmt(x, n); }
function pctClass(v) { return v > 0 ? "etf-up" : v < 0 ? "etf-down" : "etf-flat"; }

const WEEKDAYS = ["日", "一", "二", "三", "四", "五", "六"];

function renderTimeline(data, isFallback) {
  const host = $("briefing-timeline");
  if (!host) return;

  let html = '<h2 class="briefing-timeline__title">当日盘中节奏</h2>';

  // LLM version has intraday_nodes array
  const nodes = data.intraday_nodes || [];
  if (nodes.length) {
    for (const node of nodes) {
      html += `<div class="tl-node">
        <span class="tl-dot"></span>
        <time class="tl-time">${node.time || ""}</time>
        <p class="tl-text">${node.text || ""}</p>
      </div>`;
    }
  } else if (isFallback && data.intraday) {
    // Data-only fallback: generate rule-based nodes from structured data
    const intra = data.intraday;
    const peak = intra.peak_minute || {};
    const hourly = intra.hourly_flow || [];

    if (hourly.length) {
      const first = hourly[0];
      html += `<div class="tl-node"><span class="tl-dot"></span>
        <time class="tl-time">${first.hour.split("-")[0]}</time>
        <p class="tl-text tl-text--fallback">开盘首小时成交 <span class="tl-data">${fmt(first.amount_yi)} 亿</span></p>
      </div>`;
    }
    if (peak.time) {
      html += `<div class="tl-node"><span class="tl-dot"></span>
        <time class="tl-time">${peak.time}</time>
        <p class="tl-text tl-text--fallback">全天峰值 <span class="tl-data">${fmt(peak.amount_yi)} 亿/分钟</span></p>
      </div>`;
    }
    html += `<div class="tl-node"><span class="tl-dot"></span>
      <time class="tl-time">15:00</time>
      <p class="tl-text tl-text--fallback">收盘，全日成交 <span class="tl-data">${fmt(intra.total_amount_yi)} 亿</span>，净${intra.total_netflow_yi >= 0 ? "申购" : "赎回"} <span class="tl-data">${fmt(Math.abs(intra.total_netflow_yi))} 亿</span></p>
    </div>`;
  } else {
    html += '<p class="briefing-empty">暂无盘中数据</p>';
  }

  host.innerHTML = html;
}

function renderEvents(data) {
  const host = $("briefing-events");
  if (!host) return;
  const events = data.events || data.recent_events || [];
  if (!events.length) { host.innerHTML = ""; return; }

  let html = '<h2 class="briefing-events__title">近期事件回顾</h2>';
  for (const e of events) {
    html += `<div class="event-item">
      <span class="event-date">${(e.date || "").slice(5)}</span>
      <span class="event-type">${e.type || ""}</span>
      <span class="event-detail">${e.detail || e.text || ""}</span>
    </div>`;
  }
  host.innerHTML = html;
}

function renderPicks(data) {
  const host = $("briefing-picks");
  if (!host) return;
  const highlights = data.picks_highlight || [];
  const picks = data.picks || {};
  const items = highlights.length ? highlights : (picks.buy_signals || []).slice(0, 3);
  if (!items.length) { host.innerHTML = ""; return; }

  let html = '<h2 class="briefing-picks__title">今日选股亮点</h2><div class="pick-cards">';
  for (const p of items) {
    const pct = p.day_pct != null ? p.day_pct : 0;
    html += `<div class="pick-card">
      <div class="pick-card__name">${p.name || ""}</div>
      <div class="pick-card__code">${p.code || ""}</div>
      <div class="pick-card__strategy">${p.strategy || p.reason || ""}</div>
      <div class="pick-card__pct ${pctClass(pct)}">${signed(pct, 2)}%</div>
    </div>`;
  }
  html += "</div>";
  host.innerHTML = html;
}

function renderCb(data) {
  const host = $("briefing-cb");
  if (!host) return;
  const cb = data.cb || {};
  const count = cb.alerts_count || 0;
  if (!count) { host.innerHTML = ""; return; }
  host.innerHTML = `<span class="cb-badge">⚠ ${count} 只转债触发退出预警</span>`;
}

async function loadBriefing(date) {
  try {
    let data;
    try {
      data = await fetchBriefing(date);
    } catch (e) {
      // LLM version not available — try input fallback
      data = await fetchBriefingInput();
      if (data && data.date === date) {
        data._fallback = true;
      } else {
        throw e;
      }
    }
    const isFallback = data._fallback === true;
    const dateEl = $("briefingDate");
    const verEl = $("briefingVersion");
    if (dateEl) {
      const d = new Date(date + "T00:00:00");
      dateEl.textContent = date + "（周" + WEEKDAYS[d.getDay()] + "）";
    }
    if (verEl) {
      verEl.textContent = isFallback ? "数据版" : "LLM 叙事版";
      verEl.className = "etf-tag" + (isFallback ? "" : " etf-tag--ok");
    }
    renderTimeline(data, isFallback);
    renderEvents(data);
    renderPicks(data);
    renderCb(data);
  } catch (err) {
    const host = $("briefing-timeline");
    if (host) host.innerHTML = '<p class="briefing-empty">该日期暂无简报数据</p>';
  }
}

async function init() {
  try {
    const datesResp = await fetchBriefingDates();
    const dates = datesResp.dates || [];
    if (dates.length) {
      await loadBriefing(dates[0]);
    } else {
      // Try input directly
      const input = await fetchBriefingInput();
      if (input && input.date) {
        input._fallback = true;
        const dateEl = $("briefingDate");
        if (dateEl) dateEl.textContent = input.date;
        const verEl = $("briefingVersion");
        if (verEl) { verEl.textContent = "数据版"; verEl.className = "etf-tag"; }
        renderTimeline(input, true);
        renderEvents(input);
        renderPicks(input);
        renderCb(input);
      } else {
        const host = $("briefing-timeline");
        if (host) host.innerHTML = '<p class="briefing-empty">暂无简报数据，请先执行一次同步</p>';
      }
    }
  } catch (err) {
    const host = $("briefing-timeline");
    if (host) host.innerHTML = '<p class="briefing-empty">加载失败：' + (err.message || err) + '</p>';
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
