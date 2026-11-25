/* ETF 资金看板 · 前端渲染层
 *
 * 数据：来自 /api/etf/islands（或静态站 data/etf/islands.json），
 *       由 scripts/fetch_etf_dashboard.py 抓取自「重点 ETF 资金看板」。
 * 图表：ECharts（static/vendor/echarts.min.js，与项目其它页面同源）。
 *
 * 五个部分：
 *   ① 盘中分钟观测   —— 20只ETF分钟成交额 + 上证综指分时；单只ETF当日表现表
 *   ② 放量信号对比   —— 放量日 / 常态日 分钟成交额基准对比
 *   ③ 日度资金全景   —— 日度成交额、净流入、全市场宽基分项
 *   ④ ETF 两融资金   —— 融资余额与融资净买入
 *   ⑤ 主题分时       —— 主题ETF分钟成交额 + 主题指数分时
 *
 * ⚠️ 与原看板的差异：原看板的分钟线是**实时**的（轮询它的 /api/hq 代理）。
 * 本项目不引入那套代理，改用**静态快照**（抓取时刻的收盘数据），
 * 页面顶部标明「数据截至」，避免给出「看起来是实时、其实是昨天」的假象。
 */
import {
  fetchEtfIslands,
  fetchEtfThemes,
  fetchEtfCumDays,
  fetchEtfThemeCum,
  fetchEtfThemeMinutes,
  fetchEtfThemeIndices,
} from "./api.js";

const CHARTS = [];
let ECHARTS = null;

const COLORS = {
  up: "#e5484d",
  down: "#30a46c",
  accent: "#f5a524",
  cool: "#5b8def",
  violet: "#a78bfa",
  ink: "#c9d3e0",
  grid: "rgba(201,211,224,0.10)",
  axis: "#8b95a5",
};

/* ---------------------------------------------------------------- 工具 */

function $(id) {
  return document.getElementById(id);
}

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

function fmt(v, n) {
  return Number(v).toFixed(n == null ? 1 : n);
}

function signed(v, n) {
  const x = Number(v);
  return (x > 0 ? "+" : "") + fmt(x, n);
}

function pctClass(v) {
  return v > 0 ? "etf-up" : v < 0 ? "etf-down" : "etf-flat";
}

function setTag(id, text, kind) {
  const n = $(id);
  if (!n) return;
  n.textContent = text;
  n.className = "etf-tag" + (kind ? " etf-tag--" + kind : "");
}

function chart(id) {
  const node = $(id);
  if (!node || !ECHARTS) return null;
  const inst = ECHARTS.getInstanceByDom(node) || ECHARTS.init(node);
  CHARTS.push(inst);
  return inst;
}

/** 统一的暗色坐标系：轴线、分割线、tooltip 都按本项目暗底重配。 */
function darkAxis(extra) {
  return Object.assign(
    {
      axisLine: { lineStyle: { color: "rgba(201,211,224,0.18)" } },
      axisTick: { show: false },
      axisLabel: { color: COLORS.axis, fontSize: 11 },
      splitLine: { lineStyle: { color: COLORS.grid } },
    },
    extra || {}
  );
}

function darkTooltip(extra) {
  return Object.assign(
    {
      backgroundColor: "rgba(22,25,31,0.96)",
      borderColor: "rgba(201,211,224,0.20)",
      borderWidth: 1,
      textStyle: { color: "#e6e9ef", fontSize: 12 },
      confine: true,
    },
    extra || {}
  );
}

/** 全天 241 个分钟时点：上午 09:30 起 121 根 + 下午 13:01 起 120 根。 */
function fullAxis() {
  const ts = [];
  [["09:30", 121], ["13:01", 120]].forEach(([start, count]) => {
    let hh = parseInt(start.slice(0, 2), 10);
    let mm = parseInt(start.slice(3), 10);
    for (let i = 0; i < count; i++) {
      ts.push(("0" + hh).slice(-2) + ":" + ("0" + mm).slice(-2));
      mm += 1;
      if (mm === 60) { mm = 0; hh += 1; }
    }
  });
  return ts;
}
const FULL_T = fullAxis();

/** 把「按实际时间点」的数组对齐到全天 241 点；缺失时点留 null（线自然断开）。 */
function alignToFull(times, arr) {
  const map = {};
  (times || []).forEach((t, i) => { map[t] = i; });
  return FULL_T.map((t) => {
    const i = map[t];
    return i == null ? null : (arr ? arr[i] : null);
  });
}

function cumulative(arr) {
  let acc = 0;
  return (arr || []).map((v) => {
    acc += Number(v) || 0;
    return +acc.toFixed(2);
  });
}

/* ------------------------------------------------------------ 数据装载 */

let DATA = null;

async function loadData() {
  const res = await fetchEtfIslands();
  if (!res || !res.initData) throw new Error("数据为空");
  DATA = res;
  return res;
}

async function loadExtra() {
  const out = {};
  const jobs = [
    ["themes", fetchEtfThemes],
    ["cumdays", fetchEtfCumDays],
    ["theme-cum", fetchEtfThemeCum],
    ["theme-minutes", fetchEtfThemeMinutes],
    ["theme-indices", fetchEtfThemeIndices],
  ];
  await Promise.all(
    jobs.map(async ([name, fn]) => {
      try {
        out[name] = await fn();
      } catch (e) {
        out[name] = null;
      }
    })
  );
  return out;
}

/* -------------------------------------------------------- ① 分钟观测 */

const MINUTE = {
  bars: [],
  closes: [],
  times: [],
  cum: [],
  preClose: null,
  date: "",
};

function barAxisMax(bars) {
  /* 折中定标：按「剔除开盘 10 根竞价脉冲后的峰值」定档，
     但若全时段峰值超出该档位，则抬到能容纳它、且不超过 2.5 倍封顶。 */
  const STEPS = [10, 15, 20, 25, 30, 40, 50, 60, 80];
  const step = (p) => {
    for (const s of STEPS) if (s >= p) return s;
    return Math.ceil(p / 20) * 20;
  };
  let core = 0;
  let peak = 0;
  bars.forEach((v, i) => {
    const x = Number(v);
    if (!isFinite(x) || x <= 0) return;
    if (x > peak) peak = x;
    if (i >= 10 && x > core) core = x;
  });
  const base = Math.max(step(core), 10);
  if (peak > base) {
    const cap = step(base * 2.5);
    return Math.max(base, Math.min(step(peak), cap));
  }
  return base;
}

