# 退役 Worker 行情层 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 删除 Worker 的行情抓取/存储层，数据路径回到「GitHub Actions 直连数据源 → SQLite 缓存 → gh-pages 静态 JSON」，使全部 Cloudflare 资源落在免费额度内。

**Architecture:** `deploy-site.yml` 的同步步骤改回直连 `sync-incremental`（保留 3 轮重试与数据门禁）；Worker 只保留 `/api/decide/*` 与 07:35 dispatch cron；D1 只保留 `real_positions` / `real_cb_positions`。

**Tech Stack:** Cloudflare Workers (Free) · D1 · GitHub Actions · Python 3.11 + pytest · `node --test`

**Spec:** [`docs/superpowers/specs/2026-10-27-retire-worker-market-design.md`](../specs/2026-10-27-retire-worker-market-design.md)

---

## 文件结构

| 文件 | 职责 | 状态 |
|---|---|---|
| `.github/workflows/d1-backup.yml` | 一次性备份 D1（DROP 前的唯一保险） | 新建（Task 1，Task 6 可删） |
| `.github/workflows/deploy-site.yml` | 同步步骤改回直连 + 重试 + 校验 | 修改 |
| `worker/index.js` | 去掉 market 路由分发与 `runMarketSync` | 修改 |
| `wrangler.toml` | 去掉每分钟 cron | 修改 |
| `worker/schema.sql` | 删除 4 张行情/任务表 | 修改 |
| `worker/market/` | 整个目录 | 删除 |
| `scripts/pull_from_d1.py`、`scripts/seed_d1.py` | 无 D1 行情可拉/可灌 | 删除 |
| `.github/workflows/d1-seed.yml`、`d1-backfill.yml` | 同上 | 删除 |
| `tests/test_worker_market_*.mjs`、`test_pull_from_d1.py`、`test_seed_d1.py`、`test_worker_market_parity.py` | 随实现删除 | 删除 |
| `scripts/check_schema_tables.py` | 校验 schema 只剩期望的表（迁移期与将来都用得上） | 新建 |
| `CLAUDE.md` | 「Worker 抓取行情」整节改为一行说明 | 修改 |

---

## Task 1: 备份 D1（DROP 前的唯一保险）

**Files:**
- Create: `.github/workflows/d1-backup.yml`

- [ ] **Step 1: 写备份 workflow**

```yaml
name: d1-backup

# 一次性：导出 D1 全量 SQL 作为 artifact（DROP TABLE 之前必须跑过）。
# 触发：改动本文件即跑，也可手动 Run。
on:
  workflow_dispatch:
  push:
    branches: [main]
    paths:
      - ".github/workflows/d1-backup.yml"

permissions:
  contents: read

jobs:
  backup:
    runs-on: ubuntu-latest
    env:
      CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}
      CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
    steps:
      - uses: actions/checkout@v4

      - name: 导出 D1
        run: |
          STAMP="$(TZ=Asia/Shanghai date +%Y%m%d-%H%M)"
          npx --yes wrangler@latest d1 export a-finder --remote --output "/tmp/d1-${STAMP}.sql"
          ls -lh /tmp/d1-*.sql
          echo "STAMP=${STAMP}" >> "$GITHUB_ENV"

      - name: 上传 artifact（保留 90 天）
        uses: actions/upload-artifact@v4
        with:
          name: d1-backup-${{ env.STAMP }}
          path: /tmp/d1-*.sql
          retention-days: 90

      - name: 记录表行数（便于事后核对）
        run: |
          npx --yes wrangler@latest d1 execute a-finder --remote --yes \
            --command "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
          npx --yes wrangler@latest d1 execute a-finder --remote --yes \
            --command "SELECT COUNT(*) AS market_prices_rows FROM market_prices"
```

- [ ] **Step 2: 校验 YAML 与内嵌脚本**

```bash
python3 -c "import yaml; print('YAML OK', list(yaml.safe_load(open('.github/workflows/d1-backup.yml'))[True].keys()))"
python3 - <<'PY'
import yaml, subprocess, tempfile, os
wf = yaml.safe_load(open('.github/workflows/d1-backup.yml'))
for s in wf['jobs']['backup']['steps']:
    if not s.get('run'): continue
    with tempfile.NamedTemporaryFile('w', suffix='.sh', delete=False) as f:
        f.write(s['run']); p = f.name
    r = subprocess.run(['bash','-n',p], capture_output=True, text=True)
    print(('OK  ' if r.returncode==0 else 'FAIL'), s.get('name'))
    if r.returncode: print(r.stderr[:200])
    os.unlink(p)
PY
```

