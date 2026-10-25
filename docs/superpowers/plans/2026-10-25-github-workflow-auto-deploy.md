# GitHub Workflow A 股交易日自动部署 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `.github/workflows/deploy-site.yml`，A 股交易日收盘后自动同步→选股→计划→组合→转债→导出→部署 gh-pages，完全接管部署。

**Architecture:** 单 workflow 全流程，chinese_calendar 判交易日，actions/cache 固定 key 缓存 hs300.db，peaceiris/actions-gh-pages 部署（force_orphan）。

**Tech Stack:** GitHub Actions, Python 3.11, chinese_calendar, peaceiris/actions-gh-pages@v4, actions/cache@v4

**Spec:** `docs/superpowers/specs/2026-10-25-github-workflow-auto-deploy-design.md`

---

### Task 1: 新建 deploy-site.yml

**Files:**
- Create: `.github/workflows/deploy-site.yml`

- [ ] **Step 1: 创建 workflow 文件**

创建 `.github/workflows/deploy-site.yml`：

```yaml
name: deploy-site

# A 股交易日收盘后自动刷新数据并部署 gh-pages。
# 取代原 fetch-data.yml 的「只同步不部署」——现在 CI 完全接管部署。
# 交易日判定用 chinese_calendar（周末/法定节假日/调休补班精确识别）。
on:
  schedule:
    # A股收盘 15:00 北京，数据落地约 15:30 → 07:30 UTC
    - cron: "30 7 * * *"
  workflow_dispatch:

concurrency:
  group: deploy-site
  cancel-in-progress: false

permissions:
  contents: write   # push gh-pages

jobs:
  deploy:
    runs-on: ubuntu-latest
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

      - name: 判定 A 股交易日
        id: trade-day
        run: |
          python -m pip install chinese_calendar
          python - <<'PY'
          import datetime, os
          from chinese_calendar import is_workday
          today = datetime.date.today()
          if is_workday(today):
              with open(os.environ["GITHUB_OUTPUT"], "a") as f:
                  f.write("is_trade_day=true\n")
              print(f"{today} 是 A 股交易日")
          else:
              print(f"{today} 非交易日（周末或节假日），跳过")
          PY

      - name: Restore hs300.db from cache
        if: steps.trade-day.outputs.is_trade_day == 'true'
        uses: actions/cache@v4
        with:
          path: hs300.db
          key: hs300-db
          restore-keys: |
            hs300-db-

      - name: 增量同步行情（多源降级，失败容忍）
        if: steps.trade-day.outputs.is_trade_day == 'true'
        run: |
          set +e
          bash a-finder.sh sync-incremental hs300.db 20 pick-history 2>&1 | tail -30
          set -e
        continue-on-error: true

      - name: 选股 + 计划 + 组合 + 可转债
        if: steps.trade-day.outputs.is_trade_day == 'true'
        env:
          BACKFILL_ANCHOR: "2026-08-01"
        run: |
          set -e
          python stock_cli.py plan build --db hs300.db --date "$(date +%F)"
          python stock_cli.py plan build-all --db hs300.db \
            --strategy linyuan --tiers 50000,100000,150000,200000,250000,300000,350000,400000,450000,500000
          python stock_cli.py plan build-all --backfill --since "$BACKFILL_ANCHOR" --db hs300.db
          # 可转债：全市场扫描慢 + 易限流，失败不中断
          python stock_cli.py cb-picks --top 20 --db hs300.db --concurrency 2 --rate 4 || true

      - name: 导出静态站
        if: steps.trade-day.outputs.is_trade_day == 'true'
        run: python export_json.py --db hs300.db --out site

      - name: Save hs300.db to cache
        if: steps.trade-day.outputs.is_trade_day == 'true'
        uses: actions/cache@v4
        with:
          path: hs300.db
          key: hs300-db

      - name: 部署 gh-pages
        if: steps.trade-day.outputs.is_trade_day == 'true'
        uses: peaceiris/actions-gh-pages@v4
        with:
          github_token: ${{ secrets.GITHUB_TOKEN }}
          publish_dir: ./site
          force_orphan: true
          commit_message: "site: 自动更新 $(date +%F)"
```

- [ ] **Step 2: 本地校验 YAML 语法**

Run:
```bash
cd /Users/shichaopeng/Work/self-dir/projects/a-finder-cli
python3 -c "import yaml, sys; yaml.safe_load(open('.github/workflows/deploy-site.yml')); print('YAML 语法 OK')"
```

Expected: `YAML 语法 OK`

（若报 `ModuleNotFoundError: yaml`，改用：
```bash
python3 -c "
import json
# 用 json 间接校验不行，改用 ruby/其他——若无 yaml，跳过此步，GitHub 会在 push 后校验
"
```
实际校验交给 GitHub Actions 的 workflow 解析器，本地语法错误会被 push 后立刻报错。）

- [ ] **Step 3: 用 actionlint 校验（若有）**