function sparkSvg(vals) {
  if (!vals || !vals.length) return "";
  const W = 104;
  const H = 24;
  const n = vals.length;
  let max = 0;
  vals.forEach((v) => { if (v != null && v > max) max = v; });
  if (!(max > 0)) return "";
  const bw = W / n;
  let rects = "";
  for (let i = 0; i < n; i++) {
    const v = vals[i];
    if (v == null || v <= 0) continue;
    const h = Math.max(0.7, (v / max) * (H - 2));
    rects +=
      '<rect x="' + (i * bw).toFixed(2) + '" y="' + (H - h).toFixed(2) +
      '" width="' + Math.max(0.8, bw - 0.2).toFixed(2) + '" height="' + h.toFixed(2) +
      '" fill="' + COLORS.accent + '" opacity="0.85"></rect>';
  }
  let last = -1;
  for (let k = 0; k < n; k++) if (vals[k] != null) last = k;
  const line =
    last >= 0
      ? '<line x1="' + ((last + 0.5) * bw).toFixed(2) + '" y1="0" x2="' + ((last + 0.5) * bw).toFixed(2) +
        '" y2="' + H + '" stroke="rgba(201,211,224,0.35)" stroke-width="1" stroke-dasharray="2 2"></line>'
      : "";
  return (
    '<svg class="spark" viewBox="0 0 ' + W + " " + H + '" width="' + W + '" height="' + H +
    '" preserveAspectRatio="none" aria-hidden="true">' + rects + line + "</svg>"
  );
}

function renderEtfTable() {
  const tb = $("etfTable");
  if (!tb) return;
  const rows = (DATA.initData.table || []).slice().sort((a, b) => b.amount - a.amount);
  if (!rows.length) {
    tb.innerHTML = "";
    return;
  }
  const maxA = Math.max(...rows.map((r) => r.amount));
  const total = rows.reduce((s, r) => s + r.amount, 0);
  const absMax = Math.max(...rows.map((r) => Math.abs(r.pct)));
  let html = "";
  rows.forEach((r) => {
    const share = total > 0 ? (r.amount / total) * 100 : 0;
    const w = maxA > 0 ? (r.amount / maxA) * 100 : 0;
    const t = absMax ? Math.min(1, Math.abs(r.pct) / absMax) : 0;
    const a = (0.1 + 0.58 * t).toFixed(3);
    const rgb = r.pct > 0 ? "229,72,77" : r.pct < 0 ? "48,164,108" : "139,149,165";
    html +=
      "<tr><td>" + r.code + "</td><td>" + r.name + "</td>" +
      '<td class="num etf-amt-cell"><span class="etf-amt-bg" style="width:' + w.toFixed(1) + '%"></span>' +
      '<span class="etf-amt-val">' + fmt(r.amount, 2) + "</span></td>" +
      '<td class="etf-mini">' + sparkSvg(r.spark || null) + "</td>" +
      '<td class="num">' + fmt(share, 1) + "%</td>" +
      '<td class="num etf-pct-cell ' + pctClass(r.pct) + '" style="background:rgba(' + rgb + "," + a + ')">' +
      signed(r.pct, 2) + "%</td></tr>";
  });
  tb.innerHTML = html;
}

function drawMinuteChart() {
  const c = chart("chart");
  if (!c) return;
  const bars = alignToFull(MINUTE.times, MINUTE.bars);
  const closes = alignToFull(MINUTE.times, MINUTE.closes);
  const mark = MINUTE.preClose
    ? {
        symbol: "none",
        lineStyle: { type: "dashed", width: 1, color: COLORS.axis },
        label: { formatter: "昨收 " + fmt(MINUTE.preClose, 2), position: "insideEndTop", color: COLORS.axis },
        data: [{ yAxis: MINUTE.preClose }],
      }
    : undefined;
  c.setOption(
    {
      backgroundColor: "transparent",
      title: {
        text: "20只ETF分钟成交额与上证综指分时",
        left: 8, top: 4, padding: 0,
        textStyle: { fontSize: 13, color: "#e6e9ef", fontWeight: "normal" },
      },
      legend: {
        data: ["20只ETF分钟成交额合计", "上证综指"],
        left: 8, top: 26, type: "scroll", height: 22,
        itemWidth: 14, itemHeight: 9,
        textStyle: { color: COLORS.axis, fontSize: 11 },
      },
      grid: { top: 74, left: 12, right: 8, bottom: 50, containLabel: true },
      tooltip: darkTooltip({
        trigger: "axis",
        formatter: (ps) => {
          const out = [ps[0].axisValue];
          ps.forEach((p) => {
            if (p.seriesType === "bar") {
              out.push("ETF分钟成交额：" + (p.value == null ? "--" : fmt(p.value, 2) + " 亿"));
            } else {
              out.push("上证综指：" + (p.value == null ? "--" : fmt(p.value, 2)));
            }
          });
          const i = FULL_T.indexOf(ps[0].axisValue);
          if (i >= 0 && MINUTE.cum[i] != null) {
            out.push("累计（截至该分钟）：" + fmt(MINUTE.cum[i], 2) + " 亿");
          }
          if (MINUTE.preClose && ps[1] && ps[1].value != null) {
            out.push("较昨收：" + ((Number(ps[1].value) / MINUTE.preClose - 1) * 100).toFixed(2) + "%");
          }
          return out.join("<br/>");
        },
      }),
      xAxis: {
        type: "category",
        data: FULL_T,
        axisLabel: { hideOverlap: true, color: COLORS.axis, fontSize: 10 },
        axisLine: { lineStyle: { color: "rgba(201,211,224,0.18)" } },
      },
      yAxis: [
        {
          type: "value", name: "亿元", min: 0, max: barAxisMax(bars), splitNumber: 5,
          nameTextStyle: { align: "left", color: COLORS.axis, fontSize: 11 },
          axisLabel: { color: COLORS.axis, fontSize: 10 },
          splitLine: { lineStyle: { color: COLORS.grid } },
        },
        {
          type: "value", name: "上证综指（点）", scale: true,
          nameTextStyle: { align: "right", color: COLORS.axis, fontSize: 11 },
          axisLabel: { color: COLORS.axis, fontSize: 10 },
          splitLine: { show: false },
        },
      ],
      dataZoom: [
        { type: "inside", xAxisIndex: 0, moveOnMouseMove: false, zoomOnMouseWheel: true },
        { type: "slider", xAxisIndex: 0, height: 14, bottom: 10 },
      ],
      series: [
        {
          name: "20只ETF分钟成交额合计", type: "bar", data: bars,
          itemStyle: { color: "rgba(245,165,36,0.55)" },
        },
        {
          name: "上证综指", type: "line", yAxisIndex: 1, data: closes, connectNulls: false,
          showSymbol: false, lineStyle: { width: 1.6, color: COLORS.cool },
          itemStyle: { color: COLORS.cool }, markLine: mark,
        },
      ],
    },
    true
  );
}

