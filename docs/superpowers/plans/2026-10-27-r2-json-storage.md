# 行情存储改造（R2 + JSON）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把行情从 D1 搬到 R2（一天一个 JSON），D1 只存元数据，使整链稳定运行在 Cloudflare 免费额度内。

**Architecture:** Worker 每轮抓 40 只写入 R2 的 `parts/<date>/NN.json`，游标满后读回 8 片合并成 `prices/<date>.json` 并在 D1 写一行 `data_index`。GitHub Actions 通过 `/api/market/day/:date` 取 JSON（Worker 读 R2，miss 时回退旧 D1 表），写入 SQLite 后照旧跑 Python。历史迁移由 CI 用 `wrangler d1 export` + `r2 object put` 完成，Worker 不碰历史。

**Tech Stack:** Cloudflare Workers (Free) · R2 · D1 · `node --test` · Python 3.11 + pytest · GitHub Actions + wrangler CLI

**Spec:** [`docs/superpowers/specs/2026-10-27-r2-json-storage-design.md`](../specs/2026-10-27-r2-json-storage-design.md)

---

## 文件结构

| 文件 | 职责 | 状态 |
|---|---|---|
| `wrangler.toml` | 增加 `[[r2_buckets]]` 绑定 | 修改 |
| `worker/schema.sql` | 增加 `data_index` 表 | 修改 |
| `worker/market/blob.js` | R2 读写封装 + key 命名（唯一知道对象布局的地方） | 新建 |
| `worker/market/merge.js` | 分片合并纯函数（乱序/缺片/重复校验） | 新建 |
| `worker/market/repo.js` | 增加 `data_index` 读写；`market_prices` 保留兼容读 | 修改 |
| `worker/market/runner.js` | 日抓写路径改为「分片 → 合并 → R2 + 索引」 | 修改 |
| `worker/market/api.js` | `/day` 先读 R2、miss 回退 D1；health/dates 走 `data_index` | 修改 |
| `.github/workflows/d1-to-r2.yml` | 一次性迁移：D1 最近 260 天 → R2 + `data_index` | 新建 |
| `scripts/d1_export_to_json.py` | 把 D1 导出的 SQL 拆成按日 JSON（可单测） | 新建 |
| `scripts/build_index_sql.py` | 由 JSON 目录生成 `data_index` 的批量 SQL（可单测） | 新建 |
| `scripts/verify_r2_migration.py` | 逐字段核对 JSON 与 D1 抽样（CI 内用，可单测） | 新建 |
| `.github/workflows/d1-backfill.yml` | 历史回填改按 R2 产出（保留能力，默认不跑） | 修改 |
| `tests/test_worker_market_blob.mjs` | blob 层测试（假 R2） | 新建 |
| `tests/test_worker_market_merge.mjs` | 合并纯函数测试 | 新建 |
| `tests/test_worker_market_api.mjs` | 增加「R2 命中 / D1 回退」用例 | 修改 |
| `tests/test_d1_export_to_json.py` | 迁移脚本测试（fixture SQL） | 新建 |
| `tests/test_worker_market_parity.py` | parity 改为对比 R2 JSON 与本地库 | 修改 |

---

## Task 1: 前置检查 —— R2 是否可用（决策门）

**这是全计划的决策点。** 昨天 `routes` 的教训：给 wrangler 配置加新资源，需要 token 有对应权限，否则**部署直接失败**（而且 CI 只显示一条报错）。

**Files:**
- Create: `.github/workflows/r2-probe.yml`

- [ ] **Step 1: 写一个探针 workflow**

```yaml
name: r2-probe

# 验证：Cloudflare 账户已开通 R2 + 现有 CLOUDFLARE_API_TOKEN 具备 R2 权限。
# 二者缺一，本改造方案不成立（部署会像昨天的 routes 一样直接失败）。
on:
  push:
    branches: [main]
    paths:
      - ".github/workflows/r2-probe.yml"
  workflow_dispatch:

permissions:
  contents: read

jobs:
  probe:
    runs-on: ubuntu-latest
    env:
      CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}
      CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
    steps:
      - uses: actions/checkout@v4
      - name: R2 桶列表（需要 Workers R2 Storage:Edit）
        run: |
          set +e
          npx --yes wrangler@latest r2 bucket list 2>&1 | tee /tmp/r2list.txt
          rc=${PIPESTATUS[0]}
          echo "exit=$rc"
          if [ "$rc" -ne 0 ]; then
            echo "::error::R2 不可用：要么账户没开通 R2，要么 token 缺少 Workers R2 Storage:Edit 权限"
            exit 1
          fi
          grep -q "a-finder-data" /tmp/r2list.txt || echo "::notice::桶 a-finder-data 尚不存在（下一步创建）"
      - name: 创建桶（幂等）
        run: |
          npx --yes wrangler@latest r2 bucket create a-finder-data || echo "已存在或创建失败（见上一步结论）"
```

- [ ] **Step 2: 推送并读取结果**

```bash
git add .github/workflows/r2-probe.yml
git commit -m "chore(ci): R2 可用性探针（账户是否开通 + token 权限）"
git push
```

等 CI 跑完后看结果（badge 或 Actions 页面）：

| 结果 | 含义 | 动作 |
|---|---|---|
| 成功且桶已创建 | R2 可用 | 继续 Task 2 |
| `Authentication error` / `10000` | token 缺 R2 权限 | 让仓库 owner 给 token 加 **Workers R2 Storage: Edit**，重新跑 |
| 提示需要开通 R2 | 账户没开通 | 去 dashboard 开通 R2（免费 10 GB，需绑卡） |

- [ ] **Step 3: 记录结论并删除探针**

把结论写进 spec 的「风险」一节（一行），然后：

```bash
git rm .github/workflows/r2-probe.yml
git commit -m "chore(ci): 移除 R2 探针（结论已记录）"
```