Expected: `YAML OK` + 每个 run 步骤 `OK`

- [ ] **Step 3: 提交并确认备份成功**

```bash
git add .github/workflows/d1-backup.yml
git commit -m "chore(ci): D1 全量备份 workflow（DROP 前的保险）"
git push
```

等 CI 跑完，在 Actions 页面确认 artifact `d1-backup-<时间戳>` 已生成且体积合理（应 > 5 MB）。

**这一步没成功就不要继续后面的步骤。**

---

## Task 2: 同步步骤改回直连（先恢复数据链）

**目的**：让行情链在不依赖 Worker 的情况下先跑通，再拆 Worker。顺序反了会出现"两边都不工作"的窗口。

**Files:**
- Modify: `.github/workflows/deploy-site.yml`（把「上传成分股到 D1」「从 D1 拉增量」「回退直连」「行情校验」四个步骤，换回单一的「增量同步行情（短窗口 + 重试到落地）」）

- [ ] **Step 1: 用 git 历史里的直连版本替换**

```bash
# 确认要替换的四步当前的起止行
grep -n "上传成分股到 D1\|从 D1 拉增量\|回退直连\|行情校验（决定是否继续补抓）\|选股 + 计划 + 组合 + 可转债" .github/workflows/deploy-site.yml
```

把「上传成分股到 D1」到「行情校验」之间的内容整体替换为下面这一段（与 `git show e2d7b74^:.github/workflows/deploy-site.yml` 中的版本一致）：

```yaml
      - name: 增量同步行情（短窗口 + 重试到落地）
        if: steps.trade-day.outputs.is_trade_day == 'true' && steps.freshness.outputs.skip != 'true'
        env:
          EXPECT: ${{ steps.trade-day.outputs.today_bj }}
          # 日常只补最近 60 天：请求少 → 限流概率低 → 当天更可能同步成功
          SYNC_DAYS: "60"
          SYNC_RETRIES: "2"
          SYNC_CONCURRENCY: "6"
          SYNC_RATE: "6"
        run: |
          set -uo pipefail
          COLD=0
          [ -f hs300.db ] || COLD=1
          if [ "$COLD" = "1" ]; then
            echo "::notice::无缓存 DB，冷启动：扩大窗口到 400 天"
            export SYNC_DAYS=400 SYNC_CONCURRENCY=8 SYNC_RATE=8 SYNC_RETRIES=3
          fi
          ok=0
          for attempt in 1 2 3; do
            echo "── 第 $attempt 次同步 ──"
            SYNC_END_DATE="$EXPECT" \
              bash a-finder.sh sync-incremental hs300.db 20 pick-history 2>&1 | tail -40 || true
            if python3 scripts/ci_check_data.py --db hs300.db --expect "$EXPECT" --prices-only --source direct; then
              ok=1; break
            fi
            if [ "$attempt" -lt 3 ]; then
              echo "行情未就绪（数据源可能延迟），120s 后重试…"
              sleep 120
            fi
          done
          if [ "$ok" = "1" ]; then
            echo "prices_ok=true" >> "$GITHUB_OUTPUT"
          else
            echo "prices_ok=false" >> "$GITHUB_OUTPUT"
            echo "::warning::三次同步后期望交易日 $EXPECT 的行情仍不完整"
          fi
        id: sync
```

同时把最后的「数据完整性门禁」步骤里的 `--source` 去掉（或固定为 `direct`）：

```yaml
      - name: 数据完整性门禁
        if: always() && steps.trade-day.outputs.is_trade_day == 'true' && steps.freshness.outputs.skip != 'true'
        env:
          EXPECT: ${{ steps.trade-day.outputs.today_bj }}
        run: |
          set -uo pipefail
          python3 scripts/ci_check_data.py --db hs300.db --expect "$EXPECT" --source direct
          rc=$?
          if [ "$rc" -ne 0 ]; then
            echo "::error::数据不完整：期望 $EXPECT，详见上方校验输出（站点已按最新可得数据部署）"
            exit 1
          fi
          echo "✅ 数据校验通过：$EXPECT"
```