function drawCumChart(extra) {
  const c = chart("chartCum");
  if (!c) return;
  const typ = DATA.typData || {};
  const cumData = DATA.cumData || {};
  const series = [];

  /* 今日线用第①部分分钟成交额累加；只有在数据日确实是「今天」时才画，
     否则会与「昨日线」完全重合，看着像画错了。 */
  const today = (extra && extra.today) || [];
  if (today.length) {
    series.push({
      name: "今日", type: "line", data: today, connectNulls: false, showSymbol: false,
      lineStyle: { width: 2.4, color: COLORS.accent }, itemStyle: { color: COLORS.accent },
    });
  }
  const pick = (extra && extra.pick) || [];
  if (pick.length) {
    series.push({
      name: extra.pickLabel || "对比日", type: "line", data: pick, connectNulls: false, showSymbol: false,
      lineStyle: { width: 1.8, color: COLORS.violet }, itemStyle: { color: COLORS.violet },
    });
  }
  const avg = cumData.avg20 || [];
  if (avg.length) {
    series.push({
      name: "近" + (cumData.avgDays || 20) + "日均值", type: "line", data: avg,
      connectNulls: false, showSymbol: false,
      lineStyle: { width: 1, color: COLORS.ink, type: "dashed", opacity: 0.9 },
      itemStyle: { color: COLORS.ink },
    });
  }
  if (!series.length) return;

  const visible = series.map((s) => s.name);
  c.setOption(
    {
      backgroundColor: "transparent",
      title: {
        text: "20只ETF当日分钟累计成交额（亿元）",
        left: 8, top: 4, padding: 0,
        textStyle: { fontSize: 13, color: "#e6e9ef", fontWeight: "normal" },
      },
      legend: {
        data: visible, left: 8, top: 26, type: "scroll", height: 22,
        itemWidth: 14, itemHeight: 9, textStyle: { color: COLORS.axis, fontSize: 11 },
      },
      grid: { top: 74, left: 12, right: 8, bottom: 50, containLabel: true },
      tooltip: darkTooltip({
        trigger: "axis",
        formatter: (ps) => {
          const out = [ps[0].axisValue];
          ps.forEach((p) => {
            out.push(p.seriesName + "：" + (p.value == null ? "--" : fmt(p.value, 2) + " 亿"));
          });
          return out.join("<br/>");
        },
      }),
      xAxis: {
        type: "category", data: FULL_T,
        axisLabel: { hideOverlap: true, color: COLORS.axis, fontSize: 10 },
        axisLine: { lineStyle: { color: "rgba(201,211,224,0.18)" } },
      },
      yAxis: {
        type: "value", name: "累计成交额（亿元）", min: 0,
        nameTextStyle: { align: "left", color: COLORS.axis, fontSize: 11 },
        axisLabel: { color: COLORS.axis, fontSize: 10 },
        splitLine: { lineStyle: { color: COLORS.grid } },
      },
      dataZoom: [
        { type: "inside", xAxisIndex: 0 },
        { type: "slider", xAxisIndex: 0, height: 14, bottom: 10 },
      ],
      series,
    },
    true
  );
}

/* --------------------------------------------------------- ② 放量信号 */

function drawTypChart(id, cfg) {
  const c = chart(id);
  if (!c) return;
  const typ = DATA.typData || {};
  c.setOption(
    {
      backgroundColor: "transparent",
      title: {
        text: cfg.title, left: 8, top: 4, padding: 0,
        textStyle: { fontSize: 13, color: "#e6e9ef", fontWeight: "normal" },
      },
      legend: {
        data: [cfg.barName, cfg.lineName], left: 8, top: 26, type: "scroll", height: 22,
        itemWidth: 14, itemHeight: 9, textStyle: { color: COLORS.axis, fontSize: 11 },
      },
      grid: { top: 74, left: 12, right: 8, bottom: 52, containLabel: true },
      tooltip: darkTooltip({
        trigger: "axis",
        formatter: (ps) => {
          const i = ps[0].dataIndex;
          const out = [ps[0].axisValue];
          ps.forEach((p) => {
            out.push(p.seriesName + "：" + (p.value == null ? "--" : fmt(p.value, 2) + " 亿/分钟"));
          });
          return out.join("<br/>");
        },
      }),
      xAxis: {
        type: "category", data: typ.slots || [],
        axisLabel: { interval: 14, hideOverlap: true, color: COLORS.axis, fontSize: 10 },
        axisLine: { lineStyle: { color: "rgba(201,211,224,0.18)" } },
      },
      yAxis: {
        type: "value", name: "亿元/分钟", min: 0,
        nameTextStyle: { align: "left", color: COLORS.axis, fontSize: 11 },
        axisLabel: { color: COLORS.axis, fontSize: 10 },
        splitLine: { lineStyle: { color: COLORS.grid } },
      },
      dataZoom: [
        { type: "inside", xAxisIndex: 0 },
        { type: "slider", xAxisIndex: 0, height: 12, bottom: 10 },
      ],
      series: [
        { name: cfg.barName, type: "bar", data: cfg.amt, itemStyle: { color: cfg.color }, z: 2 },
        {
          name: cfg.lineName, type: "line", data: cfg.other, showSymbol: false, z: 3,
          lineStyle: { width: 1.8, color: cfg.lineColor, type: "dashed" },
          itemStyle: { color: cfg.lineColor },
        },
      ],
    },
    true
  );
}