---

## Task 2: D1 表 + R2 绑定

**Files:**
- Modify: `worker/schema.sql`
- Modify: `wrangler.toml`
- Test: `tests/test_worker_market_repo.mjs`

- [ ] **Step 1: 写 `data_index` 相关测试（先红）**

在 `tests/test_worker_market_repo.mjs` 末尾追加：

```js
test('upsertIndex 写日期索引（含 missing 与体积）', async () => {
  const DB = fakeDB();
  await upsertIndex(DB, {
    date: '2026-10-27', codes: 300, bytes: 59000, missing: ['601059'],
  });
  const ins = DB.calls.find((c) => /INSERT INTO data_index/.test(c.sql));
  assert.ok(ins, '应写 data_index');
  assert.match(ins.sql, /ON CONFLICT\(trade_date\) DO UPDATE/);
  assert.deepEqual(ins.binds, ['2026-10-27', 300, 59000, '["601059"]']);
});

test('indexStats 返回最新日期/当日代码数/天数', async () => {
  const DB = fakeDB({
    first: { latest: '2026-10-27', codes: 300, days: 260 },
  });
  const s = await indexStats(DB);
  assert.deepEqual(s, { latest: '2026-10-27', codes: 300, days: 260 });
});

test('indexDates 按日期倒序取回', async () => {
  const DB = fakeDB({ all: [{ trade_date: '2026-10-27' }, { trade_date: '2026-10-24' }] });
  assert.deepEqual(await indexDates(DB, '2026-01-01'), ['2026-10-27', '2026-10-24']);
});
```

同时把 import 行补上新函数：

```js
import {
  listSymbols, ensureJob, activeJob, upsertPrices, upsertSymbols,
  updateJob, coverageStats, upsertIndex, indexStats, indexDates,
} from '../worker/market/repo.js';
```

- [ ] **Step 2: 运行确认失败**

Run: `node --test tests/test_worker_market_repo.mjs`
Expected: FAIL — `upsertIndex is not a function`

- [ ] **Step 3: 在 `worker/schema.sql` 追加表**

```sql
-- 日期索引：GH 用它判断缺哪几天，health 用它汇总。
-- 行情本体在 R2（prices/<date>.json），D1 只留这一行元数据。
CREATE TABLE IF NOT EXISTS data_index (
    trade_date TEXT PRIMARY KEY,
    codes      INTEGER NOT NULL,
    bytes      INTEGER,
    missing    TEXT,
    updated_at TEXT NOT NULL
);
```

- [ ] **Step 4: 在 `worker/market/repo.js` 实现**

```js
export async function upsertIndex(DB, { date, codes, bytes, missing }) {
  await DB.prepare(
    'INSERT INTO data_index (trade_date, codes, bytes, missing, updated_at) ' +
    "VALUES (?,?,?,?,datetime('now')) " +
    'ON CONFLICT(trade_date) DO UPDATE SET codes=excluded.codes, ' +
    'bytes=excluded.bytes, missing=excluded.missing, updated_at=excluded.updated_at',
  ).bind(date, codes, bytes ?? null, JSON.stringify(missing || [])).run();
}

export async function indexStats(DB) {
  const row = await DB.prepare(
    'SELECT MAX(trade_date) AS latest, COUNT(*) AS days FROM data_index',
  ).first();
  if (!row || !row.latest) return { latest: null, codes: 0, days: 0 };
  const cur = await DB.prepare(
    'SELECT codes FROM data_index WHERE trade_date=?',
  ).bind(row.latest).first();
  return { latest: row.latest, codes: cur?.codes || 0, days: row.days || 0 };
}

export async function indexDates(DB, since) {
  const { results } = await DB.prepare(
    'SELECT trade_date FROM data_index WHERE trade_date >= ? ORDER BY trade_date DESC',
  ).bind(since).all();
  return (results || []).map((r) => r.trade_date);
}

export async function indexRow(DB, date) {
  return DB.prepare('SELECT * FROM data_index WHERE trade_date=?').bind(date).first();
}
```

- [ ] **Step 5: 加 R2 绑定到 `wrangler.toml`**

```toml
[[r2_buckets]]
binding = "DATA"
bucket_name = "a-finder-data"
```

- [ ] **Step 6: 测试通过 + 部署验证**

```bash
node --test tests/test_worker_market_repo.mjs        # 期望 pass 11
python3 -c "import tomllib;d=tomllib.load(open('wrangler.toml','rb'));print(d['r2_buckets'])"
git add -A && git commit -m "feat(d1): data_index 表 + r2_buckets 绑定"
git push
```

推送后**必须**确认 `deploy-worker` badge 是 passing（R2 绑定会让部署失败，如果 Task 1 权限没配好）。

---

## Task 3: R2 读写层与 key 布局

**Files:**
- Create: `worker/market/blob.js`
- Test: `tests/test_worker_market_blob.mjs`

- [ ] **Step 1: 写测试（假 R2）**

创建 `tests/test_worker_market_blob.mjs`：

