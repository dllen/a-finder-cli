# 按初始资金生成交易计划（资金档位切换）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把纸面交易计划的「固定 200 股」改为按初始资金（5W/10W/20W/30W/50W 五档）计算的真股数，页面可切换档位查看对应计划。

**Architecture:** 新增纯函数 `size_shares(capital, size_pct, price)` 作为股数单一事实来源；`build_plan` 读 `params["capital"]` 算出股数并驱动纸面成交；`insert_trade_plan` 由 `INSERT OR IGNORE` 改 upsert 保证换资金重建不陈旧；前端在 `PLAN_SCRIPT` 内用 JS 镜像 `sizeShares` 做纯展示重算（静态 `site/plan.html` 同逻辑，`export_json.py` 不改）。

**Tech Stack:** Python（Flask、sqlite3、pytest）、原生 JS + jQuery + Bootstrap 5（CDN）。测试用 pytest。

**Spec:** `docs/superpowers/specs/2026-09-02-capital-tier-trading-plan-design.md`

---

## 文件结构

- `shared_lib/strategy.py` — 新增 `LOT_SIZE` 常量 + `size_shares()` 纯函数。
- `config.py` — 新增 `CAPITAL_TIERS`、`DEFAULT_CAPITAL`。
- `plan_builder.py` — `build_plan` 读 `capital`；`_build_buy_rows` 用 `size_shares`；`_paper_trade` 跳过 0 股。
- `db_repository.py` — `insert_trade_plan` 改 upsert。
- `app.py` — `_start_plan_job` 加 `capital` 参数；`POST /api/plan/build` 校验/透传；`PLAN_BODY` 加切换器；`PLAN_SCRIPT` 加 `sizeShares` + 汇总 + 不足标注。
- `tests/test_sizing.py`（新增）、`tests/test_share_lots.py`、`tests/test_db_repository.py`、`tests/test_integration_plan.py`、`tests/test_web_plan.py`。

---

## Task 1: `size_shares` 纯函数（TDD）

**Files:**
- Create: `tests/test_sizing.py`
- Modify: `shared_lib/strategy.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_sizing.py`：

```python
import pytest

from shared_lib.strategy import size_shares, LOT_SIZE


def test_size_shares_rounds_down_to_lot():
    # 10W * 12.5% = 12500 元，买 100 元/股 → 125 股 → 向下取整 1 手 = 100 股
    assert size_shares(100000, 0.125, 100.0) == 100


def test_size_shares_multiple_lots():
    # 10W * 12.5% = 12500 元，买 50 元/股 → 250 股 → 2 手 = 200 股
    assert size_shares(100000, 0.125, 50.0) == 200


def test_size_shares_zero_when_below_one_lot():
    # 5W * 10% = 5000 元，买 1400 元/股 → 不足一手 → 0
    assert size_shares(50000, 0.10, 1400.0) == 0


def test_size_shares_zero_on_non_positive_inputs():
    assert size_shares(0, 0.10, 100.0) == 0
    assert size_shares(100000, 0.0, 100.0) == 0
    assert size_shares(100000, 0.10, 0.0) == 0
    assert size_shares(-100000, 0.10, 100.0) == 0


def test_lot_size_is_100():
    assert LOT_SIZE == 100
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_sizing.py -v`
Expected: FAIL，`ImportError: cannot import name 'size_shares'`

- [ ] **Step 3: 实现**

在 `shared_lib/strategy.py` 顶部（`from __future__ import annotations` 之后、`import hashlib` 附近）加：