/* ----------------------------------------------------- ③ 日度资金全景 */

function drawDailyChart(id, cfg) {
  const c = chart(id);
  if (!c) return;
  const D = DATA.dailyData || {};
  const dates = D.dates || [];
  if (!dates.length) return;

  const bars = cfg.metric === "netflow"
    ? (D.netflow || []).map((v) => ({
        value: v,
        itemStyle: { color: v >= 0 ? COLORS.up : COLORS.down },
      }))
    : (D.amount || []);

  const cum = cumulative(D.netflow || []);
  c.setOption(
    {
      backgroundColor: "transparent",
      title: {
        text: cfg.title, left: 8, top: 4, padding: 0,
        textStyle: { fontSize: 13, color: "#e6e9ef", fontWeight: "normal" },
      },
      legend: {
        data: [cfg.barName, "上证综指"], left: 8, top: 26, type: "scroll", height: 22,
        itemWidth: 14, itemHeight: 9, textStyle: { color: COLORS.axis, fontSize: 11 },
      },
      grid: { top: 74, left: 12, right: 8, bottom: 52, containLabel: true },
      tooltip: darkTooltip({
        trigger: "axis",
        formatter: (ps) => {
          const i = ps[0].dataIndex;
          const out = [ps[0].axisValue];
          ps.forEach((p) => {
            if (p.seriesType === "bar") {
              out.push(
                (cfg.metric === "netflow" ? "当日净流入：" : "当日成交额：") + signed(p.value, 2) + " 亿"
              );
            } else {
              out.push("上证指数：" + fmt(p.value, 2));
            }
          });
          if (cfg.metric === "netflow" && cum[i] != null) {
            out.push("累计净流入：" + signed(cum[i], 1) + " 亿");
          }
          return out.join("<br/>");
        },
      }),
      xAxis: {
        type: "category", data: dates,
        axisLabel: { hideOverlap: true, color: COLORS.axis, fontSize: 10 },
        axisLine: { lineStyle: { color: "rgba(201,211,224,0.18)" } },
      },
      yAxis: [
        {
          type: "value", name: "亿元", scale: true,
          nameTextStyle: { align: "left", color: COLORS.axis, fontSize: 11 },
          axisLabel: { color: COLORS.axis, fontSize: 10 },
          splitLine: { lineStyle: { color: COLORS.grid } },
        },
        {
          type: "value", name: "上证指数（点）", scale: true,
          nameTextStyle: { align: "right", color: COLORS.axis, fontSize: 11 },
          axisLabel: { color: COLORS.axis, fontSize: 10 },
          splitLine: { show: false },
        },
      ],
      dataZoom: [
        { type: "inside", xAxisIndex: 0, moveOnMouseMove: false, zoomOnMouseWheel: true },
        { type: "slider", xAxisIndex: 0, height: 14, bottom: 12 },
      ],
      series: [
        { name: cfg.barName, type: "bar", data: bars },
        {
          name: "上证综指", type: "line", yAxisIndex: 1, data: D.idx || [],
          connectNulls: true, showSymbol: false,
          lineStyle: { width: 1.6, color: COLORS.cool }, itemStyle: { color: COLORS.cool },
        },
      ],
    },
    true
  );
}

function drawBroadChart() {
  const c = chart("chartBroad");
  if (!c) return;
  const BR = DATA.broadData || {};
  if (!(BR.dates || []).length) return;
  const cats = BR.cats || [];
  const series = cats.map((cname, i) => ({
    name: cname, type: "bar", stack: "broad", data: BR.series[cname] || [],
    itemStyle: { color: (BR.colors || [])[i % (BR.colors || []).length] },
  }));
  series.push({
    name: "累计净流入", type: "line", yAxisIndex: 1, data: BR.totalCum || [],
    showSymbol: false, lineStyle: { width: 2.2, color: COLORS.accent },
    itemStyle: { color: COLORS.accent },
  });
  c.setOption(
    {
      backgroundColor: "transparent",
      title: {
        text: "全市场宽基ETF日度净流入与累计净流入",
        left: 8, top: 4, padding: 0,
        textStyle: { fontSize: 13, color: "#e6e9ef", fontWeight: "normal" },
      },
      legend: {
        top: 26, left: 8, type: "scroll", height: 22, itemWidth: 12, itemHeight: 9,
        textStyle: { color: COLORS.axis, fontSize: 11 },
      },
      grid: { top: 74, left: 12, right: 12, bottom: 52, containLabel: true },
      tooltip: darkTooltip({
        trigger: "axis",
        formatter: (arr) => {
          let tot = 0;
          const out = [arr[0].axisValue];
          arr.forEach((p) => {
            if (p.seriesType !== "bar") return;
            tot += p.value || 0;
            if (p.value) {
              out.push(
                '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:' +
                p.color + ';margin-right:5px"></span>' + p.seriesName + "：" + signed(p.value, 1) + " 亿"
              );
            }
          });
          out.push("当日合计：" + signed(tot, 1) + " 亿");
          arr.forEach((p) => {
            if (p.seriesType === "line" && p.value != null) {
              out.push("累计净流入：" + signed(p.value, 0) + " 亿");
            }
          });
          return out.join("<br/>");
        },
      }),
      xAxis: {
        type: "category", data: BR.dates,
        axisLabel: { hideOverlap: true, color: COLORS.axis, fontSize: 10 },
        axisLine: { lineStyle: { color: "rgba(201,211,224,0.18)" } },
      },
      yAxis: [
        {
          type: "value", name: "当日净流入（亿元）", scale: true,
          nameTextStyle: { align: "left", color: COLORS.axis, fontSize: 11 },
          axisLabel: { color: COLORS.axis, fontSize: 10 },
          splitLine: { lineStyle: { color: COLORS.grid } },
        },
        {
          type: "value", name: "累计净流入（亿元）", scale: true,
          nameTextStyle: { align: "right", color: COLORS.axis, fontSize: 11 },
          axisLabel: { color: COLORS.axis, fontSize: 10 },
          splitLine: { show: false },
        },
      ],
      dataZoom: [
        { type: "inside", xAxisIndex: 0, moveOnMouseMove: false, zoomOnMouseWheel: true },
        { type: "slider", xAxisIndex: 0, height: 14, bottom: 12 },
      ],
      series,
    },
    true
  );
}