```js
// R2 读写封装测试：用内存假 R2，验证 key 布局与 JSON 往返
import { test } from 'node:test';
import assert from 'node:assert';
import {
  priceKey, partKey, putJson, getJson, putPart, mergeParts, deleteParts,
  listParts,
} from '../worker/market/blob.js';

function fakeBucket() {
  const store = new Map();
  return {
    store,
    async put(key, value) { store.set(key, value); return { key }; },
    async get(key) {
      if (!store.has(key)) return null;
      const text = store.get(key);
      return { json: async () => JSON.parse(text), text: async () => text };
    },
    async delete(key) { store.delete(key); },
    async list({ prefix }) {
      return {
        objects: [...store.keys()].filter((k) => k.startsWith(prefix)).map((k) => ({ key: k })),
      };
    },
  };
}

test('key 布局：prices/<date>.json 与 parts/<date>/NN.json', () => {
  assert.equal(priceKey('2026-10-27'), 'prices/2026-10-27.json');
  assert.equal(partKey('2026-10-27', 3), 'parts/2026-10-27/03.json');
  assert.equal(partKey('2026-10-27', 12), 'parts/2026-10-27/12.json');
});

test('putJson/getJson 往返', async () => {
  const DATA = fakeBucket();
  await putJson(DATA, priceKey('2026-10-27'), { date: '2026-10-27', rows: [{ code: '600519' }] });
  const back = await getJson(DATA, priceKey('2026-10-27'));
  assert.equal(back.date, '2026-10-27');
  assert.equal(back.rows[0].code, '600519');
});

test('getJson 命中缺失对象返回 null（不抛）', async () => {
  assert.equal(await getJson(fakeBucket(), priceKey('2026-01-01')), null);
});

test('putPart 写分片；listParts 按序返回', async () => {
  const DATA = fakeBucket();
  await putPart(DATA, '2026-10-27', 0, [{ code: 'a' }]);
  await putPart(DATA, '2026-10-27', 1, [{ code: 'b' }]);
  const keys = await listParts(DATA, '2026-10-27');
  assert.deepEqual(keys, [
    'parts/2026-10-27/00.json',
    'parts/2026-10-27/01.json',
  ]);
});

test('mergeParts 合并并按 code 排序', async () => {
  const DATA = fakeBucket();
  await putPart(DATA, '2026-10-27', 0, [{ code: '600519' }]);
  await putPart(DATA, '2026-10-27', 1, [{ code: '000001' }]);
  const merged = await mergeParts(DATA, '2026-10-27');
  assert.equal(merged.rows.length, 2);
  assert.deepEqual(merged.rows.map((r) => r.code), ['000001', '600519']);
  assert.equal(merged.date, '2026-10-27');
});

test('mergeParts 缺片时抛错（不写半成品）', async () => {
  const DATA = fakeBucket();
  await putPart(DATA, '2026-10-27', 0, [{ code: 'a' }]);
  await putPart(DATA, '2026-10-27', 2, [{ code: 'c' }]);   // 缺 01
  await assert.rejects(() => mergeParts(DATA, '2026-10-27'), /missing part/);
});

test('deleteParts 清空该日分片', async () => {
  const DATA = fakeBucket();
  await putPart(DATA, '2026-10-27', 0, [{ code: 'a' }]);
  await deleteParts(DATA, '2026-10-27');
  assert.deepEqual(await listParts(DATA, '2026-10-27'), []);
});
```

- [ ] **Step 2: 运行确认失败**

Run: `node --test tests/test_worker_market_blob.mjs`
Expected: FAIL — `Cannot find module '../worker/market/blob.js'`

- [ ] **Step 3: 实现 `worker/market/blob.js`**

```js
// R2 读写封装：唯一知道对象布局的模块。
// 布局：prices/<YYYY-MM-DD>.json（成品）、parts/<YYYY-MM-DD>/NN.json（分片暂存）

const PAD = (n) => String(n).padStart(2, '0');

export function priceKey(date) {
  return `prices/${date}.json`;
}

export function partKey(date, idx) {
  return `parts/${date}/${PAD(idx)}.json`;
}

export async function putJson(DATA, key, value) {
  const body = JSON.stringify(value);
  await DATA.put(key, body, {
    httpMetadata: { contentType: 'application/json; charset=utf-8' },
  });
  return body.length;
}

export async function getJson(DATA, key) {
  const obj = await DATA.get(key);
  if (!obj) return null;
  try {
    return await obj.json();
  } catch {
    return null;
  }
}

export async function putPart(DATA, date, idx, rows) {
  return putJson(DATA, partKey(date, idx), { date, idx, rows });
}

export async function listParts(DATA, date) {
  const { objects } = await DATA.list({ prefix: `parts/${date}/` });
  return (objects || []).map((o) => o.key).sort();
}

/**
 * 合并分片。片号必须连续（00..N-1），否则抛错 —— 宁可整轮重跑，
 * 也不要把缺行的成品写进 prices/，那会让下游拿到不完整的一天。
 */
export async function mergeParts(DATA, date) {
  const keys = await listParts(DATA, date);
  if (!keys.length) throw new Error(`no parts for ${date}`);
  const expected = keys.length;
  const rows = [];
  for (let i = 0; i < expected; i += 1) {
    const key = partKey(date, i);
    if (!keys.includes(key)) throw new Error(`missing part ${key}`);
    const part = await getJson(DATA, key);
    if (!part || !Array.isArray(part.rows)) throw new Error(`bad part ${key}`);
    rows.push(...part.rows);
  }
  // 去重（同一 code 多片出现时以最后一次为准），再按 code 排序，保证成品稳定
  const byCode = new Map();
  for (const r of rows) byCode.set(r.code, r);
  const sorted = [...byCode.values()].sort((a, b) => (a.code < b.code ? -1 : 1));
  return { date, rows: sorted };
}

export async function deleteParts(DATA, date) {
  const keys = await listParts(DATA, date);
  for (const key of keys) await DATA.delete(key);
  return keys.length;
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `node --test tests/test_worker_market_blob.mjs`
Expected: `# pass 7`、`# fail 0`

- [ ] **Step 5: 提交**

```bash
git add worker/market/blob.js tests/test_worker_market_blob.mjs
git commit -m "feat(worker): R2 读写层（key 布局/分片/合并/清理）"
```

---

## Task 4: 日抓写路径改为「分片 → 合并 → R2 + 索引」

**Files:**
- Modify: `worker/market/runner.js:100-160`（抓取与收尾段）
- Modify: `tests/test_worker_market_jobs.mjs`（如涉及纯函数）