```python
LOT_SIZE = 100  # A股一手


def size_shares(capital: float, size_pct: float, price: float) -> int:
    """A股整手建仓：预算 = capital*size_pct，股数 = floor(预算/价格/100)*100。

    不足一手返回 0。前端 PLAN_SCRIPT.sizeShares 为此函数的 JS 镜像，禁止单边改动。
    """
    if capital <= 0 or size_pct <= 0 or price <= 0:
        return 0
    budget = float(capital) * float(size_pct)
    return int(budget // (price * LOT_SIZE)) * LOT_SIZE
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_sizing.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: Commit**

```bash
git add shared_lib/strategy.py tests/test_sizing.py
git commit -m "feat(plan): size_shares 纯函数——按资金/仓位/价格整手算股数"
```

---

## Task 2: 资金档位常量

**Files:**
- Modify: `config.py`

- [ ] **Step 1: 加常量**

在 `config.py` 末尾追加：

```python
# Initial-capital tiers for the daily plan (元). Default active capital = 10W.
CAPITAL_TIERS = [50000, 100000, 200000, 300000, 500000]
DEFAULT_CAPITAL = 100000
```

- [ ] **Step 2: 验证导入**

Run: `uv run python -c "from config import CAPITAL_TIERS, DEFAULT_CAPITAL; assert len(CAPITAL_TIERS) == 5 and DEFAULT_CAPITAL == 100000; print('ok')"`
Expected: 输出 `ok`

- [ ] **Step 3: Commit**

```bash
git add config.py
git commit -m "feat(config): 资金档位常量 CAPITAL_TIERS + DEFAULT_CAPITAL(10W)"
```

---

## Task 3: plan_builder 端到端接入资金（TDD）

**Files:**
- Modify: `plan_builder.py`
- Modify: `tests/test_share_lots.py`
- Modify: `tests/test_integration_plan.py`

- [ ] **Step 1: 更新既有测试断言到新语义**

`tests/test_share_lots.py` 的 `test_build_plan_buys_200_shares_and_accumulates_next_day`（默认 10W、BULL、score=2.0 → `position_size=0.125`，buy 价 100 → 每手 100 股）：

把这两处断言改掉（原 `400` → `200`、两个 `200` → `100`）：

```python
        assert len(opens) == 1
        assert opens[0][0] == 200  # 累积 100 + 100 股
        # 加权均价 = (100*100.1 + 100*110.11)/200 ≈ 105.1（滑点 0.1%）
        assert 104.0 < opens[0][1] < 106.0
        evts = conn.execute(
            "SELECT plan_date, shares FROM trade_events WHERE event_type='open' ORDER BY plan_date"
        ).fetchall()
        assert evts == [("2026-08-18", 100), ("2026-08-19", 100)]
```

`test_build_plan_same_day_rebuild_does_not_double_buy` 里的 `assert shares == 200` → `assert shares == 100`（函数名里的 `200` 一并改为 `_100`，或保留函数名、仅改断言——为最小改动，仅改断言值与注释）。

`tests/test_integration_plan.py` 的 `test_held_code_rebought_accumulates_shares`（600519 存量 200 股 + 今日 buy 价 100 → re-buy 100 股）：

```python
    # Accumulated: original 200 + re-buy 100 = 300 shares
    assert open_rows[0][2] == 300
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_share_lots.py tests/test_integration_plan.py -v`
Expected: FAIL（仍输出 200/400 股）

- [ ] **Step 3: 加新端到端测试**

在 `tests/test_share_lots.py` 末尾追加（复用已有 `_seed_pick` 与 `open_db`）：

```python
def test_build_plan_sizes_shares_by_capital():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = open_db(path)
    _seed_pick(conn, "2026-08-18", "600519", buy=100.0, score=2.0)
    conn.close()
    # 5W：50000*0.125=6250 元 → 62 股 → 不足一手 → 0（不建仓）
    r_small = build_plan("2026-08-18", path, params={"regime": "BULL", "capital": 50000})
    # 50W：500000*0.125=62500 元 → 625 股 → 6 手 = 600 股
    r_big = build_plan("2026-08-18", path, params={"regime": "BULL", "capital": 500000})

    buy_small = [r for r in r_small.rows if r.action == "buy"]
    buy_big = [r for r in r_big.rows if r.action == "buy"]
    assert buy_small[0].shares == 0
    assert buy_big[0].shares == 600

    conn = open_db(path)
    try:
        opens = conn.execute(
            "SELECT shares FROM open_positions WHERE code='600519' AND status='open'"
        ).fetchall()
    finally:
        conn.close()
    # 第一次 0 股不建仓，第二次 600 股建仓 → 仅一笔 600
    assert [o[0] for o in opens] == [600]