function renderBroadTable() {
  const host = $("broadTable");
  if (!host) return;
  const BR = DATA.broadData || {};
  if (!(BR.dates || []).length) return;
  const RN = 22;
  const idxs = [];
  for (let i = BR.dates.length - 1; i >= 0 && idxs.length < RN; i--) idxs.push(i);
  const cols = ["合计"].concat(BR.cats || []);
  /* 颜色深浅按「当列」最大绝对值归一化：各分类量级差很远，
     按全局归一会让小分类永远白底（等于白做条件格式）。 */
  const colMax = cols.map((cn) => {
    let m = 0;
    idxs.forEach((i) => {
      const v = cn === "合计" ? BR.total[i] : (BR.series[cn] || [])[i];
      if (v != null && Math.abs(v) > m) m = Math.abs(v);
    });
    return m || 1;
  });
  let html =
    "<thead><tr><th>日期</th>" +
    cols.map((cn) => '<th class="num">' + cn + "</th>").join("") +
    "</tr></thead><tbody>";
  idxs.forEach((i) => {
    html += "<tr><td>" + BR.dates[i] + "</td>";
    cols.forEach((cn, k) => {
      const v = cn === "合计" ? BR.total[i] : (BR.series[cn] || [])[i];
      let style = "";
      if (v != null && v) {
        const t = Math.min(1, Math.abs(v) / colMax[k]);
        if (t >= 0.02) {
          const a = (0.06 + 0.5 * t).toFixed(3);
          style = "background:rgba(" + (v > 0 ? "229,72,77" : "48,164,108") + "," + a + ");";
        }
      }
      const txt = v == null ? "--" : signed(v, cn === "合计" ? 1 : 2);
      html += '<td class="num" style="' + style + '">' + txt + "</td>";
    });
    html += "</tr>";
  });
  html += "</tbody>";
  host.innerHTML = html;
}

/* --------------------------------------------------------- ④ ETF 两融 */

function drawMarginCharts() {
  const M = DATA.marginData || null;
  const sel = $("marginCat");
  if (!M || !(M.dates || []).length || !sel) return;

  const cats = M.cats || [];
  if (!sel.options.length) {
    cats.forEach((c) => {
      const o = document.createElement("option");
      o.value = c;
      o.textContent = c;
      sel.appendChild(o);
    });
  }

  function draw() {
    const cat = sel.value;
    const rz = (M.rzye || {})[cat] || [];
    const px = (M.px || {})[cat] || [];
    const jm = (M.rzjme || {})[cat] || [];
    const nf = (M.nf || {})[cat] || [];
    const meta = (M.meta || {})[cat] || {};

    const bal = chart("chartMarginBal");
    const flow = chart("chartMarginFlow");
    if (!bal || !flow) return;

    setTag(
      "tagMargin",
      cat + " · 融资余额 " + (rz.length ? fmt(rz[rz.length - 1], 2) : "--") + " 亿" +
        " · 融资净买入 " + (jm.length ? signed(jm[jm.length - 1], 2) : "--") + " 亿" +
        " · 覆盖 " + (meta.n || "--") + " 只ETF",
      "ok"
    );

    bal.setOption(
      {
        backgroundColor: "transparent",
        title: {
          text: cat + " · ETF融资余额（亿元）与指数收盘",
          left: 8, top: 4, padding: 0,
          textStyle: { fontSize: 13, color: "#e6e9ef", fontWeight: "normal" },
        },
        legend: {
          data: ["融资余额", "指数收盘"], left: 8, top: 26, type: "scroll", height: 22,
          itemWidth: 14, itemHeight: 9, textStyle: { color: COLORS.axis, fontSize: 11 },
        },
        grid: { top: 74, left: 12, right: 8, bottom: 52, containLabel: true },
        tooltip: darkTooltip({
          trigger: "axis",
          formatter: (ps) => {
            const out = [ps[0].axisValue];
            ps.forEach((p) => {
              out.push(p.seriesName + "：" + (p.value == null ? "--" : fmt(p.value, 2)));
            });
            return out.join("<br/>");
          },
        }),
        xAxis: {
          type: "category", data: M.dates,
          axisLabel: { hideOverlap: true, color: COLORS.axis, fontSize: 10 },
          axisLine: { lineStyle: { color: "rgba(201,211,224,0.18)" } },
        },
        yAxis: [
          {
            type: "value", name: "融资余额（亿元）", scale: true,
            nameTextStyle: { align: "left", color: COLORS.axis, fontSize: 11 },
            axisLabel: { color: COLORS.axis, fontSize: 10 },
            splitLine: { lineStyle: { color: COLORS.grid } },
          },
          {
            type: "value", name: "指数（点）", scale: true,
            nameTextStyle: { align: "right", color: COLORS.axis, fontSize: 11 },
            axisLabel: { color: COLORS.axis, fontSize: 10 },
            splitLine: { show: false },
          },
        ],
        dataZoom: [
          { type: "inside", xAxisIndex: 0, moveOnMouseMove: false, zoomOnMouseWheel: true },
          { type: "slider", xAxisIndex: 0, height: 14, bottom: 12 },
        ],
        series: [
          {
            name: "融资余额", type: "bar", data: rz,
            itemStyle: { color: "rgba(91,141,239,0.6)" },
          },
          {
            name: "指数收盘", type: "line", yAxisIndex: 1, data: px, connectNulls: true,
            showSymbol: false, lineStyle: { width: 1.6, color: COLORS.accent },
            itemStyle: { color: COLORS.accent },
          },
        ],
      },
      true
    );

    flow.setOption(
      {
        backgroundColor: "transparent",
        title: {
          text: cat + " · 融资净买入 vs 全市场ETF申赎净买入（亿元）",
          left: 8, top: 4, padding: 0,
          textStyle: { fontSize: 13, color: "#e6e9ef", fontWeight: "normal" },
        },
        legend: {
          data: ["融资净买入", "ETF申赎净买入"], left: 8, top: 26, type: "scroll", height: 22,
          itemWidth: 14, itemHeight: 9, textStyle: { color: COLORS.axis, fontSize: 11 },
        },
        grid: { top: 74, left: 12, right: 8, bottom: 52, containLabel: true },
        tooltip: darkTooltip({
          trigger: "axis",
          formatter: (ps) => {
            const out = [ps[0].axisValue];
            ps.forEach((p) => {
              out.push(p.seriesName + "：" + (p.value == null ? "--" : signed(p.value, 2) + " 亿"));
            });
            return out.join("<br/>");
          },
        }),
        xAxis: {
          type: "category", data: M.dates,
          axisLabel: { hideOverlap: true, color: COLORS.axis, fontSize: 10 },
          axisLine: { lineStyle: { color: "rgba(201,211,224,0.18)" } },
        },
        yAxis: {
          type: "value", name: "亿元", scale: true,
          nameTextStyle: { align: "left", color: COLORS.axis, fontSize: 11 },
          axisLabel: { color: COLORS.axis, fontSize: 10 },
          splitLine: { lineStyle: { color: COLORS.grid } },
        },
        dataZoom: [
          { type: "inside", xAxisIndex: 0, moveOnMouseMove: false, zoomOnMouseWheel: true },
          { type: "slider", xAxisIndex: 0, height: 14, bottom: 12 },
        ],
        series: [
          {
            name: "融资净买入", type: "bar", data: jm,
            itemStyle: { color: "rgba(245,165,36,0.6)" },
          },
          {
            name: "ETF申赎净买入", type: "line", data: nf, connectNulls: true,
            showSymbol: false, lineStyle: { width: 1.6, color: COLORS.violet },
            itemStyle: { color: COLORS.violet },
          },
        ],
      },
      true
    );
  }

  sel.addEventListener("change", draw);
  draw();
}