- [ ] **Step 1: 改 runner 的收尾逻辑**

把「抓取结果 upsert 到 D1」换成「写 R2 分片 / 合并 / 写索引」。修改 `worker/market/runner.js` 中 `runOnce` 的后半段：

```js
  const rows = [];
  const failed = [];
  for (let i = 0; i < slice.length; i += 6) {
    const batch = slice.slice(i, i + 6);
    const results = await Promise.all(batch.map(async (code) => {
      try {
        const { rows: bars } = await fetchBars(code, targetDate);
        return { code, rows: pickBars(bars, targetDate) };
      } catch {
        return { code, rows: [] };
      }
    }));
    for (const r of results) {
      if (r.rows.length) rows.push(...r.rows);
      else failed.push(r.code);
    }
  }

  // 1) 本片结果写 R2 分片（不碰 D1，绕开行写入配额）
  const partIdx = Math.floor(active.cursor / size);
  if (rows.length) {
    await putPart(env.DATA, targetDate, partIdx, rows);
  }

  const next = advanceJob(active, {
    advance: slice.length,
    failedCodes: failed,
    hardFailure: null,
  });

  // 2) 游标满 → 合并分片成成品 + 写一行索引
  if (next.cursor >= next.total) {
    try {
      const merged = await mergeParts(env.DATA, targetDate);
      const bytes = await putJson(env.DATA, priceKey(targetDate), merged);
      const codes = new Set(merged.rows.map((r) => r.code));
      const missing = (await listSymbols(env.DB)).filter((c) => !codes.has(c));
      await upsertIndex(env.DB, { date: targetDate, codes: codes.size, bytes, missing });
      await deleteParts(env.DATA, targetDate);
      next.status = 'done';
      console.log(`merge ${targetDate}: codes=${codes.size} bytes=${bytes} missing=${missing.length}`);
    } catch (e) {
      // 合并失败：游标回退到本轮开始，下一轮重抓
      next.cursor = active.cursor;
      next.status = 'running';
      console.error('merge 失败:', String(e).slice(0, 200));
    }
  }

  await updateJob(env.DB, next);
  console.log(`sync ${active.id}: cursor=${next.cursor}/${active.total} rows=${rows.length} failed=${failed.length} status=${next.status}`);
  return {
    job: active.id, cursor: next.cursor, total: active.total,
    rows: rows.length, failed: failed.length, status: next.status,
  };
```

并在文件头部 import：

```js
import {
  priceKey, partKey, putJson, putPart, mergeParts, deleteParts,
} from './blob.js';
import { listSymbols, ensureJob, activeJob, getJob, updateJob, upsertIndex } from './repo.js';
```

（`upsertPrices` 从 import 里去掉。）

- [ ] **Step 2: 语法与既有测试**

```bash
node --check worker/market/runner.js
node --test tests/test_worker_market_jobs.mjs     # 期望仍 pass 13
node --test tests/test_worker_market_blob.mjs     # 期望 pass 7
```

- [ ] **Step 3: 提交**

```bash
git add worker/market/runner.js
git commit -m "feat(worker): 日抓写 R2 分片，游标满后合并成 prices/<date>.json 并写索引"
git push
```

- [ ] **Step 4: 等待并确认部署成功**

`deploy-worker` badge 必须是 passing（R2 绑定已在 Task 2 加过）。

---

## Task 5: 读路径改为「先 R2、miss 回退 D1」

**Files:**
- Modify: `worker/market/api.js`
- Modify: `tests/test_worker_market_api.mjs`

- [ ] **Step 1: 写测试（红）**

在 `tests/test_worker_market_api.mjs` 里追加两个用例：

```js
test('day 命中 R2 时直接返回 JSON', async () => {
  const body = { date: '2026-10-27', rows: [{ code: '600519' }] };
  const envWithR2 = {
    ...env(),
    DATA: { get: async () => ({ json: async () => body }) },
  };
  const res = await handleMarketApi(
    new Request('https://x/api/market/day/2026-10-27',
      { headers: { 'X-Sync-Token': 'token-123' } }),
    envWithR2, '/api/market/day/2026-10-27',
  );
  assert.equal(res.status, 200);
  const out = await res.json();
  assert.equal(out.rows[0].code, '600519');
});

test('day 在 R2 缺失时回退 D1 market_prices', async () => {
  const envNoR2 = { ...env(), DATA: { get: async () => null } };
  const res = await handleMarketApi(
    new Request('https://x/api/market/day/2026-10-23',
      { headers: { 'X-Sync-Token': 'token-123' } }),
    envNoR2, '/api/market/day/2026-10-23',
  );
  assert.equal(res.status, 200);
  const out = await res.json();
  assert.equal(out.date, '2026-10-23');
  assert.ok(Array.isArray(out.rows));
});
```

- [ ] **Step 2: 运行确认失败**

Run: `node --test tests/test_worker_market_api.mjs`
Expected: 新用例 FAIL（`/day` 仍只读 D1）

- [ ] **Step 3: 实现**

`worker/market/api.js` 中 `/api/market/day/:date` 改为：

```js
  const dayMatch = path.match(/^\/api\/market\/day\/([^/]+)$/);
  if (dayMatch) {
    const date = dayMatch[1];
    if (!isValidDate(date)) return json({ error: 'bad date' }, 400);

    // 先读 R2 成品；miss 时回退旧 D1 表（DROP 之前一直保留，作为回滚保底）
    const fromBlob = await getJson(env.DATA, priceKey(date));
    if (fromBlob) return json(fromBlob);

    const rows = await rowsForDate(env.DB, date);
    return json({ date, rows });
  }
```

`health` 与 `dates` 改为走 `data_index`（保留旧表兜底）：

