// 计划 vs 实际的对比判定（JS 版）。
// 与 decide_service.compare_plan_vs_actual 规则逐条对齐，
// 由 tests/test_decide_compare_parity.py 保证一致性。
// 配置来自 data/decide-config.json（即 config.PORTFOLIO_10W）。

function num(v) {
  if (v === null || v === undefined || v === '') return null;
  const f = Number(v);
  return Number.isFinite(f) ? f : null;
}

function dev(severity, kind, code, name, message, actual, expected) {
  return {
    severity, kind, code, name: name || code,
    message, actual: actual === undefined ? null : actual,
    expected: expected === undefined ? null : expected,
  };
}

export function dedupePlanRows(rows) {
  const seen = new Map();
  const order = [];
  for (const r of rows || []) {
    const code = String(r.code || '').trim();
    if (!code) continue;
    if (!seen.has(code)) order.push(code);
    seen.set(code, r);
  }
  return order.map(c => seen.get(c));
}

const SEV_ORDER = { danger: 0, warn: 1, info: 2 };

export function computeComparison(planRows, actualRows, cbRows, latestPrices, config) {
  const cfg = config;
  const totalCapital = Number(cfg.total_capital);

  const plan = dedupePlanRows(planRows);
  const actual = actualRows || [];
  const cb = cbRows || [];

  const planByCode = new Map(plan.map(r => [String(r.code), r]));
  const actualByCode = new Map(actual.map(r => [String(r.code), r]));

  const deviations = [];
  const pendingBuild = [];

  // 资金占用
  let stockUsed = 0;
  for (const r of actual) {
    stockUsed += (num(r.entry_price) || 0) * Math.trunc(r.shares || 0);
  }
  let cbUsed = 0;
  for (const r of cb) {
    cbUsed += (num(r.entry_price) || 0) * Math.trunc(r.shares || 0);
  }
  const cash = totalCapital - stockUsed - cbUsed;

  // 待建仓 / 漏买 / 多买
  const emptyPortfolio = actual.length === 0;
  for (const [code, p] of planByCode) {
    if (p.action !== 'buy' || actualByCode.has(code)) continue;
    if (emptyPortfolio) {
      pendingBuild.push({
        code,
        name: p.name || code,
        plan_price: num(p.plan_price),
        shares: p.shares,
        amount: Math.round((num(p.plan_price) || 0) * Math.trunc(p.shares || 0) * 100) / 100,
        stop_price: num(p.stop_price),
        tp_price: num(p.tp_price),
      });
    } else {
      deviations.push(dev('info', 'missing_buy', code, p.name,
        '计划买入但未持有', null, p.shares));
    }
  }
  for (const [code, a] of actualByCode) {
    if (!planByCode.has(code)) {
      deviations.push(dev('warn', 'unplanned', code, a.name,
        '持有但计划中没有（超配）', a.shares, 0));
    }
  }

  // 持仓数超限
  if (actual.length > Number(cfg.max_positions)) {
    deviations.push(dev('danger', 'too_many_positions', '', '',
      `持仓 ${actual.length} 只，超出上限 ${cfg.max_positions} 只`,
      actual.length, Number(cfg.max_positions)));
  }

  // 单票超上限
  const maxSingle = Number(cfg.max_single_weight) * totalCapital;
  for (const [code, a] of actualByCode) {
    const amt = (num(a.entry_price) || 0) * Math.trunc(a.shares || 0);
    if (amt > maxSingle) {
      deviations.push(dev('danger', 'single_weight_over', code, a.name,
        `单票占用 ¥${Math.round(amt).toLocaleString('en-US')}，超出 ${Math.round(Number(cfg.max_single_weight) * 100)}% 上限`,
        Math.round(amt * 100) / 100, Math.round(maxSingle * 100) / 100));
    }
  }

  // 缺止损 / 跌破止损 / 达止盈
  for (const [code, a] of actualByCode) {
    const stop = num(a.stop_price);
    const price = num((latestPrices || {})[code]);
    if (stop === null) {
      deviations.push(dev('warn', 'missing_stop', code, a.name, '未设置止损价'));
    } else if (price !== null && price <= stop) {
      deviations.push(dev('danger', 'stop_breached', code, a.name,
        `最新价 ${price.toFixed(2)} 已跌破止损 ${stop.toFixed(2)}，应执行止损`,
        price, stop));
    }
    const p = planByCode.get(code);
    const tp = p ? num(p.tp_price) : null;
    if (tp !== null && price !== null && price >= tp) {
      deviations.push(dev('info', 'tp_hit', code, a.name || code,
        `最新价 ${price.toFixed(2)} 已达止盈 ${tp.toFixed(2)}，可分批止盈`,
        price, tp));
    }
  }

  // 计划股数不符
  for (const [code, p] of planByCode) {
    const a = actualByCode.get(code);
    if (!a) continue;
    const want = p.shares;
    const have = a.shares;
    if (want !== null && want !== undefined && have !== null && have !== undefined
        && Math.trunc(want) !== Math.trunc(have)) {
      deviations.push(dev('info', 'shares_mismatch', code, p.name,
        `计划 ${Math.trunc(want)} 股，实际 ${Math.trunc(have)} 股`,
        Math.trunc(have), Math.trunc(want)));
    }
  }

  // 总仓位超限
  const maxExposure = Number(cfg.max_stock_exposure) * totalCapital;
  if (stockUsed > maxExposure) {
    deviations.push(dev('danger', 'exposure_over', '', '',
      `股票占用 ¥${Math.round(stockUsed).toLocaleString('en-US')}，超出 ${Math.round(Number(cfg.max_stock_exposure) * 100)}% 上限`,
      Math.round(stockUsed * 100) / 100, Math.round(maxExposure * 100) / 100));
  }

  // 转债仓超配
  const cbCapital = Number(cfg.cb_capital);
  if (cbUsed > cbCapital) {
    deviations.push(dev('warn', 'cb_over', '', '',
      `转债占用 ¥${Math.round(cbUsed).toLocaleString('en-US')}，超出 ¥${Math.round(cbCapital).toLocaleString('en-US')} 上限`,
      Math.round(cbUsed * 100) / 100, cbCapital));
  }
  if (cb.length > Number(cfg.max_cb_positions)) {
    deviations.push(dev('warn', 'cb_too_many', '', '',
      `转债持仓 ${cb.length} 只，超出上限 ${cfg.max_cb_positions} 只`,
      cb.length, Number(cfg.max_cb_positions)));
  }

  deviations.sort((x, y) => (SEV_ORDER[x.severity] ?? 3) - (SEV_ORDER[y.severity] ?? 3));

  const count = s => deviations.filter(d => d.severity === s).length;
  return {
    deviations,
    pending_build: pendingBuild,
    stock_used: Math.round(stockUsed * 100) / 100,
    cb_used: Math.round(cbUsed * 100) / 100,
    cash: Math.round(cash * 100) / 100,
    total_capital: totalCapital,
    plan_count: plan.length,
    actual_count: actual.length,
    cb_count: cb.length,
    danger_count: count('danger'),
    warn_count: count('warn'),
    info_count: count('info'),
  };
}