/* --------------------------------------------------------- ⑤ 主题分时 */

function renderThemeSection(themes, minutes, indices, cumData) {
  const sel = $("themeCat");
  if (!sel) return;
  const list = (themes && themes.themes) || [];
  if (!list.length) {
    sel.innerHTML = '<option value="">无可用主题</option>';
    setTag("tagThemeMin", "无可用主题", "warn");
    return;
  }
  sel.innerHTML = "";
  list.forEach((t) => {
    const o = document.createElement("option");
    o.value = t.key;
    o.textContent = t.key + "（" + (t.n || t.codes.length) + "只）";
    sel.appendChild(o);
  });

  const paint = () => {
    const key = sel.value;
    if (!key) return;
    drawThemeCharts(key, themes, minutes, indices, cumData);
    const theme = list.find((x) => x.key === key);
    const agg = aggregateTheme((minutes || {})[key] || {});
    const line = pickIndexLine(key, indices, agg.closeBySym);
    /* 状态条要说明「用了哪条价格线」—— 兜底合成的精度明显低于真实指数，
       不说清楚的话读者会把两条线当成同等精度。 */
    setTag(
      "tagThemeMin",
      key + " · " + (theme ? theme.n : "?") + " 只ETF · 数据日 " + (agg.date || "--") +
        " · 价格线：" + line.label + (line.source === "synthetic" ? "（兜底）" : ""),
      line.source === "synthetic" ? "warn" : "ok"
    );
  };

  sel.addEventListener("change", paint);
  paint();
}

/* --------------------------------------------------------- ⑤ 主题分时 */

/** 代码 → 交易所前缀符号：159915.SZ → sz159915。 */
function toSymbol(code) {
  const c = String(code);
  if (!c.includes(".")) return c;
  const [num, , mkt] = c.split(".");
  return (mkt.toUpperCase() === "SH" ? "sh" : "sz") + num;
}

/**
 * 把一个主题所有 ETF 的分钟数据聚合到全天 241 点。
 *
 * 两个坑（原站踩过，这里沿用同样的处理）：
 * 1. 分钟接口按「根数」返回，里面**会带上前一交易日**的尾巴。
 *    不过滤日期，历史收盘价会被填到今天的 15:00 上 ⇒ 柱子一路铺满，
 *    看着像"还没到点就已经收盘"。
 * 2. 停牌 / 当日无成交的 ETF 会把昨天的全天数据混进来，同样要先按日期过滤。
 */
function aggregateTheme(series) {
  let latest = "";
  Object.values(series || {}).forEach((arr) => {
    for (let i = (arr || []).length - 1; i >= 0; i--) {
      const d = String(arr[i].day || "").slice(0, 10);
      if (d) { if (d > latest) latest = d; break; }
    }
  });

  const amountByMin = {};
  const closeBySym = {};
  Object.entries(series || {}).forEach(([sym, arr]) => {
    let base = null;
    const m = {};
    (arr || []).forEach((row) => {
      const day = String(row.day || "");
      if (latest && day.slice(0, 10) !== latest) return;
      const hhmm = day.slice(11, 16);
      if (!hhmm) return;
      const amt = parseFloat(row.amount || 0);
      if (isFinite(amt) && amt > 0) {
        amountByMin[hhmm] = (amountByMin[hhmm] || 0) + amt;
      }
      const close = parseFloat(row.close);
      if (isFinite(close) && close > 0) {
        if (base == null) base = close;
        m[hhmm] = close;
      }
    });
    if (base) closeBySym[sym] = { base, m };
  });

  return {
    date: latest,
    amount: FULL_T.map((t) => (amountByMin[t] == null ? null : amountByMin[t] / 1e8)),
    closeBySym,
  };
}

/**
 * 兜底价格线：主题 ETF 等权合成（基期 1000）。
 * 只有在**真实指数与新浪指数都拿不到**时才用（见 pickIndexLine）。
 */