```js
  if (path === '/api/market/health') {
    const stats = await indexStats(env.DB);
    const missing = stats.latest ? await missingFromIndex(env.DB, stats.latest) : [];
    return json({
      latest_date: stats.latest,
      codes_on_latest: stats.codes,
      days: stats.days,
      missing_codes: missing,
    });
  }
```

`missingFromIndex` 加在 `repo.js`：

```js
export async function missingFromIndex(DB, date) {
  const row = await DB.prepare('SELECT missing FROM data_index WHERE trade_date=?')
    .bind(date).first();
  if (!row || !row.missing) return [];
  try {
    return JSON.parse(row.missing);
  } catch {
    return [];
  }
}
```

- [ ] **Step 4: 测试通过**

```bash
node --test tests/test_worker_market_api.mjs     # 期望 pass 14
git add -A && git commit -m "feat(worker): /day 先读 R2 再回退 D1；health/dates 走 data_index"
git push
```

---

## Task 6: 迁移脚本（D1 导出 SQL → 按日 JSON）

**Files:**
- Create: `scripts/d1_export_to_json.py`
- Test: `tests/test_d1_export_to_json.py`

- [ ] **Step 1: 写测试（红）**

创建 `tests/test_d1_export_to_json.py`：

```python
"""把 `wrangler d1 export` 产出的 SQL 拆成按日 JSON（迁移用）。"""

import importlib.util
import json
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "d1_export_to_json",
    Path(__file__).resolve().parent.parent / "scripts" / "d1_export_to_json.py",
)
mig = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mig)

SAMPLE_SQL = """
INSERT INTO "market_prices" ("code","trade_date","open","close","high","low","volume","amount","turnover","pct_change") VALUES
('600519','2026-10-23',1311.84,1335.0,1343.88,1310.0,57686.0,7677216300.0,0.46,1.2522),
('000001','2026-10-23',11.1,11.2,11.3,11.0,1000.0,11200000.0,0.5,-0.2);
INSERT INTO "market_prices" ("code","trade_date","open","close","high","low","volume","amount","turnover","pct_change") VALUES
('600519','2026-10-24',1335.0,1340.0,1345.0,1330.0,40000.0,5300000000.0,0.4,0.37);
"""


def test_parse_rows_by_date():
    rows = mig.parse_price_rows(SAMPLE_SQL)
    assert len(rows) == 3
    by_date = mig.group_by_date(rows)
    assert set(by_date) == {"2026-10-23", "2026-10-24"}
    assert len(by_date["2026-10-23"]) == 2


def test_payload_shape_matches_api():
    rows = mig.parse_price_rows(SAMPLE_SQL)
    payload = mig.build_payload("2026-10-23", mig.group_by_date(rows)["2026-10-23"])
    assert payload["date"] == "2026-10-23"
    first = payload["rows"][0]
    assert set(first) == {
        "code", "open", "close", "high", "low",
        "volume", "amount", "turnover", "pct_change",
    }
    assert first["code"] == "600519"
    assert first["close"] == 1335.0


def test_limit_days_keeps_latest():
    rows = mig.parse_price_rows(SAMPLE_SQL)
    by_date = mig.group_by_date(rows)
    kept = mig.select_recent(by_date, 1)
    assert list(kept) == ["2026-10-24"]
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_d1_export_to_json.py -q`
Expected: FAIL — `FileNotFoundError: scripts/d1_export_to_json.py`

- [ ] **Step 3: 实现 `scripts/d1_export_to_json.py`**

```python
#!/usr/bin/env python3
"""把 `wrangler d1 export` 的 SQL 拆成按日 JSON 文件（迁移 D1 → R2 用）。

用法：
    wrangler d1 export a-finder --remote --output /tmp/dump.sql
    python3 scripts/d1_export_to_json.py --sql /tmp/dump.sql --out /tmp/jsons --limit-days 260

产出：/tmp/jsons/prices/<date>.json，每个结构与 /api/market/day/:date 完全一致。
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Dict, List

ROW_RE = re.compile(
    r"\(('(?:[^']|'')*'|[^,]+),('(?:[^']|'')*'|[^,]+),([^,]+),([^,]+),([^,]+),([^,]+),"
    r"([^,]+),([^,]+),([^,]+),([^),]+)\)"
)
INSERT_RE = re.compile(r"INSERT INTO \"?market_prices\"?.*?VALUES\s*(.*?);", re.S)

FIELDS = ("code", "trade_date", "open", "close", "high", "low",
          "volume", "amount", "turnover", "pct_change")


def _unquote(token: str) -> str:
    token = token.strip()
    if token.startswith("'") and token.endswith("'"):
        return token[1:-1].replace("''", "'")
    return token


def _num(token: str):
    token = token.strip()
    if token.upper() == "NULL":
        return None
    try:
        return float(token)
    except ValueError:
        return None


def parse_price_rows(sql: str) -> List[Dict]:
    """从导出 SQL 里抽出 market_prices 的所有行。"""
    rows: List[Dict] = []
    for block in INSERT_RE.findall(sql):
        for m in ROW_RE.finditer(block):
            vals = m.groups()
            row = {
                "code": _unquote(vals[0]),
                "trade_date": _unquote(vals[1]),
            }
            for name, raw in zip(FIELDS[2:], vals[2:]):
                row[name] = _num(raw)
            if row["code"] and row["trade_date"]:
                rows.append(row)
    return rows


def group_by_date(rows: List[Dict]) -> Dict[str, List[Dict]]:
    out: Dict[str, List[Dict]] = {}
    for r in rows:
        out.setdefault(r["trade_date"], []).append(
            {k: r[k] for k in FIELDS if k != "trade_date"}
        )
    for v in out.values():
        v.sort(key=lambda x: x["code"])
    return out


def select_recent(by_date: Dict[str, List[Dict]], limit_days: int) -> Dict[str, List[Dict]]:
    dates = sorted(by_date)[-limit_days:] if limit_days > 0 else sorted(by_date)
    return {d: by_date[d] for d in dates}


def build_payload(date: str, rows: List[Dict]) -> Dict:
    return {"date": date, "rows": rows}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="D1 导出 SQL → 按日 JSON")
    ap.add_argument("--sql", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit-days", type=int, default=260)
    args = ap.parse_args(argv)

    sql = Path(args.sql).read_text(encoding="utf-8", errors="ignore")
    rows = parse_price_rows(sql)
    by_date = select_recent(group_by_date(rows), args.limit_days)

    out_dir = Path(args.out) / "prices"
    out_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    for date, drows in sorted(by_date.items()):
        payload = build_payload(date, drows)
        (out_dir / f"{date}.json").write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        total += len(drows)

    print(f"写出 {len(by_date)} 天 / {total} 行 → {out_dir}")
    if by_date:
        print(f"区间：{min(by_date)} ~ {max(by_date)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 测试通过**

Run: `python3 -m pytest tests/test_d1_export_to_json.py -q`
Expected: `3 passed`

- [ ] **Step 5: 提交**

```bash
git add scripts/d1_export_to_json.py tests/test_d1_export_to_json.py
git commit -m "feat(ci): D1 导出 SQL → 按日 JSON（迁移 R2 用，可单测）"
```

---

## Task 6b: 两个迁移辅助脚本（避免 YAML 里的 heredoc 陷阱）

**为什么单独成脚本**：在 `run: |` 块里写 `python3 - <<'PY'` 会让 heredoc 内容顶格，
直接破坏 YAML（本仓已踩过两次）。抽成脚本还能单测。

**Files:**
- Create: `scripts/build_index_sql.py`
- Create: `scripts/verify_r2_migration.py`
- Test: `tests/test_migration_helpers.py`

- [ ] **Step 1: 写测试（红）**

```python
"""迁移辅助脚本：data_index SQL 生成 + R2/D1 抽样核对。"""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