```

- [ ] **Step 4: 运行确认失败**

Run: `uv run pytest tests/test_share_lots.py::test_build_plan_sizes_shares_by_capital -v`
Expected: FAIL（`shares == 0` / `600` 不成立，仍为 200）

- [ ] **Step 5: 实现 plan_builder**

`plan_builder.py` 三处改动：

(1) 顶部 import 加 `DEFAULT_CAPITAL`（在 `from market_regime import RegimeType` 附近）：

```python
from config import DEFAULT_CAPITAL
```

(2) `_build_buy_rows` 签名把未使用的 `signal_strength_max` 换成 `capital`，并改 `shares`：

```python
def _build_buy_rows(
    picks: List[Dict[str, Any]],
    regime: RegimeType,
    risk_manager: RiskManager,
    capital: float,
) -> List[PlanRow]:
    """Convert each daily_picks row into a PlanRow(action='buy').

    Plan price = picks.buy. shares = size_shares(capital, size_pct, plan_price)
    （A股整手，不足一手 0）。
    """
    rows: List[PlanRow] = []
    for p in picks:
        plan_price = float(p.get("buy") or p.get("target") or 0.0)
        if plan_price <= 0:
            continue  # no usable price → skip; not a sanity failure
        score = float(p.get("score") or 0.0)
        strength = _signal_strength(score)
        cfg = risk_manager.get_config(regime, strength)
        stop, tp = compute_plan_prices(plan_price, cfg)
        risk = plan_price - stop
        rr = (tp - plan_price) / risk if risk > 0 else 0.0
        shares = size_shares(capital, cfg.position_size, plan_price)
        rows.append(PlanRow(
            code=str(p["code"]),
            action="buy",
            plan_price=plan_price,
            size_pct=cfg.position_size,
            stop_price=stop,
            tp_price=tp,
            rr_ratio=round(rr, 4),
            shares=shares,
            rationale={
                "score": score,
                "regime": regime.value,
                "signal_strength": round(strength, 4),
                "stop_loss_pct": cfg.stop_loss_pct,
                "profit_target_pct": cfg.profit_target_pct,
                "strategy": p.get("strategy"),
            },
            status="ok",
            reason="",
        ))
    return rows
```

并把顶部 `from shared_lib.strategy import (PlanRow, compute_plan_prices, params_hash)` 追加 `size_shares`：

```python
from shared_lib.strategy import (
    PlanRow,
    compute_plan_prices,
    params_hash,
    size_shares,
)
```

(3) `build_plan` 里读 `capital` 并把调用改传 `capital`（在 `params = params or {}` 之后那一段）：

```python
    params = params or {}
    max_single = float(params.get("max_single", 0.15))
    max_total = float(params.get("max_total", 0.95))
    regime = _regime_from_str(params.get("regime", "SIDEWAYS"))
    capital = float(params.get("capital") or DEFAULT_CAPITAL)
    risk_manager = RiskManager()
    phash = params_hash(params)
```

并把 `rows.extend(_build_buy_rows(picks, regime, risk_manager))` 改为：

```python
    rows.extend(_build_buy_rows(picks, regime, risk_manager, capital))
```

(4) `_paper_trade` buy 分支开头跳过 0 股（在 `if r.action == "buy" and r.status == "ok":` 之后立即加）：

```python
            if r.action == "buy" and r.status == "ok":
                if r.shares <= 0:
                    continue  # 资金不足一手，不建仓
```

- [ ] **Step 6: 运行确认通过**

Run: `uv run pytest tests/test_share_lots.py tests/test_integration_plan.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add plan_builder.py tests/test_share_lots.py tests/test_integration_plan.py
git commit -m "feat(plan): build_plan 按资金算股数并跳过 0 股建仓（端到端）"
```

---

## Task 4: insert_trade_plan 改 upsert（TDD）

**Files:**
- Modify: `db_repository.py`
- Modify: `tests/test_db_repository.py`

- [ ] **Step 1: 改写测试到 upsert 语义**

把 `tests/test_db_repository.py` 的 `test_trade_plan_idempotent_via_insert_ignore`（第 31-44 行）整段替换为：

```python
def test_trade_plan_upsert_updates_shares():
    """UNIQUE(plan_date, code, action) + upsert：二次插入更新 shares，行数仍为 1。"""
    from db_repository import insert_trade_plan, get_trade_plan_by_date
    conn = _conn()
    try:
        r1 = PlanRow("600519", "buy", 100.0, 0.1, 95.0, 110.0, 2.0, {}, "ok", "", shares=100)
        r2 = PlanRow("600519", "buy", 100.0, 0.1, 95.0, 110.0, 2.0, {}, "ok", "", shares=600)
        first_id = insert_trade_plan(conn, r1, "2026-08-18", "abc")
        second_id = insert_trade_plan(conn, r2, "2026-08-18", "abc")
        assert first_id > 0
        assert second_id > 0  # upsert，非 0
        rows = get_trade_plan_by_date(conn, "2026-08-18")
        assert len(rows) == 1
        assert rows[0]["shares"] == 600  # 被第二次覆盖
    finally:
        conn.close()
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_db_repository.py::test_trade_plan_upsert_updates_shares -v`
Expected: FAIL（`second_id == 0`，且 shares 仍 100）

- [ ] **Step 3: 实现 upsert**

把 `db_repository.py` 的 `insert_trade_plan` 里 `INSERT OR IGNORE INTO trade_plan` 改为：

```python
    cur = conn.execute(
        """INSERT INTO trade_plan
        (plan_date, code, action, plan_price, size_pct, stop_price, tp_price,
         rr_ratio, status, reason, rationale_json, params_hash, created_at, shares)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(plan_date, code, action) DO UPDATE SET
         plan_price=excluded.plan_price,
         size_pct=excluded.size_pct,
         stop_price=excluded.stop_price,
         tp_price=excluded.tp_price,
         rr_ratio=excluded.rr_ratio,
         status=excluded.status,
         reason=excluded.reason,
         rationale_json=excluded.rationale_json,
         params_hash=excluded.params_hash,
         shares=excluded.shares""",
        (
            plan_date, row.code, row.action, row.plan_price, row.size_pct,
            row.stop_price, row.tp_price, row.rr_ratio, row.status, row.reason,
            json.dumps(row.rationale), params_hash,
            dt.datetime.utcnow().isoformat(timespec="seconds"),
            row.shares,
        ),
    )
