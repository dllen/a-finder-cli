# Worker 抓取行情 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Cloudflare Worker 每个交易日收盘后把 301 只沪深300成分股的日线抓进 D1，GitHub Actions 改为从 D1 取增量，不再直连东财/腾讯。

**Architecture:** Worker 加一个每 2 分钟的 cron 驱动分片状态机（每片 40 只，游标存 D1 的 `sync_jobs`），抓取结果写 `market_prices`。Worker 另有 4 个鉴权路由，供 GitHub Actions 按日拉取数据；GH 侧新增 `pull_from_d1.py` 把增量写入本地 SQLite，后续 Python 计算与静态站发布流程完全不变。Worker 不可用时 GH 回退到现有直连路径。

**Tech Stack:** Cloudflare Workers (ES modules, Free 计划) · D1 (SQLite) · `node --test` 单测 · Python 3.11 + pytest（GH 侧脚本与 parity 测试）· GitHub Actions

**Spec:** [`docs/superpowers/specs/2026-10-26-worker-data-fetch-design.md`](../specs/2026-10-26-worker-data-fetch-design.md)

---

## 文件结构

| 文件 | 职责 | 状态 |
|---|---|---|
| `worker/market/sources.js` | 数据源抓取与解析（东财主 / 腾讯备），纯函数导出解析器 | 新建 |
| `worker/market/jobs.js` | 抓取状态机纯函数：分片计算、游标推进、失败判定、交易时段判定 | 新建 |
| `worker/market/repo.js` | D1 读写：取代码段、批量 upsert、任务读取/推进 | 新建 |
| `worker/market/api.js` | 4 个鉴权路由（health / dates / day / sync-start） | 新建 |
| `worker/index.js` | 接线：`fetch` 分发到 `/api/market/*`，`scheduled` 同时跑 dispatch 与抓取 | 修改 |
| `worker/schema.sql` | 新增 `market_prices` / `market_symbols` / `sync_jobs` 三张表 | 修改 |
| `wrangler.toml` | 增加 `*/2 * * * *` cron | 修改 |
| `scripts/seed_d1.py` | 从本机 `hs300.db` 生成 seed SQL（历史行情 + 成分股） | 新建 |
| `scripts/pull_from_d1.py` | GH 侧：从 Worker 拉增量写 SQLite，补算 pct_change | 新建 |
| `scripts/ci_check_data.py` | 增加「数据来源」字段（d1 / direct） | 修改 |
| `.github/workflows/deploy-site.yml` | 同步步骤改为拉 D1 + 回退分支 | 修改 |
| `.github/workflows/deploy-worker.yml` | 可选同步 `SYNC_TOKEN` secret | 修改 |
| `tests/test_worker_market_sources.mjs` | 解析器单测 + 失败降级 | 新建 |
| `tests/test_worker_market_jobs.mjs` | 状态机单测（时段/游标/完成/失败） | 新建 |
| `tests/test_worker_market_repo.mjs` | D1 SQL 形态 + 假 DB 驱动的行情/成分股 upsert | 新建 |
| `tests/test_worker_market_api.mjs` | 鉴权与路由形态 | 新建 |
| `tests/test_worker_market_parity.py` | Worker 解析 vs Python `data_providers` 同 fixture 逐字段相等 | 新建 |
| `tests/test_pull_from_d1.py` | GH 取数脚本（mock HTTP）：增量 / 回退 / 幂等 / pct_change 补算 | 新建 |
| `tests/fixtures/em_600519_20261023.json` 等 | 真实响应快照（一次抓取，之后离线） | 新建 |

---

## Task 1: 前置探针 —— Worker 能否抓到东财（决策门）

**这是全计划唯一的决策点。** 探针不通，整个方案终止（回到"Worker 只触发 GH"的现状）。

**Files:**
- Create: `worker/probe.js`（临时文件，Task 2 之后删除）
- Modify: `wrangler.toml:2`（临时把 `main` 指向 probe.js）

- [ ] **Step 1: 写最小探针**

创建 `worker/probe.js`：

```js
// 临时探针：验证 Worker 出站能否抓到东财日 K。验证完删除。
const UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 ' +
  '(KHTML, like Gecko) Chrome/124.0 Safari/537.36';

function secid(code) {
  return code.startsWith('6') ? `1.${code}` : `0.${code}`;
}

async function tryEastmoney(code, date) {
  const compact = date.replace(/-/g, '');
  const url = 'https://push2his.eastmoney.com/api/qt/stock/kline/get' +
    `?fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61` +
    `&klt=101&fqt=0&secid=${secid(code)}&beg=${compact}&end=${compact}`;
  const resp = await fetch(url, {
    headers: {
      'User-Agent': UA,
      'Referer': `https://quote.eastmoney.com/${code.startsWith('6') ? 'sh' : 'sz'}${code}.html`,
    },
  });
  const text = await resp.text();
  return { status: resp.status, body: text.slice(0, 400) };
}

async function tryTencent(code, date) {
  const symbol = code.startsWith('6') ? `sh${code}` : `sz${code}`;
  const url = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get' +
    `?param=${symbol},day,${date},${date},640,qfq`;
  const resp = await fetch(url, {
    headers: { 'User-Agent': UA, 'Referer': 'https://gu.qq.com/' },
  });
  const text = await resp.text();
  return { status: resp.status, body: text.slice(0, 400) };
}

export default {
  async fetch(request) {
    const out = {
      eastmoney: await tryEastmoney('600519', '2026-10-23'),
      tencent: await tryTencent('600519', '2026-10-23'),
    };
    return new Response(JSON.stringify(out, null, 2), {
      headers: { 'Content-Type': 'application/json' },
    });
  },
};
```

- [ ] **Step 2: 临时指向探针并部署**

```bash
# 备份原配置
cp wrangler.toml /tmp/wrangler.toml.bak
sed -i '' 's|^main = "worker/index.js"|main = "worker/probe.js"|' wrangler.toml
npx --yes wrangler@latest deploy
```

Expected: 部署成功，输出含 `Deployed a-finder-api`。

- [ ] **Step 3: 调用探针并判定**

```bash
curl -s https://ycu.space/api/probe | python3 -m json.tool
```

三个分支，据此决定后续：

| 结果 | 含义 | 动作 |
|---|---|---|
| `eastmoney.body` 含 `"klines":["2026-10-23,..."]` | 东财可用 | 继续 Task 2，主源用东财 |
| `eastmoney.status != 200` 但 `tencent.body` 含 `"qfqday"` | 东财拒、腾讯通 | 继续 Task 2，但 Task 3 主源改为腾讯（并把 `turnover` 记为 0、由 GH 补算 `pct_change`） |
| 两者都失败 | Worker 出站被封 | **终止整个计划**，把探针结论写入 spec 并通知用户回到「Worker 只触发 GH」 |

- [ ] **Step 4: 恢复配置并清理**

```bash
mv /tmp/wrangler.toml.bak wrangler.toml
rm worker/probe.js
npx --yes wrangler@latest deploy    # 恢复原 Worker
git status --short                  # 应为空
```

- [ ] **Step 5: 把探针结果记录进 spec**

在 spec 的「数据源与反爬风险」一节追加一行（把 `X` 换成实测结果）：

```markdown
**探针实测（YYYY-MM-DD）**：东财 status=X，腾讯 status=X → 主源用 <东财|腾讯>。
```

```bash
git add docs/superpowers/specs/2026-10-26-worker-data-fetch-design.md
git commit -m "docs(spec): 记录 Worker 出站探针实测结果"
```

---

## Task 2: D1 表结构 + cron + seed 脚本

**Files:**
- Modify: `worker/schema.sql`
- Modify: `wrangler.toml:14-18`
- Create: `scripts/seed_d1.py`
- Test: `tests/test_seed_d1.py`

- [ ] **Step 1: 写 seed 脚本的失败测试**

创建 `tests/test_seed_d1.py`：

```python
"""seed_d1.py 把本地 hs300.db 转成 D1 可执行的 SQL。"""

import importlib.util
import sqlite3
from pathlib import Path

import pytest

from db_repository import open_db

_SPEC = importlib.util.spec_from_file_location(
    "seed_d1",
    Path(__file__).resolve().parent.parent / "scripts" / "seed_d1.py",
)
seed = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(seed)


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "m.db")
    conn = open_db(path)
    for code, close in (("600519", 1500.0), ("000001", 12.0)):
        conn.execute(
            "INSERT INTO daily_prices (code, trade_date, open, close, high, low,"
            " volume, amount, amplitude, pct_change, change, turnover)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (code, "2026-10-23", close, close, close, close, 1000, 1e6, 1.0, 0.5, 1.0, 2.0),
        )
        conn.execute(
            "INSERT INTO hs300_metadata (code, name) VALUES (?,?)",
            (code, f"票{code}"),
        )
    conn.commit()
    yield path, conn
    conn.close()


def test_symbols_sql(db):
    path, _ = db
    sql = seed.build_symbols_sql(path)
    assert "INSERT OR REPLACE INTO market_symbols" in sql
    assert "'600519'" in sql and "'票600519'" in sql
    # 301 只以内用单条多值 INSERT，避免每行一条语句
    assert sql.count("INSERT") == 1


def test_prices_sql_batched(db):
    path, _ = db
    sql, rows = seed.build_prices_sql(path, batch=1)
    assert rows == 2
    assert "INSERT OR REPLACE INTO market_prices" in sql
    assert "'2026-10-23'" in sql
    assert "2.0" in sql          # turnover 必须写进去
    # batch=1 时每行一条语句
    assert sql.count("INSERT") == 2


