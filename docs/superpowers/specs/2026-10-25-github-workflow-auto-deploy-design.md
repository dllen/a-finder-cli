# GitHub Workflow：A 股交易日收盘后自动刷新并部署 — 设计文档

**日期**：2026-10-25
**状态**：设计中（已获用户逐节确认）

---

## 一、目标

新建一个 GitHub Actions workflow，在 **A 股交易日收盘后（15:30 北京时间）** 自动完成：
行情同步 → 选股 → 交易计划 → 资金档位组合 → 可转债榜单 → 静态站导出 → 部署 gh-pages。

**取代**现有 `fetch-data.yml` 的"只同步不部署"模式（该文件注释明确写着因 geo-block 不部署，权威部署靠本地 cron），让 CI **完全接管部署**。

**非目标**：
- ❌ 不改 `weekly-evolve.yml`（周五进化闭环照旧）
- ❌ 不动本地 `a-finder.sh` 脚本（仅 CI 参考其步骤顺序）
- ❌ 不引入新的运行时依赖到 `pyproject.toml`（`chinese_calendar` 仅在 workflow 内 pip install）

---

## 二、现状与问题

| 现状 | 问题 |
|---|---|
| `fetch-data.yml` 工作日 15:30 跑，best-effort 同步缓存，**不部署** | 线上更新仍依赖本地 cron，本地没跑就断更 |
| 缓存 key 用 `hs300-db-${{ github.run_id }}` | 每次唯一，缓存无限增长 |
| `weekly-evolve.yml` 同样用 `run_id` key | 同上 |
| 部署靠本地 `push_site_to_ghpages`（orphan commit force-push） | CI 需等效机制 |
| K 线端点 GitHub runner 上 geo-block 风险 | 需多源降级 + 失败容忍 |

---

## 三、方案

新增 `.github/workflows/deploy-site.yml`，单 workflow 全流程（方案 A，已确认）。

### 3.1 触发

```yaml
on:
  schedule:
    # A股收盘 15:00 北京，数据落地约 15:30 → 07:30 UTC
    - cron: "30 7 * * *"
  workflow_dispatch:
```

- cron 每天跑（GitHub cron 无法表达中国节假日），交易日判定在 workflow 内第一步做
- `workflow_dispatch` 手动兜底

### 3.2 权限与并发

```yaml
concurrency:
  group: deploy-site
  cancel-in-progress: false

permissions:
  contents: write   # push gh-pages
```

### 3.3 步骤流程

| # | 步骤 | 关键点 |
|---|---|---|
| 1 | checkout + setup-python 3.11 | pip cache |
| 2 | 判定 A 股交易日 | `chinese_calendar.is_workday()`，非交易日 exit 0 |
| 3 | 恢复 hs300.db 缓存 | `key: hs300-db`，`restore-keys: hs300-db-` |
| 4 | 增量同步行情 | `sync-incremental`，多源降级，失败容忍 |
| 5 | 选股 + 计划 + 组合 + 转债 | 转债 `|| true` 容忍 |
| 6 | 导出静态站 | `export_json.py` |
| 7 | 保存 hs300.db 缓存 | 固定 key |
| 8 | 部署 gh-pages | peaceiris，force_orphan |

---

## 四、详细设计

### 4.1 交易日判定（chinese_calendar）

```yaml
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
```

- `is_workday()` 语义 = 周一~周五 − 法定节假日 + 调休补班 = A 股交易日
- 后续步骤加 `if: steps.trade-day.outputs.is_trade_day == 'true'`
- 判定失败默认当作交易日继续跑（宁白跑不漏跑）

### 4.2 缓存（固定 key）

```yaml
- name: Restore hs300.db
  uses: actions/cache@v4
  with:
    path: hs300.db
    key: hs300-db
    restore-keys: hs300-db-

# ... 同步/选股/计划 ...

- name: Save hs300.db
  uses: actions/cache@v4
  with:
    path: hs300.db
    key: hs300-db
```

固定 `key: hs300-db`，每次覆盖更新；`restore-keys: hs300-db-` 兼容历史 `run_id` 键一次迁移。

### 4.3 同步（geo-block 应对）

```yaml
- name: 增量同步行情
  run: |
    set +e
    bash a-finder.sh sync-incremental hs300.db 20 pick-history 2>&1 | tail -30
    set -e
  continue-on-error: true
```

- 走 `fetch_daily_kline_smart` + SourceHealthTracker 多源降级（东财被封→新浪/腾讯）
- `continue-on-error` + 缓存兜底：三源全败也继续用旧缓存导出，线上不断更
- 收盘 15:30 跑，当日数据通常已落地；未落地则自然补到上一交易日（`latest_trade_date`）

### 4.4 选股/计划/组合/转债

```yaml
- name: 选股 + 计划 + 组合
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
```

步骤顺序与本地 `a-finder.sh site` 完全一致。

### 4.5 导出 + 部署

```yaml
- name: 导出静态站
  run: python export_json.py --db hs300.db --out site

- name: 部署 gh-pages
  uses: peaceiris/actions-gh-pages@v4
  with:
    github_token: ${{ secrets.GITHUB_TOKEN }}
    publish_dir: ./site
    force_orphan: true
    commit_message: "site: 自动更新 $(date +%F)"
```

- `force_orphan: true` 保持 gh-pages 单 commit（与现有本地 push 一致）
- `secrets.GITHUB_TOKEN` + `permissions: contents: write`，无需额外 secret

---

## 五、文件变更

| 文件 | 变更 |
|---|---|
| `.github/workflows/deploy-site.yml` | **新建**（~90 行） |
| `.github/workflows/fetch-data.yml` | 可保留或删除（不再承担部署，建议删除避免重复跑） |

**零改动**：`app.py`、`export_json.py`、`a-finder.sh`、`pyproject.toml`、源码。

> 关于 `fetch-data.yml`：设计上建议删除（其职责被 deploy-site.yml 完全覆盖），但保留也无害（仅浪费一次同步）。此点留给实施时与用户确认。

---

## 六、风险控制

| 风险 | 缓解 |
|---|---|
| GitHub runner geo-block 三源全灭 | `continue-on-error` + 缓存兜底，用旧数据继续部署，不断更 |
| 转债扫描慢/限流 | `|| true` 降级，沿用上次转债数据 |
| 交易日判定失败 | 默认当交易日跑，宁白跑不漏跑 |
| 缓存 key 无限增长 | 固定 `key: hs300-db` |
| 首次跑无缓存 | `restore-keys` 无命中则从空库全量同步（慢但可完成） |
| 本地 cron 与 CI 双跑冲突 | 用户已确认 CI 完全接管，本地 cron 应停用 |

---

## 七、验证计划

1. **手动触发**（`workflow_dispatch`）：验证全流程在 GitHub runner 上跑通，重点看：
   - 交易日判定正确
   - 数据源可达性（新浪/腾讯是否被 geo-block）
   - 转债是否触发限流
   - gh-pages 部署成功
2. **日志检查**：`数据源优先级: ...` 探针输出，确认多源降级生效
3. **线上确认**：ycu.space 数据日期更新到最新交易日
4. **次日定时触发**：确认 cron 到点自动跑

---

## 八、评审 / 实施

- 决策已确认：完全接管部署 + 交易日历判定 + GitHub-hosted 多源降级
- 设计分 4 节逐节获用户确认
- 接着调 writing-plans 拆实施计划（本功能为单文件 workflow，计划会很短）