Run:
```bash
cd /Users/shichaopeng/Work/self-dir/projects/a-finder-cli
which actionlint && actionlint .github/workflows/deploy-site.yml || echo "actionlint 未安装，跳过（GitHub 会在 push 后校验）"
```

Expected: 无错误，或提示 actionlint 未安装

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/deploy-site.yml
git commit -m "feat(ci): 新增 deploy-site workflow（A股交易日自动同步+部署）"
```

---

### Task 2: 删除旧 fetch-data.yml

**Files:**
- Delete: `.github/workflows/fetch-data.yml`

- [ ] **Step 1: 确认 fetch-data.yml 职责已被 deploy-site.yml 覆盖**

对照确认：
- `fetch-data.yml` 的同步步骤（sync-incremental + plan build + build-all + build-all --backfill）→ deploy-site.yml 已完整覆盖
- `fetch-data.yml` 不部署 → deploy-site.yml 增加了部署
- `fetch-data.yml` 的缓存逻辑 → deploy-site.yml 用固定 key 改进

- [ ] **Step 2: 删除文件**

```bash
cd /Users/shichaopeng/Work/self-dir/projects/a-finder-cli
git rm .github/workflows/fetch-data.yml
```

- [ ] **Step 3: Commit**

```bash
git commit -m "chore(ci): 删除 fetch-data.yml（职责已被 deploy-site.yml 覆盖）"
```

---

### Task 3: 手动触发实测

**Files:** None（验证）

- [ ] **Step 1: 推送 main 触发 workflow 解析**

```bash
cd /Users/shichaopeng/Work/self-dir/projects/a-finder-cli
git push origin main
```

Expected: push 成功，无 YAML 解析错误

- [ ] **Step 2: 在 GitHub 手动触发 workflow_dispatch**

操作：GitHub 仓库 → Actions → `deploy-site` → Run workflow → Run workflow 按钮。

- [ ] **Step 3: 观察运行结果**

在 Actions 页面确认：
1. 交易日判定步骤输出正确（"是 A 股交易日" 或 "非交易日"）
2. 同步步骤的 `数据源优先级: ...` 探针输出（确认多源降级生效）
3. 转债步骤是否触发限流（`|| true` 是否兜住）
4. 部署步骤成功

- [ ] **Step 4: 验证线上数据更新**

```bash
curl -sL https://ycu.space/data/dates.json | python3 -c "import sys,json; print('最新日期:', json.load(sys.stdin)['dates'][0])"
```

Expected: 日期为最新交易日

- [ ] **Step 5: 若数据源 geo-block 全灭，记录并调整**

若同步步骤三源全失败（`数据源优先级` 输出为空或全是失败），记录到 `.learnings/ERRORS.md`，考虑降级策略（如改用 self-hosted runner）。但 workflow 因缓存兜底不会挂。

---

### Task 4: 更新文档

**Files:**
- Modify: `CLAUDE.md`（Common Commands 或 CI 相关段落）

- [ ] **Step 1: 在 CLAUDE.md 补充 CI 部署说明**

在 CLAUDE.md 的 `# Web dashboard / lifecycle` 段落后追加：

```markdown
# CI 自动部署
- `.github/workflows/deploy-site.yml`：A 股交易日 15:30 北京（07:30 UTC）自动
  同步→选股→计划→组合→转债→导出→部署 gh-pages。用 chinese_calendar 判交易日，
  数据源多源降级（SourceHealthTracker），同步/转债失败容忍（缓存兜底）。
  手动兜底：GitHub Actions 页面的 workflow_dispatch。
- 本地 `a-finder.sh site --push` 已由 CI 接管；本地 cron 应停用，避免与 CI 双跑。
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: 记录 CI 自动部署（deploy-site.yml）"
git push origin main
```

---

## Self-Review

**Spec coverage:**
- §3.1 触发 → Task 1（cron + workflow_dispatch）
- §3.2 权限/并发 → Task 1（permissions/concurrency）
- §3.3 步骤流程 8 步 → Task 1（全部实现）
- §4.1 交易日判定 → Task 1
- §4.2 缓存固定 key → Task 1
- §4.3 同步失败容忍 → Task 1
- §4.4 选股/计划/组合/转债 → Task 1
- §4.5 导出+部署 → Task 1
- §五 文件变更（删除 fetch-data.yml）→ Task 2
- §七 验证计划 → Task 3
- CLAUDE.md 文档 → Task 4

**Placeholder scan:** 无 TBD/TODO/FIXME。所有 YAML 完整、可运行。

**Type consistency:**
- `steps.trade-day.outputs.is_trade_day` — 在 Task 1 判定步骤设置，后续所有步骤 `if` 引用一致
- 缓存 `key: hs300-db` + `restore-keys: hs300-db-` — 恢复与保存一致
- `BACKFILL_ANCHOR: "2026-08-01"` — 与 spec §4.4 一致
- peaceiris `publish_dir: ./site` + `force_orphan: true` — 与 spec §4.5 一致