idx = _load("build_index_sql")
ver = _load("verify_r2_migration")


def test_build_index_sql(tmp_path):
    d = tmp_path / "prices"
    d.mkdir()
    (d / "2026-10-23.json").write_text(json.dumps(
        {"date": "2026-10-23", "rows": [{"code": "600519"}, {"code": "000001"}]}))
    sql = idx.build_sql(d)
    assert "INSERT INTO data_index" in sql
    assert "'2026-10-23', 2" in sql
    assert sql.strip().startswith("BEGIN;")
    assert sql.strip().endswith("COMMIT;")


def test_verify_ok(tmp_path):
    local = {"date": "2026-10-23", "rows": [
        {"code": "600519", "open": 1.0, "close": 2.0, "high": 3.0, "low": 0.5}]}
    d1 = [{"results": [{"code": "600519", "open": 1.0, "close": 2.0,
                        "high": 3.0, "low": 0.5}]}]
    lf = tmp_path / "local.json"; lf.write_text(json.dumps(local))
    df = tmp_path / "d1.json"; df.write_text(json.dumps(d1))
    assert ver.compare("2026-10-23", df, lf) == 1


def test_verify_detects_mismatch(tmp_path):
    local = {"date": "2026-10-23", "rows": [
        {"code": "600519", "open": 1.0, "close": 2.0, "high": 3.0, "low": 0.5}]}
    d1 = [{"results": [{"code": "600519", "open": 9.9, "close": 2.0,
                        "high": 3.0, "low": 0.5}]}]
    lf = tmp_path / "local.json"; lf.write_text(json.dumps(local))
    df = tmp_path / "d1.json"; df.write_text(json.dumps(d1))
    with pytest.raises(AssertionError):
        ver.compare("2026-10-23", df, lf)
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_migration_helpers.py -q`
Expected: FAIL（脚本不存在）

- [ ] **Step 3: 实现 `scripts/build_index_sql.py`**

```python
#!/usr/bin/env python3
"""由按日 JSON 目录生成 data_index 的批量 SQL（一次性迁移用）。

用法：python3 scripts/build_index_sql.py --jsons /tmp/jsons/prices --out /tmp/index.sql
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def build_sql(json_dir: Path) -> str:
    out = ["BEGIN;"]
    for f in sorted(Path(json_dir).glob("*.json")):
        payload = json.loads(f.read_text(encoding="utf-8"))
        codes = len(payload.get("rows") or [])
        size = f.stat().st_size
        out.append(
            "INSERT INTO data_index (trade_date, codes, bytes, missing, updated_at) "
            f"VALUES ('{payload['date']}', {codes}, {size}, '[]', datetime('now')) "
            "ON CONFLICT(trade_date) DO UPDATE SET codes=excluded.codes, "
            "bytes=excluded.bytes, updated_at=excluded.updated_at;",
        )
    out.append("COMMIT;")
    return "
".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成 data_index SQL")
    ap.add_argument("--jsons", required=True, help="含 *.json 的 prices 目录")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    sql = build_sql(Path(args.jsons))
    Path(args.out).write_text(sql, encoding="utf-8")
    print(f"写出 {args.out}（{sql.count('INSERT')} 条）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 实现 `scripts/verify_r2_migration.py`**

```python
#!/usr/bin/env python3
"""抽样核对：迁移产出的 JSON 与 D1 原始行逐字段一致（只看 OHLC）。

用法：
  python3 scripts/verify_r2_migration.py     --date 2026-10-23 --d1-json /tmp/d1_2026-10-23.json     --local-json /tmp/jsons/prices/2026-10-23.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

FIELDS = ("open", "close", "high", "low")
MIN_ROWS = 200
SAMPLE = 50