```

（`created_at` 不参与 UPDATE；`return` 语句保留原样 `cur.lastrowid if cur.rowcount > 0 else 0`，upsert 下两者均 >0。）

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_db_repository.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add db_repository.py tests/test_db_repository.py
git commit -m "fix(plan): insert_trade_plan 改 upsert，换资金重建覆盖 shares 不陈旧"
```

---

## Task 5: 后端 API 接收资金（TDD）

**Files:**
- Modify: `app.py`
- Modify: `tests/test_web_plan.py`

- [ ] **Step 1: 写失败测试**

`tests/test_web_plan.py` 末尾追加（monkeypatch `_start_plan_job` 捕获 capital，避免异步）：

```python
def test_api_plan_build_passes_capital(plan_db, monkeypatch):
    import app as app_module

    captured = {}
    def fake_start(db_path, plan_date, capital):
        captured["capital"] = capital
        return "job-1"

    monkeypatch.setattr(app_module, "_start_plan_job", fake_start)
    app = create_app(db_path=plan_db)
    app.config["TESTING"] = True
    client = app.test_client()

    resp = client.post("/api/plan/build",
                       data=json.dumps({"plan_date": "2026-08-18", "capital": 500000}),
                       content_type="application/json")
    assert resp.status_code == 202
    assert captured["capital"] == 500000

    # 非法档位回退默认 10W
    client.post("/api/plan/build",
                data=json.dumps({"plan_date": "2026-08-18", "capital": 999999}),
                content_type="application/json")
    assert captured["capital"] == 100000
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_web_plan.py::test_api_plan_build_passes_capital -v`
Expected: FAIL（`_start_plan_job` 不接受 `capital` 参数 → TypeError）

- [ ] **Step 3: 实现**

`app.py` 三处：

(1) 顶部 `from pick_history import run_picks` 之后加：

```python
from config import CAPITAL_TIERS, DEFAULT_CAPITAL
```

(2) `_start_plan_job` 签名与 `params`（第 721 行、第 746-751 行）：

```python
def _start_plan_job(db_path, plan_date, capital):
    from config import MAX_SINGLE, MAX_TOTAL, RR_TARGET, SLIPPAGE
    ...
            params = {
                "max_single": MAX_SINGLE,
                "max_total": MAX_TOTAL,
                "rr_target": RR_TARGET,
                "regime": "sideways",
                "capital": capital,
            }
```

(3) `plan_build` 路由（第 906-915 行）：

```python
    @app.post("/api/plan/build")
    def plan_build():
        body = request.get_json(silent=True) or {}
        plan_date = (body.get("plan_date") or "").strip()
        if not plan_date:
            return jsonify({"error": "plan_date 必填"}), 400
        capital = body.get("capital")
        try:
            capital = int(capital)
        except (TypeError, ValueError):
            capital = DEFAULT_CAPITAL
        if capital not in CAPITAL_TIERS:
            capital = DEFAULT_CAPITAL
        job_id = _start_plan_job(db_path, plan_date, capital)
        if job_id is None:
            return jsonify({"error": "已有任务进行中"}), 409
        return jsonify({"job_id": job_id}), 202
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_web_plan.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_web_plan.py
git commit -m "feat(web): POST /api/plan/build 接收 capital（校验档位，非法回退默认）"
```

---

## Task 6: 前端资金档位切换器 + 展示重算