- [ ] **Step 2: 校验**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/deploy-site.yml')); print('YAML OK')"
python3 - <<'PY'
import yaml, subprocess, tempfile, os
wf = yaml.safe_load(open('.github/workflows/deploy-site.yml'))
bad = 0
for s in wf['jobs']['deploy']['steps']:
    if not s.get('run'): continue
    with tempfile.NamedTemporaryFile('w', suffix='.sh', delete=False) as f:
        f.write(s['run']); p = f.name
    r = subprocess.run(['bash','-n',p], capture_output=True, text=True)
    print(('OK  ' if r.returncode==0 else 'FAIL'), s.get('name'))
    bad += 0 if r.returncode==0 else 1
    os.unlink(p)
print('失败数:', bad)
PY
grep -c "D1 拉增量\|上传成分股" .github/workflows/deploy-site.yml    # 期望 0
```

Expected: `YAML OK`、`失败数: 0`、最后一条输出 `0`

- [ ] **Step 3: 提交（不 push）**

```bash
git add .github/workflows/deploy-site.yml
git commit -m "feat(ci): 同步步骤改回直连数据源（退役 Worker 抓取的前置）"
```

---

## Task 3: 手动验证直连链路

- [ ] **Step 1: 触发一次 deploy-site**

在 Actions 页面手动 Run `deploy-site`（或等下一次 15:30 定时）。等待完成后检查：

| 检查项 | 期望 |
|---|---|
| 「增量同步行情」步骤 | 成功，日志出现「第 1 次同步」或「行情未就绪…」后仍成功 |
| 「数据完整性门禁」 | `✅ 数据校验通过`，且打印 `数据来源: direct` |
| 「选股 + 计划」 | 日志出现 `行情最新日: YYYY-MM-DD` |
| 站点 | `curl -s https://dllen.github.io/a-finder-cli/data/dates.json \| head -c 100` 首日 = 当天（交易日） |

- [ ] **Step 2: 失败则排查（不要继续拆分 Worker）**

```bash
# 本地复跑同一条命令，看真实报错
bash a-finder.sh sync-incremental hs300.db 20 pick-history
python3 scripts/ci_check_data.py --db hs300.db            # 默认以「最近工作日」为期望日
```

- [ ] **Step 3: 通过后 push**

```bash
git push
```

---

## Task 4: 删除 Worker 行情代码 + 每分钟 cron

**Files:**
- Delete: `worker/market/`（整目录）
- Modify: `worker/index.js`
- Modify: `wrangler.toml`

- [ ] **Step 1: 精简 `worker/index.js`**

删除以下内容：
1. 文件顶部的 `import { handleMarketApi } from './market/api.js';` 与 `import { runMarketSync } from './market/runner.js';`
2. `fetch` 处理器里这两行：
   ```js
   const marketRes = await handleMarketApi(request, env, path);
   if (marketRes) return marketRes;
   ```
3. `scheduled` 处理器里抓取那段（保留 dispatch 那段）：
   ```js
   try {
     await runMarketSync(env);
   } catch (err) {
     console.error('runMarketSync 异常:', String(err).slice(0, 300));
   }
   ```

改完后 `scheduled` 应只剩 dispatch 一个 `ctx.waitUntil`。

- [ ] **Step 2: 删除目录与 cron**

```bash
git rm -r worker/market
python3 - <<'PY'
import pathlib
p = pathlib.Path('wrangler.toml')
s = p.read_text()
s = s.replace('crons = ["35 7 * * *", "* * * * *"]', 'crons = ["35 7 * * *"]')
s = s.replace('# 每分钟：驱动行情抓取状态机（Worker 自己判时段，非交易时段秒退）\n', '')
p.write_text(s)
print('cron 已收敛为一条')
PY
python3 -c "import tomllib;d=tomllib.load(open('wrangler.toml','rb'));print('crons:', d['triggers']['crons'])"
```

- [ ] **Step 3: 语法检查 + 本地冒烟**

```bash
node --check worker/index.js
node -e "
import('./worker/index.js').then(m => {
  console.log('fetch:', typeof m.default.fetch, '| scheduled:', typeof m.default.scheduled);
}).catch(e => { console.error('加载失败:', e.message); process.exit(1); });
"
```

Expected: `fetch: function | scheduled: function`

- [ ] **Step 4: 提交并部署，确认线上行为**