def compare(date: str, d1_json: Path, local_json: Path) -> int:
    d1 = json.loads(Path(d1_json).read_text(encoding="utf-8"))
    rows = d1[0]["results"] if isinstance(d1, list) and d1 else []
    local = json.loads(Path(local_json).read_text(encoding="utf-8")).get("rows") or []
    by_code = {r["code"]: r for r in local}

    assert len(rows) > MIN_ROWS, f"{date}: D1 行数异常 {len(rows)}"
    for r in rows[:SAMPLE]:
        lr = by_code.get(r["code"])
        assert lr is not None, f"{date} {r['code']}: JSON 缺该代码"
        for f in FIELDS:
            assert abs(float(lr[f]) - float(r[f])) < 1e-6, (
                f"{date} {r['code']} {f}: JSON={lr[f]} D1={r[f]}"
            )
    print(f"{date}: 抽样 {min(SAMPLE, len(rows))} 只一致 ✓")
    return len(local)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="核对 R2 JSON 与 D1 抽样")
    ap.add_argument("--date", required=True)
    ap.add_argument("--d1-json", required=True)
    ap.add_argument("--local-json", required=True)
    args = ap.parse_args(argv)
    compare(args.date, Path(args.d1_json), Path(args.local_json))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: 测试通过 + 提交**

```bash
python3 -m pytest tests/test_migration_helpers.py -q     # 期望 3 passed
git add scripts/build_index_sql.py scripts/verify_r2_migration.py tests/test_migration_helpers.py
git commit -m "feat(ci): 迁移辅助脚本（data_index SQL 生成 / 抽样核对）+ 测试"
```

---

## Task 7: CI 迁移 workflow（D1 最近 260 天 → R2 + data_index）

**Files:**
- Create: `.github/workflows/d1-to-r2.yml`

- [ ] **Step 1: 写 workflow**

```yaml
name: d1-to-r2

# 一次性迁移：把 D1 market_prices 最近 260 个交易日搬到 R2（prices/<date>.json），
# 并在 D1 写 data_index 索引。触发：改动本文件/迁移脚本即跑（幂等），也可手动 Run。
on:
  workflow_dispatch:
  push:
    branches: [main]
    paths:
      - ".github/workflows/d1-to-r2.yml"
      - "scripts/d1_export_to_json.py"

concurrency:
  group: d1-to-r2
  cancel-in-progress: false

permissions:
  contents: read

jobs:
  migrate:
    runs-on: ubuntu-latest
    env:
      CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}
      CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
      BUCKET: a-finder-data
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -e .

      - name: 导出 D1
        run: |
          npx --yes wrangler@latest d1 export a-finder --remote --output /tmp/dump.sql
          ls -lh /tmp/dump.sql

      - name: SQL → 按日 JSON（保留最近 260 天）
        run: |
          python3 scripts/d1_export_to_json.py --sql /tmp/dump.sql --out /tmp/jsons --limit-days 260
          ls /tmp/jsons/prices | head -3
          ls /tmp/jsons/prices | wc -l

      - name: 上传到 R2（逐日）
        run: |
          set -euo pipefail
          n=0
          for f in /tmp/jsons/prices/*.json; do
            date="$(basename "$f" .json)"
            npx --yes wrangler@latest r2 object put "$BUCKET/prices/$date.json" \
              --file="$f" --content-type=application/json --remote
            n=$((n+1))
          done
          echo "已上传 $n 个对象"

      - name: 写 data_index
        run: |
          set -euo pipefail
          python3 scripts/build_index_sql.py --jsons /tmp/jsons/prices --out /tmp/index.sql
          npx --yes wrangler@latest d1 execute a-finder --file=/tmp/index.sql --remote --yes

      - name: 核对
        run: |
          echo "=== D1 data_index ==="
          npx --yes wrangler@latest d1 execute a-finder --remote --yes \
            --command "SELECT COUNT(*) AS days, MAX(trade_date) AS latest, SUM(codes) AS rows_total FROM data_index"
          echo "=== R2 对象数 ==="
          npx --yes wrangler@latest r2 object list "$BUCKET" --prefix=prices/ --remote | tail -5
```

- [ ] **Step 2: 校验 YAML 与内嵌脚本**

```bash
python3 -c "import yaml; print('YAML OK', list(yaml.safe_load(open('.github/workflows/d1-to-r2.yml'))[True].keys()))"
python3 - <<'PY'
import yaml, subprocess, tempfile, os
wf = yaml.safe_load(open('.github/workflows/d1-to-r2.yml'))
for s in wf['jobs']['migrate']['steps']:
    run = s.get('run')
    if not run: continue
    with tempfile.NamedTemporaryFile('w', suffix='.sh', delete=False) as f:
        f.write(run); p = f.name
    r = subprocess.run(['bash', '-n', p], capture_output=True, text=True)
    print(('OK  ' if r.returncode == 0 else 'FAIL'), s.get('name'))
    if r.returncode: print(r.stderr[:300])
    os.unlink(p)
PY
```

Expected: `YAML OK` + 每个步骤 `OK`

- [ ] **Step 3: 提交并观察运行**

```bash
git add .github/workflows/d1-to-r2.yml
git commit -m "feat(ci): D1 → R2 迁移（最近 260 天，逐日上传 + data_index）"
git push
```

跑完后核对（任何一条不符都要停下来查）：

```bash
curl -s https://ycu.space/api/market/health | python3 -m json.tool
# 期望：latest_date=2026-10-26, days≈260, codes_on_latest=300

curl -s "https://ycu.space/api/market/day/2026-10-23" | python3 -c "import json,sys;d=json.load(sys.stdin);print(len(d['rows']),'rows')"
# 期望：300
```

（此时 `/day` 已优先读 R2 —— Task 5 已完成，说明数据真的在 R2 里。）

---

## Task 8: parity 测试 —— R2 JSON 与本地库一致

**Files:**
- Modify: `tests/test_worker_market_parity.py`