function syntheticIndexLine(closeBySym) {
  const pxMap = {};
  FULL_T.forEach((hhmm) => {
    let sum = 0;
    let n = 0;
    Object.values(closeBySym || {}).forEach(({ base, m }) => {
      const v = m[hhmm];
      if (v != null && isFinite(v)) { sum += v / base; n += 1; }
    });
    if (n) pxMap[hhmm] = +(1000 * sum / n).toFixed(1);
  });
  return FULL_T.map((t) => (pxMap[t] == null ? null : pxMap[t]));
}

/**
 * 选价格参照线，三级优先级（与原站一致，但**把选中了哪条写进返回值**）：
 *   ① 东财指数分钟（/api/idxminute）—— 最准，是真实指数点位
 *   ② 新浪指数分钟（/api/kline）—— 次选
 *   ③ 主题 ETF 等权合成（基期 1000）—— 兜底
 *
 * ⚠️ 两个实测事实（2026-11-25）：
 *   - idxminute 的时间戳**没有秒**（"2026-11-25 09:30"），ktrends 有
 *     （"2026-11-25 09:30:00"）。两种格式都要能解析。
 *   - idxminute 对某些 secid（如上证 000001）**稳定失败**，所以必须有回落。
 */
function pickIndexLine(themeKey, indices, closeBySym) {
  const entry = (indices || {})[themeKey] || {};

  const toLine = (rows) => {
    if (!Array.isArray(rows) || rows.length < 10) return null;
    // 只取最新那一天（分钟接口可能带上前一交易日的尾巴）
    let latest = "";
    for (const r of rows) {
      const d = String(r.day || "").slice(0, 10);
      if (d && d > latest) latest = d;
    }
    if (!latest) return null;
    const map = {};
    for (const r of rows) {
      const day = String(r.day || "");
      if (day.slice(0, 10) !== latest) continue;
      const hhmm = day.slice(11, 16);
      const close = parseFloat(r.close);
      if (hhmm && isFinite(close)) map[hhmm] = close;
    }
    const values = Object.values(map);
    if (values.length < 10) return null;
    return {
      px: FULL_T.map((t) => (map[t] == null ? null : map[t])),
      date: latest,
    };
  };

  const idx = toLine(entry.idx);
  if (idx) {
    return { px: idx.px, source: "index", label: "主题指数", date: idx.date };
  }
  const sina = toLine(entry.sina);
  if (sina) {
    return { px: sina.px, source: "sina", label: "主题指数", date: sina.date };
  }
  return {
    px: syntheticIndexLine(closeBySym),
    source: "synthetic",
    label: "主题ETF等权合成",
    date: "",
  };
}

function drawThemeCharts(themeKey, themesData, minutesData, indicesData, cumData) {
  const theme = (themesData.themes || []).find((x) => x.key === themeKey);
  if (!theme) return;
  const minuteSeries = (minutesData || {})[themeKey] || {};
  const agg = aggregateTheme(minuteSeries);

  const amtArr = agg.amount;
  /* 价格参照线：真实指数优先，拿不到才回落等权合成（pickIndexLine 内部三级）。 */
  const indexLine = pickIndexLine(themeKey, indicesData, agg.closeBySym);
  const pxArr = indexLine.px;

  /* 合成指数线的取值范围（留 20% 余量，避免贴边） */
  let pmin = null;
  let pmax = null;
  pxArr.forEach((v) => {
    if (v == null) return;
    if (pmin == null || v < pmin) pmin = v;
    if (pmax == null || v > pmax) pmax = v;
  });
  const pad = pmin != null && pmax != null ? (pmax - pmin) * 0.2 || pmax * 0.005 : null;

  const min = chart("chartThemeMin");
  if (min) {
    min.setOption(
      {
        backgroundColor: "transparent",
        title: {
          text: theme.key + " · 主题ETF分钟成交额与" + indexLine.label,
          left: 8, top: 4, padding: 0,
          textStyle: { fontSize: 13, color: "#e6e9ef", fontWeight: "normal" },
        },
        legend: {
          data: ["主题ETF分钟成交额", indexLine.label],
          left: 8, top: 26, type: "scroll", height: 22,
          itemWidth: 14, itemHeight: 9, textStyle: { color: COLORS.axis, fontSize: 11 },
        },
        grid: { top: 74, left: 12, right: 12, bottom: 50, containLabel: true },
        tooltip: darkTooltip({
          trigger: "axis",
          formatter: (ps) => {
            const out = [ps[0].axisValue];
            ps.forEach((p) => {
              if (p.seriesType === "bar") {
                out.push("分钟成交额：" + (p.value == null ? "--" : fmt(p.value, 2) + " 亿"));
              } else {
                out.push(indexLine.label + "：" + (p.value == null ? "--" : fmt(p.value, 1)));
              }
            });
            return out.join("<br/>");
          },
        }),
        xAxis: {
          type: "category", data: FULL_T,
          axisLabel: { hideOverlap: true, color: COLORS.axis, fontSize: 10 },
          axisLine: { lineStyle: { color: "rgba(201,211,224,0.18)" } },
        },
        yAxis: [
          {
            type: "value", name: "亿元/分钟", min: 0,
            nameTextStyle: { align: "left", color: COLORS.axis, fontSize: 11 },
            axisLabel: { color: COLORS.axis, fontSize: 10 },
            splitLine: { lineStyle: { color: COLORS.grid } },
          },
          {
            type: "value",
            name: indexLine.source === "synthetic" ? "等权合成（基期1000）" : "指数点位",
            scale: true,
            min: pad != null ? pmin - pad : null,
            max: pad != null ? pmax + pad : null,
            nameTextStyle: { align: "right", color: COLORS.axis, fontSize: 11 },
            axisLabel: { color: COLORS.axis, fontSize: 10 },
            splitLine: { show: false },
          },
        ],
        dataZoom: [
          { type: "inside", xAxisIndex: 0 },
          { type: "slider", xAxisIndex: 0, height: 14, bottom: 10 },
        ],
        series: [
          {
            name: "主题ETF分钟成交额", type: "bar", data: amtArr,
            itemStyle: { color: "rgba(245,165,36,0.55)" },
          },
          {
            name: indexLine.label, type: "line", yAxisIndex: 1, data: pxArr,
            connectNulls: false, showSymbol: false,
            lineStyle: { width: 1.8, color: COLORS.cool },
            itemStyle: { color: COLORS.cool },
          },
        ],
      },
      true
    );
  }

  /* 累计图：今日累计 vs 近 N 日累计均值基准 */
  const cum = chart("chartThemeCum");
  if (!cum) return;
  const todayCum = [];
  let acc = 0;
  amtArr.forEach((v) => {
    acc += v || 0;
    todayCum.push(+acc.toFixed(2));
  });
  const pack = (cumData && cumData.themes && cumData.themes[themeKey]) || null;
  const avg = pack ? pack.cum || [] : [];
  const nAvg = pack ? pack.n || 0 : 0;

  const series = [
    {
      name: "今日累计", type: "line", data: todayCum, connectNulls: false, showSymbol: false,
      lineStyle: { width: 2.4, color: COLORS.accent }, itemStyle: { color: COLORS.accent },
    },
  ];
  if (avg.length) {
    series.push({
      name: "近" + nAvg + "日均值", type: "line", data: avg, connectNulls: false, showSymbol: false,
      lineStyle: { width: 1.2, color: COLORS.violet, type: "dashed" },
      itemStyle: { color: COLORS.violet },
    });
  }

  cum.setOption(
    {
      backgroundColor: "transparent",
      title: {
        text: theme.key + " · 主题ETF分钟累计成交额（亿元）",
        left: 8, top: 4, padding: 0,
        textStyle: { fontSize: 13, color: "#e6e9ef", fontWeight: "normal" },
      },
      legend: {
        data: series.map((s) => s.name), left: 8, top: 26, type: "scroll", height: 22,
        itemWidth: 14, itemHeight: 9, textStyle: { color: COLORS.axis, fontSize: 11 },
      },
      grid: { top: 74, left: 12, right: 8, bottom: 50, containLabel: true },
      tooltip: darkTooltip({
        trigger: "axis",
        formatter: (ps) => {
          const out = [ps[0].axisValue];
          ps.forEach((p) => {
            out.push(p.seriesName + "：" + (p.value == null ? "--" : fmt(p.value, 2) + " 亿"));
          });
          return out.join("<br/>");
        },
      }),
      xAxis: {
        type: "category", data: FULL_T,
        axisLabel: { hideOverlap: true, color: COLORS.axis, fontSize: 10 },
        axisLine: { lineStyle: { color: "rgba(201,211,224,0.18)" } },
      },
      yAxis: {
        type: "value", name: "累计成交额（亿元）", min: 0,
        nameTextStyle: { align: "left", color: COLORS.axis, fontSize: 11 },
        axisLabel: { color: COLORS.axis, fontSize: 10 },
        splitLine: { lineStyle: { color: COLORS.grid } },
      },
      dataZoom: [
        { type: "inside", xAxisIndex: 0 },
        { type: "slider", xAxisIndex: 0, height: 14, bottom: 10 },
      ],
      series,
    },
    true
  );
}