```bash
git add -A
git commit -m "feat(worker): 退役行情抓取层（删除 worker/market 与每分钟 cron）"
git push
```

等 `deploy-worker` 变绿后验证：

```bash
curl -s -o /dev/null -w "decide: %{http_code}\n" https://ycu.space/api/decide/health   # 期望 200
curl -s -o /dev/null -w "market: %{http_code}\n" https://ycu.space/api/market/health   # 期望 404
```

---

## Task 5: DROP 四张 D1 表

**前置**：Task 1 的备份 artifact 已确认存在。

**Files:**
- Create: `scripts/check_schema_tables.py`
- Modify: `worker/schema.sql`
- Create: `.github/workflows/d1-drop.yml`（一次性，跑完删除）

- [ ] **Step 1: 先写 schema 校验脚本**

```python
#!/usr/bin/env python3
'''校验 schema 里的建表语句是否正好是期望的那几张。

用法：python3 scripts/check_schema_tables.py --schema worker/schema.sql \
        --expect real_positions,real_cb_positions
'''

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def tables_in(schema_text: str) -> list[str]:
    return re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", schema_text)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="校验 schema 建表清单")
    ap.add_argument("--schema", default="worker/schema.sql")
    ap.add_argument("--expect", required=True, help="逗号分隔的期望表名")
    args = ap.parse_args(argv)

    found = sorted(tables_in(Path(args.schema).read_text(encoding="utf-8")))
    expect = sorted(t.strip() for t in args.expect.split(",") if t.strip())
    print(f"实际: {found}")
    print(f"期望: {expect}")
    if found != expect:
        print("❌ 建表清单不符", file=sys.stderr)
        return 1
    print("✅ schema 符合预期")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

自测：

```bash
python3 scripts/check_schema_tables.py --expect real_positions,real_cb_positions,market_prices,market_symbols,sync_jobs,worker_heartbeat
# 现在应通过（6 张表）
```

- [ ] **Step 2: 再从 schema 里删表定义**

`worker/schema.sql` 只保留 `real_positions` / `real_cb_positions` 两张表：
手工删掉 `market_prices`（含 `idx_mp_date` 索引）、`market_symbols`、`sync_jobs`、
`worker_heartbeat` 四块定义（连同上方注释）。

删除后用脚本**强制校验**（不要用正则批量删，容易误伤）：

```bash
python3 scripts/check_schema_tables.py --schema worker/schema.sql \
  --expect real_positions,real_cb_positions
```

- [ ] **Step 3: 写一次性 DROP workflow**

```yaml
name: d1-drop

# 一次性：删除已退役的行情/任务表。执行前请确认 d1-backup 的 artifact 存在。
on:
  push:
    branches: [main]
    paths:
      - ".github/workflows/d1-drop.yml"
  workflow_dispatch:

permissions:
  contents: read

jobs:
  drop:
    runs-on: ubuntu-latest
    env:
      CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}
      CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
    steps:
      - uses: actions/checkout@v4
      - name: 删除表
        run: |
          for t in market_prices market_symbols sync_jobs worker_heartbeat; do
            echo "DROP TABLE IF EXISTS $t;"
          done > /tmp/drop.sql
          cat /tmp/drop.sql
          npx --yes wrangler@latest d1 execute a-finder --file=/tmp/drop.sql --remote --yes
      - name: 核对
        run: |
          npx --yes wrangler@latest d1 execute a-finder --remote --yes \
            --command "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