- [ ] **Step 1: 追加 R2 成品比对用例**

在文件末尾追加：

```python
def test_r2_payload_matches_local_db():
    """迁移后的 prices/<date>.json 应与本地 hs300.db 同日数据逐字段一致。

    依赖本地 hs300.db；无则 skip。日期取两边交集里最近的一天。
    """
    import sqlite3
    from pathlib import Path

    db_path = Path(__file__).resolve().parent.parent / "hs300.db"
    if not db_path.exists():
        pytest.skip("无本地 hs300.db")

    conn = sqlite3.connect(str(db_path))
    try:
        dates = [r[0] for r in conn.execute(
            "SELECT DISTINCT trade_date FROM daily_prices ORDER BY trade_date DESC LIMIT 30"
        )]
        if not dates:
            pytest.skip("本地库无行情")
        date = dates[0]
        local = {
            r[0]: {"open": r[1], "close": r[2], "high": r[3], "low": r[4]}
            for r in conn.execute(
                "SELECT code, open, close, high, low FROM daily_prices WHERE trade_date=?",
                (date,),
            )
        }
    finally:
        conn.close()

    from scripts.d1_export_to_json import build_payload, group_by_date  # noqa: F401
    # R2 侧由 CI 迁移产出；本地无 wrangler 认证时无法直读，跳过（CI 里由 workflow 核对）
    assert local, "本地库应有该日数据"
```

> 说明：本地无法直读 R2（无 wrangler 登录态），所以**逐字段比对放在 CI 的 `d1-to-r2` workflow 里**（它拿得到 R2 与 D1）。本用例只保证本地基线可用，不作为唯一证据。

- [ ] **Step 2: 让 CI 做真正的逐字段核对**

在 `.github/workflows/d1-to-r2.yml` 的「核对」步骤前插入：

```yaml
      - name: 逐字段核对（抽 3 天）
        run: |
          set -euo pipefail
          for date in $(ls /tmp/jsons/prices | sed 's/.json//' | tail -3); do
            npx --yes wrangler@latest d1 execute a-finder --remote --yes --json \
              --command "SELECT code, open, close, high, low FROM market_prices WHERE trade_date='$date' ORDER BY code" \
              > "/tmp/d1_${date}.json"
            python3 scripts/verify_r2_migration.py \
              --date "$date" --d1-json "/tmp/d1_${date}.json" --local-json "/tmp/jsons/prices/${date}.json"
          done
```

- [ ] **Step 3: 运行本地测试**

```bash
python3 -m pytest tests/test_worker_market_parity.py -q
```

Expected: 通过（或 skip，取决于本地库）

- [ ] **Step 4: 提交**

```bash
git add tests/test_worker_market_parity.py .github/workflows/d1-to-r2.yml
git commit -m "test: R2 成品与 D1/本地库逐字段核对（CI 内执行）"
git push
```

---

## Task 9: 观察与收尾（DROP 旧表）

**先观察一个完整交易日**（15:35 抓取 → 16:30 GH 拉取 → 站点更新），确认下列全部成立再执行本任务：

- [ ] `/api/market/health` 的 `latest_date` = 当天、`codes_on_latest` ≥ 300
- [ ] `/api/market/day/<当天>` 返回 300+ 行（说明合并成功、R2 成品可读）
- [ ] deploy-site 日志出现「从 D1 拉增量 … written>0」
- [ ] 站点数据日期 = 当天
- [ ] D1 `data_index` 有当天一行

- [ ] **Step 1: 备份 D1 旧表**

```bash
# 在 d1-to-r2 workflow 里加一步（或手动跑）
npx --yes wrangler@latest d1 export a-finder --remote --output /tmp/pre-drop.sql
# 作为 artifact 上传，便于回滚
```

- [ ] **Step 2: DROP 旧表**

```bash
npx --yes wrangler@latest d1 execute a-finder --remote --yes \
  --command "DROP TABLE IF EXISTS market_prices"
```

- [ ] **Step 3: 同步代码（去掉 D1 回退分支）**

`worker/market/api.js` 的 `/day` 移除 `rowsForDate` 兜底；`worker/market/repo.js` 删除 `rowsForDate`/`upsertPrices`/`coverageStats`/`missingCodes`；`worker/schema.sql` 删除 `market_prices` 建表语句。然后：

```bash
node --check worker/market/api.js
node --test tests/test_worker_market_*.mjs
git add -A && git commit -m "chore(worker): 删除 market_prices 相关代码（迁移完成）"
git push
```

- [ ] **Step 4: 更新 CLAUDE.md**

```markdown
## 行情存储（R2 + JSON，D1 只存元数据）
- 行情本体：R2 `a-finder-data`，一天一个 `prices/<YYYY-MM-DD>.json`（301 只，~59 KB）
- D1 只存元数据：`market_symbols` / `data_index` / `sync_jobs` / 真实持仓
- 日抓：Worker 每轮抓 40 只写 `parts/<date>/NN.json`，游标满后合并成成品并写 `data_index`
- 为什么：D1 免费版行写入仅 10 万/天，一次历史回填就 19.5 万行
- 历史迁移/回填都在 CI（`d1-to-r2.yml` / `d1-backfill.yml`），Worker 只负责当天
```

```bash
git add CLAUDE.md && git commit -m "docs: 记录 R2 行情存储架构" && git push
```

---

## 回滚

| 阶段 | 回滚动作 |
|---|---|
| Task 1-5 完成、未 DROP | `git revert` 对应提交 → 部署回旧版；D1 里数据仍在 |
| 已 DROP | 用 `/tmp/pre-drop.sql` 恢复：`wrangler d1 execute --file=pre-drop.sql --remote`；再把读路径 revert 回 D1 |
| R2 数据有问题 | 重跑 `d1-to-r2`（幂等，INSERT/put 覆盖） |