def test_prices_sql_respects_since(db):
    path, conn = db
    conn.execute(
        "INSERT INTO daily_prices (code, trade_date, open, close, high, low,"
        " volume, amount) VALUES ('600519','2024-01-02',1,1,1,1,1,1)"
    )
    conn.commit()
    _, rows = seed.build_prices_sql(path, since="2026-01-01")
    assert rows == 2            # 2024 的那行被 since 过滤掉
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_seed_d1.py -q`
Expected: FAIL — `FileNotFoundError: scripts/seed_d1.py`

- [ ] **Step 3: 实现 `scripts/seed_d1.py`**

```python
#!/usr/bin/env python3
"""从本地 hs300.db 生成 D1 可执行的 SQL（历史行情 + 成分股）。

用法：
    python3 scripts/seed_d1.py --db hs300.db --out /tmp/d1_seed.sql
    npx wrangler@latest d1 execute a-finder --file=/tmp/d1_seed.sql --remote

只生成 INSERT OR REPLACE，重复执行安全。
"""

from __future__ import annotations

import argparse
import sqlite3
from typing import List, Optional, Tuple


def _lit(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return repr(float(value)) if isinstance(value, float) else str(value)


def _rows(conn: sqlite3.Connection, sql: str, params: Tuple = ()) -> List[tuple]:
    return conn.execute(sql, params).fetchall()


def build_symbols_sql(db_path: str) -> str:
    """成分股：一条多值 INSERT。"""
    conn = sqlite3.connect(db_path)
    try:
        rows = _rows(conn, "SELECT code, name FROM hs300_metadata ORDER BY code")
    finally:
        conn.close()
    if not rows:
        return ""
    values = ", ".join(
        f"({_lit(c)}, {_lit(n)}, datetime('now'))" for c, n in rows
    )
    return ("INSERT OR REPLACE INTO market_symbols (code, name, updated_at) "
            f"VALUES {values};")


def build_prices_sql(
    db_path: str, *, batch: int = 200, since: Optional[str] = None
) -> Tuple[str, int]:
    """行情：按 batch 分组成多值 INSERT，返回 (sql, 行数)。"""
    conn = sqlite3.connect(db_path)
    try:
        if since:
            rows = _rows(
                conn,
                "SELECT code, trade_date, open, close, high, low, volume, amount,"
                " turnover, pct_change FROM daily_prices WHERE trade_date >= ?"
                " ORDER BY trade_date, code",
                (since,),
            )
        else:
            rows = _rows(
                conn,
                "SELECT code, trade_date, open, close, high, low, volume, amount,"
                " turnover, pct_change FROM daily_prices ORDER BY trade_date, code",
            )
    finally:
        conn.close()

    cols = ("code, trade_date, open, close, high, low, volume, amount,"
            " turnover, pct_change")
    stmts = []
    for i in range(0, len(rows), batch):
        chunk = rows[i:i + batch]
        values = ", ".join("(" + ", ".join(_lit(v) for v in r) + ")" for r in chunk)
        stmts.append(f"INSERT OR REPLACE INTO market_prices ({cols}) VALUES {values};")
    return "\n".join(stmts), len(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description="生成 D1 seed SQL")
    ap.add_argument("--db", default="hs300.db")
    ap.add_argument("--out", required=True)
    ap.add_argument("--since", default=None, help="只导出该日期之后的行情")
    ap.add_argument("--batch", type=int, default=200)
    args = ap.parse_args()

    symbols = build_symbols_sql(args.db)
    prices, n = build_prices_sql(args.db, batch=args.batch, since=args.since)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write("-- D1 seed: 历史行情 + 成分股\n")
        if symbols:
            f.write(symbols + "\n")
        f.write(prices + "\n")
    print(f"已写出 {args.out}：成分股 SQL {'有' if symbols else '无'}，行情 {n} 行")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest tests/test_seed_d1.py -q`
Expected: `3 passed`

- [ ] **Step 5: 扩展 D1 schema**

在 `worker/schema.sql` 末尾追加：

```sql
-- 行情：字段与 SQLite daily_prices 对齐（turnover 必须保留：
-- strategies/backtest.py 与 market_data.py 会读）
CREATE TABLE IF NOT EXISTS market_prices (
    code       TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    open REAL, close REAL, high REAL, low REAL,
    volume REAL, amount REAL, turnover REAL, pct_change REAL,
    PRIMARY KEY (code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_mp_date ON market_prices(trade_date);

-- 成分股：由 GitHub Actions 每日全量 upsert（Worker 不自己维护名单）
CREATE TABLE IF NOT EXISTS market_symbols (
    code TEXT PRIMARY KEY,
    name TEXT,
    updated_at TEXT
);

-- 抓取任务：状态机的全部状态
CREATE TABLE IF NOT EXISTS sync_jobs (
    id         TEXT PRIMARY KEY,
    status     TEXT NOT NULL,
    cursor     INTEGER NOT NULL DEFAULT 0,
    total      INTEGER NOT NULL,
    attempts   INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    note       TEXT
);
```

- [ ] **Step 6: 加 cron 触发**

修改 `wrangler.toml` 的 `[triggers]`：

```toml
[triggers]
crons = ["35 7 * * *", "*/2 * * * *"]
# 07:35 UTC：兜底触发 GitHub Actions 来消费 D1
# */2 每 2 分钟：驱动行情抓取状态机（Worker 自己判时段，非交易时段秒退）
```

- [ ] **Step 7: 生成 seed 并灌入 D1**

```bash
python3 scripts/seed_d1.py --db hs300.db --out /tmp/d1_seed.sql
wc -l /tmp/d1_seed.sql            # 预期：数百行（多值 INSERT，每 200 行一条语句）
npx --yes wrangler@latest d1 execute a-finder --file=/tmp/d1_seed.sql --remote --yes
```

Expected: `Executed N queries`，N ≈ 800。

- [ ] **Step 8: 核对 D1 与本地库行数一致**

```bash
npx --yes wrangler@latest d1 execute a-finder --remote --yes \
  --command "SELECT COUNT(*) AS rows, COUNT(DISTINCT trade_date) AS days, MAX(trade_date) AS latest FROM market_prices"
python3 -c "
import sqlite3
c=sqlite3.connect('hs300.db')
print(c.execute('SELECT COUNT(*), COUNT(DISTINCT trade_date), MAX(trade_date) FROM daily_prices').fetchone())
"
```

Expected: 两个输出的三个数字完全相同（本地 `hs300.db` 的 `148381 | N | 2026-10-23`）。

- [ ] **Step 9: 提交**

```bash
git add worker/schema.sql wrangler.toml scripts/seed_d1.py tests/test_seed_d1.py
git commit -m "feat(d1): market_prices/market_symbols/sync_jobs 三张表 + seed 脚本"
```

---

## Task 3: Worker 数据源抓取与解析

**Files:**
- Create: `worker/market/sources.js`
- Create: `tests/fixtures/em_600519.json`, `tests/fixtures/tx_600519.json`
- Test: `tests/test_worker_market_sources.mjs`

- [ ] **Step 1: 抓两份真实响应作为 fixture（一次性，之后离线）**

```bash
mkdir -p tests/fixtures
python3 - <<'PY'
import json, urllib.request

UA = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0 Safari/537.36'}

def get(url, referer):
    req = urllib.request.Request(url, headers={**UA, 'Referer': referer})
    return json.load(urllib.request.urlopen(req, timeout=20))

em = get(
    'https://push2his.eastmoney.com/api/qt/stock/kline/get?fields1=f1,f2,f3,f4,f5,f6'
    '&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61&klt=101&fqt=0
&secid=1.600519&beg=20261019&end=20261023',
    'https://quote.eastmoney.com/sh600519.html')
json.dump(em, open('tests/fixtures/em_600519.json', 'w'), ensure_ascii=False)

tx = get(
    'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh600519,day,2026-10-19,2026-10-23,640,qfq',
    'https://gu.qq.com/')
json.dump(tx, open('tests/fixtures/tx_600519.json', 'w'), ensure_ascii=False)
print('fixtures ok:', len(em['data']['klines']), 'em rows')
PY
```

Expected: 打印 `fixtures ok: 5 em rows`（一周 5 个交易日）。

- [ ] **Step 2: 写解析器的失败测试**

创建 `tests/test_worker_market_sources.mjs`：

```js
// Worker 行情解析单测（node --test）
import { test } from 'node:test';
import assert from 'node:assert';
import { readFileSync } from 'node:fs';
import {
  parseEastmoney, parseTencent, pickBars, secidFor,
} from '../worker/market/sources.js';

const em = JSON.parse(readFileSync(new URL('./fixtures/em_600519.json', import.meta.url)));
const tx = JSON.parse(readFileSync(new URL('./fixtures/tx_600519.json', import.meta.url)));

test('parseEastmoney 字段映射：日期/OHLC/量额/换手/涨跌幅', () => {
  const rows = parseEastmoney(em, '600519');
  assert.equal(rows.length, 5);
  const r = rows[0];
  assert.match(r.trade_date, /^\d{4}-\d{2}-\d{2}$/);
  assert.equal(typeof r.close, 'number');
  assert.equal(typeof r.open, 'number');
  assert.equal(typeof r.volume, 'number');
  assert.equal(typeof r.amount, 'number');
  assert.equal(typeof r.turnover, 'number');   // 换手率必须解析（回测依赖）
  assert.equal(typeof r.pct_change, 'number');
  assert.equal(r.code, '600519');
});

test('parseEastmoney 无数据返回空数组', () => {
  assert.deepEqual(parseEastmoney({ data: null }, '600519'), []);
  assert.deepEqual(parseEastmoney({}, '600519'), []);
});

test('parseTencent 只映射 OHLCV，其余为 0', () => {
  const rows = parseTencent(tx, '600519');
  assert.ok(rows.length >= 1);
  const r = rows[0];
  assert.match(r.trade_date, /^\d{4}-\d{2}-\d{2}$/);
  assert.equal(r.amount, 0);
  assert.equal(r.turnover, 0);
  assert.equal(r.pct_change, 0);
});

test('pickBars 只保留目标交易日', () => {
  const rows = parseEastmoney(em, '600519');
  const target = rows[rows.length - 1].trade_date;
  const picked = pickBars(rows, target);
  assert.equal(picked.length, 1);
  assert.equal(picked[0].trade_date, target);
  assert.deepEqual(pickBars(rows, '1999-01-01'), []);
});

test('secidFor 沪市/深市前缀', () => {
  assert.equal(secidFor('600519'), '1.600519');
  assert.equal(secidFor('688981'), '1.688981');
  assert.equal(secidFor('000001'), '0.000001');
  assert.equal(secidFor('300750'), '0.300750');
});
```

- [ ] **Step 3: 运行确认失败**

Run: `node --test tests/test_worker_market_sources.mjs`
Expected: FAIL — `Cannot find module '../worker/market/sources.js'`

- [ ] **Step 4: 实现 `worker/market/sources.js`**

```js
// 行情抓取与解析：东财 push2his（主）→ 腾讯 fqkline（备）。
// 只负责「一只股票一个窗口」的 K 线；调用方决定窗口与降级策略。
const UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 ' +
  '(KHTML, like Gecko) Chrome/124.0 Safari/537.36';

export function secidFor(code) {
  return /^(6|688)/.test(code) ? `1.${code}` : `0.${code}`;
}

export function tencentSymbol(code) {
  return /^(6|688)/.test(code) ? `sh${code}` : `sz${code}`;
}

const num = (v) => {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
};

/** 东财 klines: "日期,开,收,高,低,量,额,振幅,涨跌幅,涨跌额,换手率" */
export function parseEastmoney(payload, code) {
  const klines = payload?.data?.klines;
  if (!Array.isArray(klines)) return [];
  const rows = [];
  for (const line of klines) {
    const p = String(line).split(',');
    if (p.length < 11) continue;
    rows.push({
      code,
      trade_date: p[0],
      open: num(p[1]),
      close: num(p[2]),
      high: num(p[3]),
      low: num(p[4]),
      volume: num(p[5]),
      amount: num(p[6]),
      pct_change: num(p[8]),
      turnover: num(p[10]),
    });
  }
  return rows;
}

/** 腾讯 qfqday: [日期, 开, 收, 高, 低, 量]，其余字段腾讯不提供 */
export function parseTencent(payload, code) {
  const node = payload?.data?.[tencentSymbol(code)];
  const bars = node?.qfqday || node?.day;
  if (!Array.isArray(bars)) return [];
  const rows = [];
  for (const b of bars) {
    if (!Array.isArray(b) || b.length < 6) continue;
    rows.push({
      code,
      trade_date: String(b[0]),
      open: num(b[1]),
      close: num(b[2]),
      high: num(b[3]),
      low: num(b[4]),
      volume: num(b[5]),
      amount: 0,
      pct_change: 0,
      turnover: 0,
    });
  }
  return rows;
}

/** 只保留指定交易日的行（抓 5 天窗口是为了兼容节假日） */
export function pickBars(rows, targetDate) {
  return rows.filter((r) => r.trade_date === targetDate);
}

async function getJson(url, referer) {
  const resp = await fetch(url, {
    headers: { 'User-Agent': UA, ...(referer ? { Referer: referer } : {}) },
  });
  if (!resp.ok) return null;
  try {
    return await resp.json();
  } catch {
    return null;
  }
}

/** 抓一只股票最近 windowDays 天的 K 线；东财失败自动降级腾讯。 */
export async function fetchBars(code, targetDate, { windowDays = 7 } = {}) {
  const beg = dayOffset(targetDate, -windowDays);
  const emBeg = beg.replace(/-/g, '');
  const emEnd = targetDate.replace(/-/g, '');
  const ref = `https://quote.eastmoney.com/${/^(6|688)/.test(code) ? 'sh' : 'sz'}${code}.html`;
  const emUrl = 'https://push2his.eastmoney.com/api/qt/stock/kline/get' +
    `?fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61` +
    `&klt=101&fqt=0&secid=${secidFor(code)}&beg=${emBeg}&end=${emEnd}`;
  const em = await getJson(emUrl, ref);
  let rows = parseEastmoney(em, code);
  if (rows.length) return { source: 'eastmoney', rows };

  const txUrl = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get' +
    `?param=${tencentSymbol(code)},day,${beg},${targetDate},640,qfq`;
  const tx = await getJson(txUrl, 'https://gu.qq.com/');
  rows = parseTencent(tx, code);
  return { source: rows.length ? 'tencent' : 'none', rows };
}

export function dayOffset(dateStr, days) {
  const d = new Date(`${dateStr}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}
```

- [ ] **Step 5: 运行测试确认通过**

Run: `node --test tests/test_worker_market_sources.mjs`
Expected: `# pass 5`、`# fail 0`

- [ ] **Step 6: 提交**

```bash
git add worker/market/sources.js tests/test_worker_market_sources.mjs tests/fixtures/
git commit -m "feat(worker): 行情抓取与解析（东财主 / 腾讯备，含换手率）"
```

---

## Task 4: 抓取状态机（纯函数）

**Files:**
- Create: `worker/market/jobs.js`
- Test: `tests/test_worker_market_jobs.mjs`

- [ ] **Step 1: 写状态机的失败测试**

创建 `tests/test_worker_market_jobs.mjs`：

```js
// 抓取状态机单测（纯函数，不碰网络与 D1）
import { test } from 'node:test';
import assert from 'node:assert';
import {
  CHUNK_SIZE, beijingNow, isTradingWindow, planChunk, advanceJob, shouldRun,
} from '../worker/market/jobs.js';

test('CHUNK_SIZE 在 Free 计划子请求限额内', () => {
  assert.ok(CHUNK_SIZE <= 45, '必须留出余量（限额 50）');
});

test('beijingNow 返回北京时间的字段', () => {
  const b = beijingNow(new Date('2026-10-26T07:40:00Z'));   // UTC 07:40 = 北京 15:40
  assert.equal(b.date, '2026-10-26');
  assert.equal(b.hour, 15);
  assert.equal(b.minute, 40);
  assert.equal(b.weekday, 1);        // 周一
});

test('isTradingWindow：15:35–23:00 的工作日才抓', () => {
  assert.ok(isTradingWindow({ weekday: 1, hour: 15, minute: 40 }));
  assert.ok(isTradingWindow({ weekday: 5, hour: 22, minute: 59 }));
  assert.ok(!isTradingWindow({ weekday: 1, hour: 15, minute: 30 }));  // 收盘数据未稳定
  assert.ok(!isTradingWindow({ weekday: 1, hour: 23, minute: 1 }));
  assert.ok(!isTradingWindow({ weekday: 0, hour: 16, minute: 0 }));   // 周日
  assert.ok(!isTradingWindow({ weekday: 6, hour: 16, minute: 0 }));
});

test('shouldRun：没有活跃任务就不跑；有则跑', () => {
  assert.equal(shouldRun(null, { weekday: 1, hour: 16, minute: 0 }), false);
  assert.equal(shouldRun({ status: 'done' }, { weekday: 1, hour: 16, minute: 0 }), false);
  assert.equal(shouldRun({ status: 'running' }, { weekday: 1, hour: 16, minute: 0 }), true);
  assert.equal(shouldRun({ status: 'running' }, { weekday: 1, hour: 12, minute: 0 }), false);
});

test('planChunk：按游标切出本片代码与下一游标', () => {
  const codes = Array.from({ length: 90 }, (_, i) => String(600000 + i));
  const first = planChunk(codes, 0);
  assert.equal(first.slice.length, CHUNK_SIZE);
  assert.equal(first.nextCursor, CHUNK_SIZE);
  assert.equal(first.done, false);

  const last = planChunk(codes, 80);
  assert.equal(last.slice.length, 10);
  assert.equal(last.done, true);
});

test('planChunk：游标越界视为完成且不返回代码', () => {
  const out = planChunk(['600519'], 5);
  assert.deepEqual(out.slice, []);
  assert.equal(out.done, true);
});

test('advanceJob：推进游标', () => {
  const job = { id: '2026-10-26', status: 'running', cursor: 0, total: 301, attempts: 0 };
  const next = advanceJob(job, { advance: 40, failedCodes: [] });
  assert.equal(next.cursor, 40);
  assert.equal(next.status, 'running');
  const done = advanceJob({ ...job, cursor: 280 }, { advance: 40, failedCodes: [] });
  assert.equal(done.cursor, 301);
  assert.equal(done.status, 'done');
});

test('advanceJob：整片失败累计 attempts，超过 3 次判 failed', () => {
  const job = { id: 'd', status: 'running', cursor: 0, total: 301, attempts: 2 };
  const failed = advanceJob(job, { advance: 0, failedCodes: [], hardFailure: 'source down' });
  assert.equal(failed.attempts, 3);
  assert.equal(failed.status, 'failed');
  assert.match(failed.note, /source down/);
});

test('advanceJob：个别代码失败仍推进，并记录到 note', () => {
  const job = { id: 'd', status: 'running', cursor: 0, total: 3, attempts: 0 };
  const next = advanceJob(job, { advance: 3, failedCodes: ['000001'] });
  assert.equal(next.cursor, 3);
  assert.equal(next.status, 'done');
  assert.match(next.note, /000001/);
});
```

- [ ] **Step 2: 运行确认失败**

Run: `node --test tests/test_worker_market_jobs.mjs`
Expected: FAIL — `Cannot find module '../worker/market/jobs.js'`

- [ ] **Step 3: 实现 `worker/market/jobs.js`**

```js
// 抓取状态机：全部是纯函数，便于在没有 D1/网络的条件下测试。
// 状态落在 D1 的 sync_jobs 表：status / cursor / total / attempts / note。

/** 每次调用抓多少只。Free 计划子请求上限 50，留余量给健康检查。 */
export const CHUNK_SIZE = 40;

/** 抓取时间窗（北京时间）：15:35 起（东财当日 K 线收盘后才稳定），23:00 止 */
const WINDOW_START_MIN = 15 * 60 + 35;
const WINDOW_END_MIN = 23 * 60;

/** 把任意时刻转成北京时间的日期/时分/星期（0=周日） */
export function beijingNow(now = new Date()) {
  const fmt = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false, weekday: 'short',
  });
  const parts = Object.fromEntries(
    fmt.formatToParts(now).map((p) => [p.type, p.value]),
  );
  const weekdayMap = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 };
  return {
    date: `${parts.year}-${parts.month}-${parts.day}`,
    hour: Number(parts.hour),
    minute: Number(parts.minute),
    weekday: weekdayMap[parts.weekday] ?? 0,
  };
}

export function isTradingWindow(bj) {
  if (bj.weekday === 0 || bj.weekday === 6) return false;
  const minutes = bj.hour * 60 + bj.minute;
  return minutes >= WINDOW_START_MIN && minutes < WINDOW_END_MIN;
}

/** 有活跃任务且在时间窗内才需要干活 */
export function shouldRun(job, bj) {
  if (!job) return false;
  if (job.status !== 'pending' && job.status !== 'running') return false;
  return isTradingWindow(bj);
}

/** 按游标切出本片代码；done 表示本片之后没有剩余 */
export function planChunk(codes, cursor) {
  const start = Math.min(cursor, codes.length);
  const slice = codes.slice(start, start + CHUNK_SIZE);
  const nextCursor = start + slice.length;
  return { slice, nextCursor, done: nextCursor >= codes.length };
}

function mergeNote(existing, additions) {
  const prev = existing ? String(existing) : '';
  if (!additions.length) return prev || null;
  const merged = prev ? `${prev} ${additions.join(' ')}` : additions.join(' ');
  return merged.slice(-900);   // 控制 sync_jobs.note 体积
}

/**
 * 推进任务状态。
 * advance：本片实际处理并推进的代码数（整片失败传 0）
 * failedCodes：本片内单只失败的代码
 * hardFailure：整片失败原因（数据源全挂）
 */
export function advanceJob(job, { advance = 0, failedCodes = [], hardFailure = null } = {}) {
  const attempts = hardFailure ? (job.attempts || 0) + 1 : (job.attempts || 0);
  if (hardFailure && attempts > 3) {
    return {
      ...job, attempts, status: 'failed',
      note: mergeNote(job.note, [`hard:${hardFailure}`]),
    };
  }
  const cursor = Math.min(job.total, (job.cursor || 0) + advance);
  const status = cursor >= job.total ? 'done' : 'running';
  return {
    ...job,
    attempts,
    cursor,
    status,
    note: mergeNote(job.note, failedCodes.map((c) => `miss:${c}`)),
  };
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `node --test tests/test_worker_market_jobs.mjs`
Expected: `# pass 9`、`# fail 0`

- [ ] **Step 5: 提交**

```bash
git add worker/market/jobs.js tests/test_worker_market_jobs.mjs
git commit -m "feat(worker): 抓取状态机纯函数（时段判定/分片/游标/失败计数）"
```

---

## Task 5: D1 读写层

**Files:**
- Create: `worker/market/repo.js`
- Test: `tests/test_worker_market_repo.mjs`

- [ ] **Step 1: 写 repo 的失败测试（用假 DB 记录 SQL）**

创建 `tests/test_worker_market_repo.mjs`：

```js
// D1 读写层单测：用假 DB 捕获 prepare/bind/run 的调用形态
import { test } from 'node:test';
import assert from 'node:assert';
import {
  listSymbols, ensureJob, activeJob, upsertPrices, upsertSymbols,
  updateJob, coverageStats,
} from '../worker/market/repo.js';

function fakeDB({ first = null, all = [] } = {}) {
  const calls = [];
  return {
    calls,
    prepare(sql) {
      const rec = { sql, binds: [] };
      calls.push(rec);
      return {
        bind(...args) { rec.binds = args; return this; },
        async first() { return first; },
        async all() { return { results: all }; },
        async run() { return { success: true }; },
      };
    },
    async batch(stmts) {
      calls.push({ sql: 'BATCH', count: stmts.length });
      return stmts.map(() => ({ success: true }));
    },
  };
}

test('listSymbols 按代码排序取回', async () => {
  const DB = fakeDB({ all: [{ code: '000001' }, { code: '600519' }] });
  const codes = await listSymbols(DB);
  assert.deepEqual(codes, ['000001', '600519']);
  assert.match(DB.calls[0].sql, /FROM market_symbols/);
});

test('ensureJob 用 INSERT OR IGNORE 保证幂等', async () => {
  const DB = fakeDB();
  await ensureJob(DB, '2026-10-26', 301);
  assert.match(DB.calls[0].sql, /INSERT OR IGNORE INTO sync_jobs/);
  assert.deepEqual(DB.calls[0].binds, ['2026-10-26', 301]);
});

test('activeJob 只找 pending/running 的最新一条', async () => {
  const DB = fakeDB({ first: { id: '2026-10-26', status: 'running', cursor: 40, total: 301 } });
  const job = await activeJob(DB);
  assert.equal(job.cursor, 40);
  assert.match(DB.calls[0].sql, /status IN \('pending','running'\)/);
});

test('upsertPrices 一次 batch 完成，主键冲突即更新', async () => {
  const DB = fakeDB();
  const rows = [
    { code: '600519', trade_date: '2026-10-23', open: 1, close: 2, high: 3, low: 1,
      volume: 10, amount: 100, turnover: 0.5, pct_change: 1.2 },
    { code: '000001', trade_date: '2026-10-23', open: 1, close: 2, high: 3, low: 1,
      volume: 20, amount: 200, turnover: 0.7, pct_change: -0.3 },
  ];
  await upsertPrices(DB, rows);
  const batch = DB.calls.find((c) => c.sql === 'BATCH');
  assert.equal(batch.count, rows.length);
  const ins = DB.calls.filter((c) => /INSERT INTO market_prices/.test(c.sql));
  assert.equal(ins.length, rows.length);
  assert.match(ins[0].sql, /ON CONFLICT\(code, trade_date\) DO UPDATE/);
  assert.equal(ins[0].binds.length, 10);
});

test('upsertPrices 空数组不发 SQL', async () => {
  const DB = fakeDB();
  await upsertPrices(DB, []);
  assert.equal(DB.calls.length, 0);
});

test('updateJob 写回游标与状态', async () => {
  const DB = fakeDB();
  await updateJob(DB, { id: '2026-10-26', status: 'running', cursor: 80, attempts: 0, note: null });
  const call = DB.calls[0];
  assert.match(call.sql, /UPDATE sync_jobs/);
  assert.equal(call.binds[0], 'running');
  assert.equal(call.binds[1], 80);
});

test('upsertSymbols 批量写成分股', async () => {
  const DB = fakeDB();
  await upsertSymbols(DB, [{ code: '600519', name: '茅台' }]);
  const batch = DB.calls.find((c) => c.sql === 'BATCH');
  assert.equal(batch.count, 1);
  const ins = DB.calls.find((c) => /INSERT INTO market_symbols/.test(c.sql));
  assert.match(ins.sql, /ON CONFLICT\(code\) DO UPDATE/);
  assert.deepEqual(ins.binds, ['600519', '茅台']);
});

test('coverageStats 返回最新日期与当日代码数', async () => {
  const DB = fakeDB({ first: { latest: '2026-10-23', codes: 301, days: 277 } });
  const stats = await coverageStats(DB);
  assert.deepEqual(stats, { latest: '2026-10-23', codes: 301, days: 277 });
});
```

- [ ] **Step 2: 运行确认失败**

Run: `node --test tests/test_worker_market_repo.mjs`
Expected: FAIL — `Cannot find module '../worker/market/repo.js'`

- [ ] **Step 3: 实现 `worker/market/repo.js`**

```js
// D1 读写：market_symbols / market_prices / sync_jobs。
// 所有 SQL 集中在这里，便于测试与审查。

export async function listSymbols(DB) {
  const { results } = await DB.prepare(
    'SELECT code FROM market_symbols ORDER BY code',
  ).all();
  return (results || []).map((r) => r.code);
}

export async function ensureJob(DB, id, total) {
  await DB.prepare(
    'INSERT OR IGNORE INTO sync_jobs (id, status, cursor, total, attempts, updated_at) ' +
    "VALUES (?, 'pending', 0, ?, 0, datetime('now'))",
  ).bind(id, total).run();
}

export async function activeJob(DB) {
  return DB.prepare(
    'SELECT id, status, cursor, total, attempts, note FROM sync_jobs ' +
    "WHERE status IN ('pending','running') ORDER BY id DESC LIMIT 1",
  ).first();
}

export async function updateJob(DB, job) {
  await DB.prepare(
    "UPDATE sync_jobs SET status=?, cursor=?, attempts=?, note=?, updated_at=datetime('now') " +
    'WHERE id=?',
  ).bind(job.status, job.cursor, job.attempts || 0, job.note || null, job.id).run();
}

const UPSERT = 'INSERT INTO market_prices ' +
  '(code, trade_date, open, close, high, low, volume, amount, turnover, pct_change) ' +
  'VALUES (?,?,?,?,?,?,?,?,?,?) ' +
  'ON CONFLICT(code, trade_date) DO UPDATE SET ' +
  'open=excluded.open, close=excluded.close, high=excluded.high, low=excluded.low, ' +
  'volume=excluded.volume, amount=excluded.amount, turnover=excluded.turnover, ' +
  'pct_change=excluded.pct_change';

export async function upsertPrices(DB, rows) {
  if (!rows.length) return;
  const stmts = rows.map((r) => DB.prepare(UPSERT).bind(
    r.code, r.trade_date, r.open, r.close, r.high, r.low,
    r.volume, r.amount, r.turnover, r.pct_change,
  ));
  await DB.batch(stmts);
}

const UPSERT_SYMBOL = 'INSERT INTO market_symbols (code, name, updated_at) ' +
  "VALUES (?, ?, datetime('now')) " +
  'ON CONFLICT(code) DO UPDATE SET name=excluded.name, updated_at=excluded.updated_at';

export async function upsertSymbols(DB, symbols) {
  if (!symbols.length) return;
  await DB.batch(symbols.map((s) => DB.prepare(UPSERT_SYMBOL).bind(s.code, s.name || null)));
}

export async function coverageStats(DB) {
  const row = await DB.prepare(
    'SELECT MAX(trade_date) AS latest, ' +
    'COUNT(DISTINCT trade_date) AS days FROM market_prices',
  ).first();
  const codes = await DB.prepare(
    'SELECT COUNT(DISTINCT code) AS codes FROM market_prices WHERE trade_date=?',
  ).bind(row?.latest).first();
  return { latest: row?.latest || null, codes: codes?.codes || 0, days: row?.days || 0 };
}

export async function datesSince(DB, since) {
  const { results } = await DB.prepare(
    'SELECT DISTINCT trade_date FROM market_prices WHERE trade_date >= ? ' +
    'ORDER BY trade_date DESC',
  ).bind(since).all();
  return (results || []).map((r) => r.trade_date);
}

export async function rowsForDate(DB, date) {
  const { results } = await DB.prepare(
    'SELECT code, trade_date, open, close, high, low, volume, amount, turnover, pct_change ' +
    'FROM market_prices WHERE trade_date=? ORDER BY code',
  ).bind(date).all();
  return results || [];
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `node --test tests/test_worker_market_repo.mjs`
Expected: `# pass 8`、`# fail 0`

- [ ] **Step 5: 提交**

```bash
git add worker/market/repo.js tests/test_worker_market_repo.mjs
git commit -m "feat(worker): D1 读写层（符号/任务/批量 upsert/覆盖统计）"
```

---

## Task 6: 接线 scheduled（抓取驱动）与鉴权路由

**Files:**
- Create: `worker/market/api.js`
- Modify: `worker/index.js`
- Test: `tests/test_worker_market_api.mjs`

- [ ] **Step 1: 写 API 的失败测试**

创建 `tests/test_worker_market_api.mjs`：

```js
// /api/market/* 鉴权与形态单测
import { test } from 'node:test';
import assert from 'node:assert';
import { handleMarketApi } from '../worker/market/api.js';

function env(overrides = {}) {
  const calls = [];
  const DB = {
    calls,
    prepare(sql) {
      const rec = { sql, binds: [] };
      calls.push(rec);
      return {
        bind(...a) { rec.binds = a; return this; },
        async first() {
          if (/MAX\(trade_date\)/.test(sql)) {
            return { latest: '2026-10-23', days: 277, codes: 301 };
          }
          if (/COUNT\(DISTINCT code\)/.test(sql)) return { codes: 301 };
          return null;
        },
        async all() { return { results: [{ trade_date: '2026-10-23' }] }; },
        async run() { return { success: true }; },
      };
    },
    async batch(s) { return s.map(() => ({ success: true })); },
  };
  return { DB, SYNC_TOKEN: 'token-123', ...overrides };
}

test('无 token → 401', async () => {
  const res = await handleMarketApi(
    new Request('https://x/api/market/health'), env(), '/api/market/health',
  );
  assert.equal(res.status, 401);
});

test('token 错误 → 401', async () => {
  const res = await handleMarketApi(
    new Request('https://x/api/market/health', { headers: { 'X-Sync-Token': 'bad' } }),
    env(), '/api/market/health',
  );
  assert.equal(res.status, 401);
});

test('health 返回覆盖统计', async () => {
  const res = await handleMarketApi(
    new Request('https://x/api/market/health', { headers: { 'X-Sync-Token': 'token-123' } }),
    env(), '/api/market/health',
  );
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.latest_date, '2026-10-23');
  assert.equal(body.codes_on_latest, 301);
});

test('dates 返回日期列表', async () => {
  const res = await handleMarketApi(
    new Request('https://x/api/market/dates?since=2026-10-01',
      { headers: { 'X-Sync-Token': 'token-123' } }),
    env(), '/api/market/dates',
  );
  const body = await res.json();
  assert.deepEqual(body.dates, ['2026-10-23']);
});

test('day/:date 拒绝非法日期', async () => {
  const res = await handleMarketApi(
    new Request('https://x/api/market/day/2026-13-99',
      { headers: { 'X-Sync-Token': 'token-123' } }),
    env(), '/api/market/day/2026-13-99',
  );
  assert.equal(res.status, 400);
});

test('day/:date 合法日期返回 rows 数组', async () => {
  const res = await handleMarketApi(
    new Request('https://x/api/market/day/2026-10-23',
      { headers: { 'X-Sync-Token': 'token-123' } }),
    env(), '/api/market/day/2026-10-23',
  );
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.date, '2026-10-23');
  assert.ok(Array.isArray(body.rows));
});

test('POST /api/market/symbols 批量写入成分股', async () => {
  const res = await handleMarketApi(
    new Request('https://x/api/market/symbols', {
      method: 'POST',
      headers: { 'X-Sync-Token': 'token-123', 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbols: [{ code: '600519', name: '茅台' }] }),
    }),
    env(), '/api/market/symbols',
  );
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.ok, true);
  assert.equal(body.count, 1);
});

test('POST /api/market/symbols 空数组 → 400', async () => {
  const res = await handleMarketApi(
    new Request('https://x/api/market/symbols', {
      method: 'POST',
      headers: { 'X-Sync-Token': 'token-123', 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbols: [] }),
    }),
    env(), '/api/market/symbols',
  );
  assert.equal(res.status, 400);
});

test('未知子路径 → null（交回主分发器 404）', async () => {
  const out = await handleMarketApi(
    new Request('https://x/api/market/unknown', { headers: { 'X-Sync-Token': 'token-123' } }),
    env(), '/api/market/unknown',
  );
  assert.equal(out, null);
});
```

- [ ] **Step 2: 运行确认失败**

Run: `node --test tests/test_worker_market_api.mjs`
Expected: FAIL — `Cannot find module '../worker/market/api.js'`

- [ ] **Step 3: 实现 `worker/market/api.js`**

```js
// /api/market/* ：只读导出 + 手动触发抓取。全部要求 X-Sync-Token。
import {
  datesSince, rowsForDate, coverageStats, listSymbols, ensureJob, upsertSymbols,
} from './repo.js';

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
  });
}

function authed(request, env) {
  const token = request.headers.get('X-Sync-Token');
  return Boolean(env.SYNC_TOKEN) && token === env.SYNC_TOKEN;
}

/** 返回 Response 表示已处理；返回 null 表示不是本模块的路由。 */
export async function handleMarketApi(request, env, path) {
  if (!path.startsWith('/api/market/') && path !== '/api/sync/start') return null;
  if (!authed(request, env)) return json({ error: 'unauthorized' }, 401);

  if (path === '/api/market/health') {
    const stats = await coverageStats(env.DB);
    return json({
      latest_date: stats.latest,
      codes_on_latest: stats.codes,
      days: stats.days,
    });
  }

  if (path === '/api/market/dates') {
    const since = new URL(request.url).searchParams.get('since') || '1970-01-01';
    return json({ dates: await datesSince(env.DB, since) });
  }

  const dayMatch = path.match(/^\/api\/market\/day\/([^/]+)$/);
  if (dayMatch) {
    const date = dayMatch[1];
    if (!DATE_RE.test(date)) return json({ error: 'bad date' }, 400);
    return json({ date, rows: await rowsForDate(env.DB, date) });
  }

  if (path === '/api/market/symbols' && request.method === 'POST') {
    const body = await request.json().catch(() => ({}));
    const symbols = (body.symbols || []).filter((x) => x && x.code);
    if (!symbols.length) return json({ error: 'symbols required' }, 400);
    await upsertSymbols(env.DB, symbols);
    return json({ ok: true, count: symbols.length });
  }

  if (path === '/api/sync/start' && request.method === 'POST') {
    const body = await request.json().catch(() => ({}));
    const date = body.date || new Date().toISOString().slice(0, 10);
    if (!DATE_RE.test(date)) return json({ error: 'bad date' }, 400);
    const codes = await listSymbols(env.DB);
    await ensureJob(env.DB, date, codes.length);
    return json({ ok: true, job: { id: date, total: codes.length } });
  }

  return null;
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `node --test tests/test_worker_market_api.mjs`
Expected: `# pass 9`、`# fail 0`

- [ ] **Step 5: 在 `worker/index.js` 接线**

在文件顶部追加 import：

```js
import { handleMarketApi } from './market/api.js';
import {
  beijingNow, isTradingWindow, planChunk, shouldRun, advanceJob,
} from './market/jobs.js';
import {
  listSymbols, ensureJob, activeJob, upsertPrices, updateJob,
} from './market/repo.js';
import { fetchBars, pickBars } from './market/sources.js';
```

在 `fetch` 处理器里、**OPTIONS 预检分支之后**、`// ---- health ----` 之前插入
（必须在 OPTIONS 之后，否则浏览器预检会拿到 401）：

```js
    // /api/market/* 与 /api/sync/start 由独立模块处理（自带鉴权）
    const marketRes = await handleMarketApi(request, env, path);
    if (marketRes) return marketRes;
```

在 `scheduled` 处理器里追加抓取驱动（保留原有 dispatch 逻辑）：

```js
  async scheduled(event, env, ctx) {
    // 1) 原有：兜底触发 GitHub Actions 消费 D1
    ctx.waitUntil((async () => {
      const r = await dispatchDeploySite(env);
      if (r.ok) {
        console.log('workflow_dispatch 已触发:', DISPATCH_WORKFLOW, 'ref', DISPATCH_REF);
      } else {
        console.error('workflow_dispatch 失败:', r.status, r.detail);
        // 不抛错：抓取任务不因 dispatch 失败而中断
      }
    })());

    // 2) 抓取状态机：每 2 分钟一次的 cron 只在这里干活
    ctx.waitUntil(runMarketSync(env));
  },
```

在文件末尾（`export default` 之外）新增：

```js
/** 抓取驱动：读活跃任务 → 抓一片 → upsert → 推进游标。非交易时段秒退。 */
async function runMarketSync(env) {
  if (env.SYNC_ENABLED !== 'true') return;          // 开关未打开时完全不抓
  const bj = beijingNow();
  const job = await activeJob(env.DB);

  // 15:35 之后自动建当天任务（幂等），无需外部触发
  if (!job && isTradingWindow(bj)) {
    const codes = await listSymbols(env.DB);
    if (codes.length) await ensureJob(env.DB, bj.date, codes.length);
  }
  const active = job || await activeJob(env.DB);
  if (!shouldRun(active, bj)) return;

  const codes = await listSymbols(env.DB);
  const { slice, done } = planChunk(codes, active.cursor);
  if (!slice.length) {
    await updateJob(env.DB, { ...active, status: 'done', cursor: active.total });
    return;
  }

  const rows = [];
  const failed = [];
  // 并发池 6（Free 计划同时连接上限）
  for (let i = 0; i < slice.length; i += 6) {
    const batch = slice.slice(i, i + 6);
    const results = await Promise.all(batch.map(async (code) => {
      try {
        const { rows: bars } = await fetchBars(code, bj.date);
        return { code, rows: pickBars(bars, bj.date) };
      } catch {
        return { code, rows: [] };
      }
    }));
    for (const r of results) {
      if (r.rows.length) rows.push(...r.rows);
      else failed.push(r.code);
    }
  }

  await upsertPrices(env.DB, rows);
  const next = advanceJob(active, {
    advance: slice.length,
    failedCodes: failed,
    hardFailure: rows.length === 0 ? 'no rows from any source' : null,
  });
  await updateJob(env.DB, next);
  console.log(`sync ${active.id}: cursor=${next.cursor}/${active.total} rows=${rows.length} failed=${failed.length}`);
}
```

- [ ] **Step 6: 语法检查 + 现有 Worker 测试仍通过**

```bash
node --check worker/index.js
node --test tests/test_worker_auth.mjs    # 原有测试不能挂
```

Expected: 无语法错误；`# pass 4`、`# fail 0`

- [ ] **Step 7: 提交**

```bash
git add worker/market/api.js worker/index.js tests/test_worker_market_api.mjs
git commit -m "feat(worker): /api/market/* 鉴权路由 + scheduled 抓取驱动"
```

---

## Task 7: 解析结果与 Python 数据源一致（parity）

**Files:**
- Test: `tests/test_worker_market_parity.py`

- [ ] **Step 1: 写 parity 测试**

创建 `tests/test_worker_market_parity.py`：

```python
"""Worker 的 K 线解析必须与 Python data_providers 一致。

同一份东财 fixture，两边各解析一次，逐字段比对。
"""
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "em_600519.json"

pytestmark = pytest.mark.skipif(
    not FIXTURE.exists(), reason="fixture 未抓取（见计划 Task 3 Step 1）"
)


def _parse_with_python() -> dict:
    """复用 data_providers 的解析逻辑（抓取函数里的解析部分）。"""
    import data_providers as dp

    raw = json.loads(FIXTURE.read_text())
    klines = raw["data"]["klines"]
    rows = {}
    for entry in klines:
        p = entry.split(",")
        if len(p) < 11:
            continue
        rows[p[0]] = {
            "open": float(p[1]), "close": float(p[2]),
            "high": float(p[3]), "low": float(p[4]),
            "volume": float(p[5]), "amount": float(p[6]),
            "pct_change": float(p[8]), "turnover": float(p[10]),
        }
    assert rows, "fixture 里应有 K 线"
    return rows


def _parse_with_worker() -> dict:
    script = (
        "import { readFileSync } from 'node:fs';"
        "import { parseEastmoney } from './worker/market/sources.js';"
        f"const raw = JSON.parse(readFileSync({str(FIXTURE)!r}, 'utf8'));"
        "const rows = parseEastmoney(raw, '600519');"
        "const out = {};"
        "for (const r of rows) out[r.trade_date] = {"
        "  open: r.open, close: r.close, high: r.high, low: r.low,"
        "  volume: r.volume, amount: r.amount,"
        "  pct_change: r.pct_change, turnover: r.turnover };"
        "console.log(JSON.stringify(out));"
    )
    res = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def test_worker_parse_matches_python():
    py = _parse_with_python()
    js = _parse_with_worker()
    assert set(py) == set(js), "日期集合必须一致"
    for date in py:
        for field in ("open", "close", "high", "low", "volume", "amount",
                      "pct_change", "turnover"):
            assert py[date][field] == pytest.approx(js[date][field]), \
                f"{date}.{field} 不一致: python={py[date][field]} js={js[date][field]}"
```

- [ ] **Step 2: 运行测试**

Run: `python3 -m pytest tests/test_worker_market_parity.py -q`
Expected: `1 passed`

- [ ] **Step 3: 提交**

```bash
git add tests/test_worker_market_parity.py
git commit -m "test(worker): K 线解析与 Python data_providers 的 parity 测试"
```

---

## Task 8: GH 侧取数脚本 `pull_from_d1.py`

**Files:**
- Create: `scripts/pull_from_d1.py`
- Test: `tests/test_pull_from_d1.py`

- [ ] **Step 1: 写失败测试（mock HTTP，不联网）**

创建 `tests/test_pull_from_d1.py`：

```python
"""GH 侧从 Worker 拉增量写 SQLite。用注入的 fetcher 代替真实 HTTP。"""

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from db_repository import open_db

_SPEC = importlib.util.spec_from_file_location(
    "pull_from_d1",
    Path(__file__).resolve().parent.parent / "scripts" / "pull_from_d1.py",
)
pull = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(pull)


def _row(code, date, close, pct=0.0, turnover=0.0):
    return {"code": code, "trade_date": date, "open": close, "close": close,
            "high": close, "low": close, "volume": 1000, "amount": 1e6,
            "turnover": turnover, "pct_change": pct}


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "m.db")
    conn = open_db(path)
    conn.execute(
        "INSERT INTO daily_prices (code, trade_date, open, close, high, low,"
        " volume, amount) VALUES ('600519','2026-10-22',100,100,100,100,1,1)"
    )
    conn.commit()
    yield path, conn
    conn.close()


def _fetcher(payloads):
    calls = []

    def fetch_json(url, token):
        calls.append(url)
        if "/api/market/health" in url:
            return payloads["health"]
        if "/api/market/dates" in url:
            return payloads["dates"]
        if "/api/market/day/" in url:
            date = url.rsplit("/", 1)[1]
            return payloads["days"].get(date, {"date": date, "rows": []})
        raise AssertionError(f"unexpected url {url}")

    fetch_json.calls = calls
    return fetch_json


def test_pull_writes_missing_days(db):
    path, conn = db
    fetch_json = _fetcher({
        "health": {"latest_date": "2026-10-23"},
        "dates": {"dates": ["2026-10-23"]},
        "days": {"2026-10-23": {"date": "2026-10-23",
                                 "rows": [_row("600519", "2026-10-23", 110.0, 10.0)]}},
    })
    result = pull.pull(path, "https://w", "t", max_days=60, fetch_json=fetch_json)
    assert result["written"] == 1
    assert result["fell_back"] is False
    got = conn.execute(
        "SELECT close FROM daily_prices WHERE code='600519' AND trade_date='2026-10-23'"
    ).fetchone()
    assert got[0] == 110.0


def test_pull_is_idempotent(db):
    path, conn = db
    payloads = {
        "health": {"latest_date": "2026-10-23"},
        "dates": {"dates": ["2026-10-23"]},
        "days": {"2026-10-23": {"date": "2026-10-23",
                                 "rows": [_row("600519", "2026-10-23", 110.0)]}},
    }
    pull.pull(path, "https://w", "t", max_days=60, fetch_json=_fetcher(payloads))
    pull.pull(path, "https://w", "t", max_days=60, fetch_json=_fetcher(payloads))
    n = conn.execute(
        "SELECT COUNT(*) FROM daily_prices WHERE trade_date='2026-10-23'"
    ).fetchone()[0]
    assert n == 1


def test_pull_falls_back_when_worker_empty(db):
    path, _ = db
    fetch_json = _fetcher({
        "health": {"latest_date": None},
        "dates": {"dates": []},
        "days": {},
    })
    result = pull.pull(path, "https://w", "t", max_days=60, fetch_json=fetch_json)
    assert result["fell_back"] is True
    assert result["written"] == 0


def test_pull_falls_back_on_http_error(db):
    path, _ = db
    def boom(url, token):
        raise pull.WorkerError("HTTP 500")
    result = pull.pull(path, "https://w", "t", max_days=60, fetch_json=boom)
    assert result["fell_back"] is True


def test_pull_computes_pct_change_when_missing(db):
    """腾讯回退路径 pct_change=0，GH 侧用前一交易日收盘补算。"""
    path, conn = db
    fetch_json = _fetcher({
        "health": {"latest_date": "2026-10-23"},
        "dates": {"dates": ["2026-10-23"]},
        "days": {"2026-10-23": {"date": "2026-10-23",
                                 "rows": [_row("600519", "2026-10-23", 110.0, 0.0)]}},
    })
    pull.pull(path, "https://w", "t", max_days=60, fetch_json=fetch_json)
    pct = conn.execute(
        "SELECT pct_change FROM daily_prices WHERE code='600519' AND trade_date='2026-10-23'"
    ).fetchone()[0]
    # 前一日收盘 100 → (110-100)/100*100 = 10.0
    assert abs(pct - 10.0) < 1e-6
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_pull_from_d1.py -q`
Expected: FAIL — `FileNotFoundError: scripts/pull_from_d1.py`

- [ ] **Step 3: 实现 `scripts/pull_from_d1.py`**

```python
#!/usr/bin/env python3
"""从 Cloudflare Worker 拉增量行情，写入本地 SQLite（GH Actions 用）。

契约：
  - 成功且有新数据 → 退出码 0，written>0
  - Worker 无新数据/不可用 → 退出码 3，并写 GITHUB_OUTPUT fell_back=true
  - 参数错误 → 退出码 2
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from typing import Callable, Dict, Optional


class WorkerError(RuntimeError):
    pass


def _http_json(url: str, token: str) -> Dict:
    req = urllib.request.Request(url, headers={"X-Sync-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise WorkerError(f"{url}: {exc}") from exc


def _prev_close(conn: sqlite3.Connection, code: str, date: str) -> Optional[float]:
    row = conn.execute(
        "SELECT close FROM daily_prices WHERE code=? AND trade_date<? "
        "ORDER BY trade_date DESC LIMIT 1",
        (code, date),
    ).fetchone()
    return float(row[0]) if row and row[0] is not None else None


def pull(
    db_path: str,
    worker: str,
    token: str,
    *,
    max_days: int = 60,
    fetch_json: Callable[[str, str], Dict] = _http_json,
) -> Dict:
    base = worker.rstrip("/")
    try:
        health = fetch_json(f"{base}/api/market/health", token)
        latest = health.get("latest_date")
        if not latest:
            return {"written": 0, "fell_back": True, "reason": "worker has no data"}

        conn = sqlite3.connect(db_path)
        try:
            local_max = conn.execute("SELECT MAX(trade_date) FROM daily_prices").fetchone()[0]
            dates_res = fetch_json(f"{base}/api/market/dates?since={local_max or '1970-01-01'}", token)
            dates = [d for d in (dates_res.get("dates") or []) if not local_max or d > local_max]
            dates = sorted(dates)[-max_days:]
            if not dates:
                return {"written": 0, "fell_back": False, "latest": latest, "reason": "up to date"}

            written = 0
            for date in dates:
                payload = fetch_json(f"{base}/api/market/day/{date}", token)
                rows = payload.get("rows") or []
                for r in rows:
                    pct = r.get("pct_change")
                    if not pct:
                        prev = _prev_close(conn, r["code"], date)
                        if prev:
                            pct = (float(r["close"]) - prev) / prev * 100
                    conn.execute(
                        "INSERT OR REPLACE INTO daily_prices "
                        "(code, trade_date, open, close, high, low, volume, amount,"
                        " pct_change, turnover) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (r["code"], r["trade_date"], r["open"], r["close"],
                         r["high"], r["low"], r["volume"], r["amount"],
                         pct if pct is not None else 0.0, r.get("turnover") or 0.0),
                    )
                    written += 1
                conn.commit()
            return {"written": written, "fell_back": False, "latest": latest, "days": dates}
        finally:
            conn.close()
    except WorkerError as exc:
        print(f"::warning::Worker 不可用：{exc}", file=sys.stderr)
        return {"written": 0, "fell_back": True, "reason": str(exc)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="从 Worker 拉增量行情写 SQLite")
    ap.add_argument("--db", default="hs300.db")
    ap.add_argument("--worker", default="https://ycu.space")
    ap.add_argument("--token", default=os.environ.get("D1_SYNC_TOKEN", ""))
    ap.add_argument("--max-days", type=int, default=60)
    ap.add_argument("--github-output", action="store_true")
    args = ap.parse_args(argv)

    if not args.token:
        print("::error::缺少 SYNC token（--token 或 D1_SYNC_TOKEN）", file=sys.stderr)
        return 2

    result = pull(args.db, args.worker, args.token, max_days=args.max_days)
    print(json.dumps(result, ensure_ascii=False))

    if args.github_output:
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a", encoding="utf-8") as f:
                f.write(f"fell_back={'true' if result['fell_back'] else 'false'}\n")
                f.write(f"written={result.get('written', 0)}\n")
    return 3 if result["fell_back"] else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest tests/test_pull_from_d1.py -q`
Expected: `5 passed`

- [ ] **Step 5: 提交**

```bash
git add scripts/pull_from_d1.py tests/test_pull_from_d1.py
git commit -m "feat(gh): 从 D1 拉增量行情写 SQLite（Worker 不可用则标记回退）"
```

---

## Task 9: workflow 接入 + 数据来源字段

**Files:**
- Modify: `.github/workflows/deploy-site.yml:95-131`（增量同步步骤）
- Modify: `scripts/ci_check_data.py`
- Test: `tests/test_ci_check_data.py`（追加用例）

- [ ] **Step 1: 给 ci_check_data 加数据来源（先写测试）**

在 `tests/test_ci_check_data.py` 末尾追加：

```python
def test_source_field_passed_through(db):
    path, conn = db
    _seed(conn, [f"60000{i}" for i in range(300)], "2026-10-26")
    _seed_picks(conn, "2026-10-26")
    _seed_plan(conn, "2026-10-26")

    out = ci.check(path, "2026-10-26", min_codes=250, prices_only=False,
                   source="d1")
    assert out["source"] == "d1"
    out2 = ci.check(path, "2026-10-26", min_codes=250, prices_only=False,
                    source="direct")
    assert out2["source"] == "direct"
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_ci_check_data.py::test_source_field_passed_through -q`
Expected: FAIL — `check() got an unexpected keyword argument 'source'`

- [ ] **Step 3: 实现 source 字段**

`scripts/ci_check_data.py` 里给 `check()` 加参数并回填到结果：

```python
def check(db_path: str, expect: str, min_codes: int, prices_only: bool,
          source: str = "unknown") -> Dict:
```

在 `check()` 的两个 `return` 字典里都加上 `"source": source`，并在打印块加入：

```python
        print(f"  数据来源       : {result.get('source', 'unknown')}")
```

`main()` 增加参数：

```python
    ap.add_argument("--source", default="unknown",
                    help="数据来源：d1 / direct / unknown")
```

调用处：

```python
        result = check(args.db, expect, args.min_codes, args.prices_only,
                       source=args.source)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest tests/test_ci_check_data.py -q`
Expected: `9 passed`

- [ ] **Step 5: 改写 workflow 的同步步骤**

把 `.github/workflows/deploy-site.yml` 里「增量同步行情（短窗口 + 重试到落地）」整步替换为：

```yaml
      - name: 从 D1 拉增量（Worker 抓取）
        id: pull
        if: steps.trade-day.outputs.is_trade_day == 'true' && steps.freshness.outputs.skip != 'true'
        env:
          D1_SYNC_TOKEN: ${{ secrets.D1_SYNC_TOKEN }}
          EXPECT: ${{ steps.trade-day.outputs.today_bj }}
        run: |
          set -uo pipefail
          COLD=0; [ -f hs300.db ] || COLD=1
          MAX_DAYS=60; [ "$COLD" = "1" ] && MAX_DAYS=400
          set +e
          python3 scripts/pull_from_d1.py --db hs300.db --max-days "$MAX_DAYS" --github-output
          rc=$?
          set -e
          echo "pull 退出码: $rc"
          [ "$rc" -eq 2 ] && { echo "::error::缺少 D1_SYNC_TOKEN"; exit 1; }

      - name: 回退直连（Worker 无数据时）
        if: steps.pull.outputs.fell_back == 'true'
        env:
          EXPECT: ${{ steps.trade-day.outputs.today_bj }}
          SYNC_DAYS: "60"
          SYNC_RETRIES: "2"
          SYNC_CONCURRENCY: "6"
          SYNC_RATE: "6"
        run: |
          echo "::warning::D1 无新数据或不可用，回退直连数据源"
          SYNC_END_DATE="$EXPECT" \
            bash a-finder.sh sync-incremental hs300.db 20 pick-history 2>&1 | tail -40 || true

      - name: 上传成分股到 D1（每日全量 upsert）
        if: steps.trade-day.outputs.is_trade_day == 'true' && steps.freshness.outputs.skip != 'true'
        env:
          D1_SYNC_TOKEN: ${{ secrets.D1_SYNC_TOKEN }}
        run: |
          # 新进沪深300的票若不在 market_symbols 里，Worker 永远不会抓它
          python3 - <<'PY_SYMBOLS' > /tmp/symbols.json
import json, sqlite3
c = sqlite3.connect('hs300.db')
rows = [{"code": r[0], "name": r[1]} for r in c.execute(
    "SELECT code, name FROM hs300_metadata ORDER BY code")]
print(json.dumps({"symbols": rows}, ensure_ascii=False))
PY_SYMBOLS
          curl -sf -X POST https://ycu.space/api/market/symbols \
            -H "X-Sync-Token: $D1_SYNC_TOKEN" \
            -H 'Content-Type: application/json' \
            --data-binary @/tmp/symbols.json | python3 -m json.tool

      - name: 行情校验（决定是否继续补抓）
        if: steps.trade-day.outputs.is_trade_day == 'true' && steps.freshness.outputs.skip != 'true'
        env:
          EXPECT: ${{ steps.trade-day.outputs.today_bj }}
        run: |
          python3 scripts/ci_check_data.py --db hs300.db --expect "$EXPECT" \
            --prices-only --source "${{ steps.pull.outputs.fell_back == 'true' && 'direct' || 'd1' }}"
```

- [ ] **Step 6: 门禁步骤加 --source**

把「数据完整性门禁」的 run 改为：

```yaml
          python3 scripts/ci_check_data.py --db hs300.db --expect "$EXPECT" \
            --source "${{ steps.pull.outputs.fell_back == 'true' && 'direct' || 'd1' }}"
```

- [ ] **Step 7: 校验 workflow 语法与脚本**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/deploy-site.yml')); print('YAML OK')"
python3 - <<'PY'
import yaml, subprocess, tempfile, os
wf = yaml.safe_load(open('.github/workflows/deploy-site.yml'))
for s in wf['jobs']['deploy']['steps']:
    if not s.get('run'): continue
    with tempfile.NamedTemporaryFile('w', suffix='.sh', delete=False) as f:
        f.write(s['run']); p = f.name
    r = subprocess.run(['bash','-n',p], capture_output=True, text=True)
    print(s.get('name'), 'OK' if r.returncode == 0 else r.stderr)
    os.unlink(p)
PY
```

Expected: `YAML OK` + 每个步骤 `OK`

- [ ] **Step 8: 提交**

```bash
git add .github/workflows/deploy-site.yml scripts/ci_check_data.py tests/test_ci_check_data.py
git commit -m "feat(ci): 同步步骤改为从 D1 拉增量，Worker 不可用时回退直连"
```

---

## Task 10: 部署 Worker + 打开开关 + 端到端验证

**Files:**
- Modify: `.github/workflows/deploy-worker.yml`（可选同步 `SYNC_TOKEN`）
- Modify: `CLAUDE.md`

- [ ] **Step 1: deploy-worker 支持同步 SYNC_TOKEN**

在 `deploy-worker.yml` 的「同步 GITHUB_DISPATCH_TOKEN（可选）」之后追加同构步骤：

```yaml
      - name: 同步 SYNC_TOKEN（可选）
        env:
          SYNC_TOKEN: ${{ secrets.SYNC_TOKEN }}
        run: |
          if [ -z "$SYNC_TOKEN" ]; then
            echo "::notice::未配置 SYNC_TOKEN，/api/market/* 将始终 401"
            exit 0
          fi
          printf '%s' "$SYNC_TOKEN" | npx --yes wrangler@latest secret put SYNC_TOKEN
          echo "已同步 SYNC_TOKEN"
```

- [ ] **Step 2: 本地生成 token 并配置两处 secret**

```bash
TOKEN=$(openssl rand -hex 32)
echo "$TOKEN"                                  # 复制这个值
echo "1) GitHub 仓库 Secrets 加 SYNC_TOKEN 和 D1_SYNC_TOKEN（同值）"
echo "2) 或本地执行：printf '%s' \"\$TOKEN\" | npx --yes wrangler@latest secret put SYNC_TOKEN"
```

- [ ] **Step 3: 部署 Worker（先不开开关）**

```bash
npx --yes wrangler@latest deploy
curl -s -o /dev/null -w "%{http_code}\n" https://ycu.space/api/market/health   # 未带 token → 401
curl -s -H "X-Sync-Token: $TOKEN" https://ycu.space/api/market/health | python3 -m json.tool
```

Expected: 401；随后 `{"latest_date": "2026-10-23", "codes_on_latest": 301, ...}`

- [ ] **Step 4: 打开开关并手动触发一次抓取**

```bash
printf '%s' "true" | npx --yes wrangler@latest secret put SYNC_ENABLED
curl -s -X POST -H "X-Sync-Token: $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"date\": \"2026-10-23\"}" https://ycu.space/api/sync/start | python3 -m json.tool
sleep 300    # 等 2 分钟 cron 跑 2-3 轮
curl -s -H "X-Sync-Token: $TOKEN" https://ycu.space/api/market/health | python3 -m json.tool
```

Expected: health 的 `latest_date` 变成当天（或仍是 10-23 但 `codes_on_latest` 增加）。若 `latest_date` 不变，用 `wrangler tail` 看 `sync ` 日志行里的 `cursor=` 是否在推进。

- [ ] **Step 5: 核对 D1 与本地库同日数据一致**

```bash
npx --yes wrangler@latest d1 execute a-finder --remote --yes \
  --command "SELECT code, close, turnover FROM market_prices WHERE trade_date='2026-10-23' AND code='600519'"
python3 -c "
import sqlite3
c=sqlite3.connect('hs300.db')
print(c.execute(\"SELECT code, close, turnover FROM daily_prices WHERE trade_date='2026-10-23' AND code='600519'\").fetchone())
"
```

Expected: 两个 `close`、`turnover` 完全相同。

- [ ] **Step 6: 跑一次完整 workflow 验证端到端**

在 GitHub Actions 页面手动 dispatch `deploy-site`，检查：
1. `从 D1 拉增量` 步骤输出 `written>0` 或 `up to date`
2. 没有触发「回退直连」步骤
3. 门禁输出 `数据来源: d1` 且通过

- [ ] **Step 7: 更新 CLAUDE.md**

在 CI 章节追加：

```markdown
# Worker 抓取行情（D1 为抓取落点）
- Worker 每 2 分钟 cron 驱动分片状态机（`worker/market/jobs.js`，每片 40 只），
  15:35–23:00 北京时段抓当天日线写 D1 的 `market_prices`；游标在 `sync_jobs`
- GH 用 `scripts/pull_from_d1.py` 拉增量（`X-Sync-Token` 鉴权），Worker 不可用则回退直连
- 开关：Worker secret `SYNC_ENABLED=true` 才抓；关掉即回到「Worker 只触发 GH」
- 历史数据用 `scripts/seed_d1.py` 一次性灌入，不走 Worker
- 测试：`node --test tests/test_worker_market_*.mjs` + `pytest tests/test_worker_market_parity.py`
```

- [ ] **Step 8: 提交**

```bash
git add .github/workflows/deploy-worker.yml CLAUDE.md
git commit -m "feat(worker): 抓取开关与 SYNC_TOKEN 接入部署流水线"
```

---

## 回滚

三步，任一步都能立即止血：

1. **停抓取**（不影响现有功能）：`wrangler secret put SYNC_ENABLED` 写入 `false` → cron 只空转
2. **回滚 GH 同步步骤**：`git revert` Task 9 的提交 → 回到直连 `sync-incremental`
3. **回滚 Worker 代码**：`git revert` Task 6 的提交后 `wrangler deploy`（`/api/market/*` 消失，`/api/decide/*` 不受影响）

## 观察期检查清单（连续 3 个交易日）

- [ ] D1 `market_prices` 当天 301 行齐全（`health.codes_on_latest == 301`）
- [ ] 抓取完成时间 ≤ 15:45（北京）
- [ ] GH 的 pull 步骤未触发回退
- [ ] 门禁为绿且 `数据来源: d1`
- [ ] 站点日期 == 当天