/* ------------------------------------------------------------ 启动 */

async function init() {
  const status = $("etfStatus");
  if (status) status.innerHTML = '<span class="etf-tag">加载中…</span>';

  try {
    ECHARTS = window.echarts;
    if (!ECHARTS) throw new Error("ECharts 未加载");

    const [data, extra] = await Promise.all([loadData(), loadExtra()]);

    const initData = data.initData || {};
    MINUTE.times = initData.times || [];
    MINUTE.bars = initData.bars || [];
    MINUTE.closes = initData.closes || [];
    MINUTE.preClose = Number(initData.preClose) || null;
    MINUTE.date = String(initData.date || "");
    MINUTE.cum = cumulative(MINUTE.bars);

    renderEtfTable();
    drawMinuteChart();
    drawCumChart({ today: alignToFull(MINUTE.times, MINUTE.cum) });

    const typ = data.typData || {};
    drawTypChart("chartTypCase", {
      title: "放量案例日 · 重点ETF分钟成交额",
      amt: typ.supAmt || [], other: typ.norAmt || [],
      barName: "放量日均值", lineName: "常态日均值",
      lineColor: COLORS.cool, color: "rgba(229,72,77,0.62)",
    });
    drawTypChart("chartTypNorm", {
      title: "常态交易日 · 重点ETF分钟成交额",
      amt: typ.norAmt || [], other: typ.supAmt || [],
      barName: "常态日均值", lineName: "放量日均值",
      lineColor: COLORS.up, color: "rgba(91,141,239,0.62)",
    });

    drawDailyChart("chartDAmt", {
      title: "20只ETF日度成交额与上证综指",
      barName: "20只ETF日度成交额合计", metric: "amount",
    });
    drawDailyChart("chartDNf", {
      title: "20只ETF日度净流入（申赎口径）与上证综指",
      barName: "20只ETF日度净流入", metric: "netflow",
    });
    drawBroadChart();
    renderBroadTable();
    drawMarginCharts();
    renderThemeSection(
      extra.themes,
      extra["theme-minutes"],
      extra["theme-indices"],
      extra["theme-cum"]
    );

    /* 数据新鲜度标签 */
    const cmp = data.cmpData || {};
    const latest = cmp.latest || {};
    const dailyDates = (data.dailyData || {}).dates || [];
    setTag(
      "etfDate",
      "数据截至 " + (latest.date || (dailyDates.length ? dailyDates[dailyDates.length - 1] : "--")),
      "ok"
    );
    setTag(
      "etfStats",
      "最新成交额 " + (latest.amount != null ? fmt(latest.amount, 2) + " 亿" : "--") +
        " · 净流入 " + (latest.netflow != null ? signed(latest.netflow, 2) + " 亿" : "--"),
      "ok"
    );
    setTag("etfUpdate", "已载入", "ok");
    if (status) status.innerHTML = "";
  } catch (err) {
    setTag("etfUpdate", "加载失败：" + (err && err.message ? err.message : err), "err");
    if (status) {
      status.innerHTML =
        '<span class="etf-tag etf-tag--err">看板数据加载失败：' +
        (err && err.message ? err.message : err) +
        "</span>";
    }
  }
}

window.addEventListener("resize", () => {
  CHARTS.forEach((c) => {
    if (c && c.resize) c.resize();
  });
});

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