```

- [ ] **Step 4: 提交触发**

```bash
git add worker/schema.sql .github/workflows/d1-drop.yml
git commit -m "chore(d1): 删除已退役的四张行情/任务表（schema + 一次性 DROP）"
git push
```

等 `d1-drop` 跑完，核对输出只剩 `real_positions` / `real_cb_positions`（及 cf 系统表）。

- [ ] **Step 5: 决策台冒烟**

```bash
curl -s https://ycu.space/api/decide/health | python3 -m json.tool   # d1: ok
curl -s https://ycu.space/api/decide/positions | head -c 200          # 能读到持仓
```

- [ ] **Step 6: 删除一次性 workflow**

```bash
git rm .github/workflows/d1-drop.yml
git commit -m "chore(ci): 移除一次性 d1-drop workflow（已执行）"
git push
```

---

## Task 6: 删除残留脚本/测试/workflow，更新文档

**Files:**
- Delete: `scripts/pull_from_d1.py`、`scripts/seed_d1.py`、`.github/workflows/d1-seed.yml`、`.github/workflows/d1-backfill.yml`、`.github/workflows/d1-backup.yml`（备份已归档）
- Delete: `tests/test_worker_market_sources.mjs`、`tests/test_worker_market_jobs.mjs`、`tests/test_worker_market_repo.mjs`、`tests/test_worker_market_api.mjs`、`tests/test_worker_market_parity.py`、`tests/test_pull_from_d1.py`、`tests/test_seed_d1.py`
- Modify: `CLAUDE.md`

- [ ] **Step 1: 删除文件**

```bash
git rm scripts/pull_from_d1.py scripts/seed_d1.py
git rm .github/workflows/d1-seed.yml .github/workflows/d1-backfill.yml .github/workflows/d1-backup.yml
git rm tests/test_worker_market_sources.mjs tests/test_worker_market_jobs.mjs \
       tests/test_worker_market_repo.mjs tests/test_worker_market_api.mjs \
       tests/test_worker_market_parity.py tests/test_pull_from_d1.py tests/test_seed_d1.py
ls worker/ tests/ | head -30          # 确认没有残留 market 相关文件
```

- [ ] **Step 2: 全量测试**

```bash
node --test tests/test_worker_auth.mjs        # Worker 侧仅剩的测试，期望 pass 9
python3 -m pytest tests/ -q                   # 期望 500 左右通过、0 失败
```

如果有测试因为删除而报 `ImportError`，说明还有别处引用，逐个排查（`grep -rn "pull_from_d1\|seed_d1\|worker/market" --include=*.py --include=*.js --include=*.mjs .`）。

- [ ] **Step 3: 更新 CLAUDE.md**

把「# Worker 抓取行情（D1 为抓取落点）」整节替换为：

```markdown
# 行情数据路径（GH 直连，无 Cloudflare 存储）
- 行情唯一存储是 GitHub Actions 的 SQLite 缓存（`hs300.db`，actions/cache，10 GB 免费）；
  gh-pages 只发布站点消费的 JSON（picks/plan/portfolio/decide），不发布原始行情。
- Worker 只做两件事：`/api/decide/*`（决策台持仓，D1 仅 `real_*` 两张表）与 07:35 兜底
  dispatch cron。**不要再把行情放进 D1**——免费版行写入仅 10 万/天，一次历史回填就会打爆。
- 历史已删除的方案：Worker 抓取 + D1 存储（2026-10-26）、R2 + JSON（2026-10-27，R2 未开通）。
  设计文档在 `docs/superpowers/specs/`，需要时可查。
```

同时在「Conventions」里加一条：

```markdown
- 新建 Cloudflare 资源（R2/KV/Queues/新 D1 表）前，先确认免费额度足够、且 token 具备对应权限
  （2026-10-26 因 `routes` 权限导致 deploy 连续失败数小时，教训见 git 历史）。
```

- [ ] **Step 4: 提交**

```bash
git add -A
git commit -m "chore: 清理 Worker 行情层残留（脚本/workflow/测试）+ 更新 CLAUDE.md"
git push
```

- [ ] **Step 5: 终态验证**

```bash
# 1) Worker 只剩 decide
for p in /api/decide/health /api/market/health; do
  echo -n "$p → "; curl -s -o /dev/null -w "%{http_code}\n" "https://ycu.space$p"
done
# 期望：200 / 404

# 2) 仓库里不再有行情抓取的 Worker 代码
ls worker/            # 期望：auth.js index.js schema.sql
grep -rn "market_prices" --include=*.sql --include=*.js --include=*.py . | grep -v docs/ | head
# 期望：无输出
```

---

## 回滚

| 阶段 | 回滚动作 |
|---|---|
| Task 2-3 后想恢复 Worker 抓取 | `git revert` Task 2 的提交（deploy-site 回到拉 D1 版本），但需先恢复 Worker market 代码 |
| Task 4 后 | `git revert` Task 4 的提交并 `git push`（代码在 git 历史里，含 `worker/market/`） |
| Task 5 后（表已删） | 用 `d1-backup` artifact：`wrangler d1 execute a-finder --file=<备份>.sql --remote` |
| 任何时点 | 数据链始终由 Task 2 之后的直连路径保障，Worker 的删除不影响站点更新 |