**Files:**
- Modify: `app.py`（`PLAN_BODY` + `PLAN_SCRIPT`，纯字符串改动）

- [ ] **Step 1: PLAN_BODY 加切换器**

在 `PLAN_BODY` 的 `.filter-toolbar` 里、日期 `.filter-group` 之后插入（第 489 行 `</div>` 后、`<input id="q" ...>` 前）：

```html
    <div class="filter-group">
      <label class="form-label" for="capital-group">资金</label>
      <div id="capital-group" class="btn-group btn-group-sm" role="group" aria-label="资金档位">
        <button type="button" class="btn btn-outline-primary capital-btn" data-capital="50000">5W</button>
        <button type="button" class="btn btn-outline-primary capital-btn" data-capital="100000">10W</button>
        <button type="button" class="btn btn-outline-primary capital-btn" data-capital="200000">20W</button>
        <button type="button" class="btn btn-outline-primary capital-btn" data-capital="300000">30W</button>
        <button type="button" class="btn btn-outline-primary capital-btn" data-capital="500000">50W</button>
      </div>
    </div>
```

（不加 `write-control`，故静态导出 `plan.html` 保留此切换器。）

- [ ] **Step 2: PLAN_SCRIPT 加常量与 sizeShares**

`PLAN_SCRIPT` 首行 `var PLAN_STATE = { data: null, filter: '' };` 改为：

```js
var PLAN_STATE = { data: null, filter: '', capital: 100000 };
var CAPITAL_TIERS = [50000, 100000, 200000, 300000, 500000]; // 与 config.py CAPITAL_TIERS 同步
var CAPITAL_LABELS = {50000:'5W',100000:'10W',200000:'20W',300000:'30W',500000:'50W'};
function sizeShares(capital, sizePct, price){ // 与 shared_lib/strategy.py size_shares 同式
  if (!(capital > 0) || !(sizePct > 0) || !(price > 0)) return 0;
  return Math.floor(capital * sizePct / (price * 100)) * 100;
}
function rowShares(r){
  if (r.action !== 'buy') return (r.shares == null ? 0 : r.shares);
  return sizeShares(PLAN_STATE.capital, r.size_pct, r.plan_price);
}
function insufficientLot(r){
  return r.action === 'buy' && (r.size_pct || 0) > 0 && rowShares(r) === 0;
}
```

- [ ] **Step 3: 桌面表 `row()` 用计算股数**

把 `row()` 里两行（`var shares = r.shares == null ? '—' : r.shares;` 及 `<td class="num">'+shares+'</td>`）改为：

```js
  var sharesHtml = insufficientLot(r) ? '0 <span class="text-warning small">资金不足一手</span>' : rowShares(r);
```

并把 `<td class="num">'+shares+'</td>` 改为 `<td class="num">'+sharesHtml+'</td>`。

- [ ] **Step 4: 移动端卡片股数**

移动端卡片（`drawPlan` 内 `.plan-card`）的股数行：

原 `'<span class="k">股数</span><span class="v num">'+(r.shares==null?'—':r.shares)+'</span>'+`

改为 `'<span class="k">股数</span><span class="v num">'+(insufficientLot(r)?'0 <span class="text-warning small">资金不足一手</span>':rowShares(r))+'</span>'+`

- [ ] **Step 5: 汇总条加资金信息**

`drawPlan` 里汇总计算与 `summary` 字符串（当前 `var groups = ...; var buySize = 0, buyShares = 0, failed = 0; ...` 那段及 `var summary = ...`）整体替换为：

```js
    var groups = {buy:[], hold:[], exit:[]};
    var buySize = 0, buyShares = 0, usedCapital = 0, failed = 0;
    rows.forEach(function(r){
      (groups[r.action] || (groups[r.action]=[])).push(r);
      if (r.action === 'buy' && r.status === 'ok' && r.size_pct != null) {
        buySize += r.size_pct;
        var sh = rowShares(r);
        buyShares += sh;
        usedCapital += sh * (r.plan_price || 0);
      }
      if (r.status === 'failed') failed++;
    });
    var cash = PLAN_STATE.capital - usedCapital;
    var util = PLAN_STATE.capital > 0 ? usedCapital / PLAN_STATE.capital : 0;
    var capLabel = CAPITAL_LABELS[PLAN_STATE.capital] || (PLAN_STATE.capital/10000 + 'W');
    var counts = Object.entries(groups).filter(function(e){return e[1].length}).map(function(e){return actionMeta(e[0]).label+' '+e[1].length;}).join(' · ');
    var summary = '<div class="card summary-card mb-3"><div class="card-body py-2">' +
      '<span class="me-3"><strong>'+data.plan_date+'</strong></span>' +
      '<span class="text-muted me-3">'+rows.length+' 行</span>' +
      '<span class="text-muted me-3">'+counts+'</span>' +
      '<span class="me-3">资金 '+capLabel+'</span>' +
      '<span class="me-3 '+(util>1?'text-danger':'text-muted')+'">已用 ¥'+fmt(usedCapital)+' ('+(util*100).toFixed(1)+'%)</span>' +
      '<span class="me-3 '+(cash<0?'text-danger':'text-muted')+'">现金 ¥'+fmt(cash)+'</span>' +
      '<span class="text-muted me-3">买入合计仓位 '+ (buySize*100).toFixed(1) +'% · '+ buyShares +' 股</span>' +
      '<span class="text-muted">失败 '+failed+'</span>' +
      '</div></div>';
```

- [ ] **Step 6: 绑定切换器 + build 带资金**

(1) `buildPlan()` 里 `data: JSON.stringify({plan_date: date})` 改为：

```js
    data: JSON.stringify({plan_date: date, capital: PLAN_STATE.capital}),
```

(2) `$(function(){ ... })` 里，`$('#btn-build').on('click', buildPlan);` 之后加：

```js
  $('#capital-group .capital-btn').on('click', function(){
    var c = parseInt($(this).attr('data-capital'), 10);
    if (PLAN_STATE.capital === c) return;
    PLAN_STATE.capital = c;
    $('#capital-group .capital-btn').removeClass('btn-primary').addClass('btn-outline-primary');
    $(this).removeClass('btn-outline-primary').addClass('btn-primary');
    if (PLAN_STATE.data) drawPlan(PLAN_STATE.data);
  });
  $('#capital-group .capital-btn[data-capital="'+PLAN_STATE.capital+'"]')
    .removeClass('btn-outline-primary').addClass('btn-primary');
```

- [ ] **Step 7: 语法/渲染冒烟**

Run: `uv run python -c "import app; print('PLAN_SCRIPT ok')"`
Expected: 输出 `PLAN_SCRIPT ok`（确认 `app.py` 无语法错误）。

再本地起服务目测（可选，非门禁）：`bash run_web.sh` 打开 `http://127.0.0.1:8000/plan`，点 5W/10W/50W 看股数变化与「资金不足一手」标注。

- [ ] **Step 8: Commit**

```bash
git add app.py
git commit -m "feat(web): 交易计划页资金档位切换器 + 前端整手重算与汇总"
```

---

## Task 7: 全量回归 + 收尾

**Files:** 无新文件（仅验证）

- [ ] **Step 1: 全量测试**

Run: `uv run pytest -q`
Expected: 全绿（此前 ~145 tests，本轮新增/改写后应全 pass）。若有残留 200 股断言失败，按 Task 3 Step 1 的口径逐一改为资金感知预期（每只买入股数 = `size_shares(DEFAULT_CAPITAL, position_size, buy_price)` 向下取整手）。

- [ ] **Step 2: 静态导出冒烟（不提交 site/，已被 gitignore）**

Run: `uv run python export_json.py --db hs300.db --out site`
Expected: 输出 `导出完成：... → site`；`site/plan.html` 含 `capital-group` 与 `sizeShares`，`site/data/plan-<date>.json` 结构不变。

- [ ] **Step 3: Commit（如有残余改动）**

```bash
git status --short
# 仅当有未提交的测试/代码改动时：
git add -A && git commit -m "test(plan): 资金感知股数断言回归收尾"
```

---

## 自检清单（实现后逐条核对）

- [ ] `size_shares` 与前端 `sizeShares` 同式（整手向下取整、非正返回 0）。
- [ ] `build_plan` 读 `params["capital"]`，缺省 `DEFAULT_CAPITAL`，`params_hash` 含 capital。
- [ ] `_paper_trade` 跳过 `shares <= 0` 买单。
- [ ] `insert_trade_plan` 为 upsert，换资金重建 `shares` 覆盖不陈旧。
- [ ] `POST /api/plan/build` 校验 `capital` 落在 `CAPITAL_TIERS`，非法回退默认。
- [ ] 计划页切换档位仅重绘（不发请求/不建仓）；「生成 plan」随选中档位建仓。
- [ ] 静态 `site/plan.html` 由 `export_json.py` 生成，含切换器且无写入按钮。
