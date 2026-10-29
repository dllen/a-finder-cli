## [ERR-20260318-001] sync-hs300-meta

**Logged**: 2026-03-18T23:22:46Z
**Priority**: medium
**Status**: pending
**Area**: backend

### Summary
Metadata sync hit intermittent remote connection reset for single stock code

### Error
```
2026-03-18 23:22:31,983 WARNING 沪深300成分股获取失败，改用本地数据库代码列表
2026-03-18 23:22:31,984 INFO 元数据同步开始: symbols=300
2026-03-18 23:22:46,665 WARNING 元数据同步失败: code=000001 error=Remote end closed connection without response
```

### Context
- Command/operation attempted: sync-hs300-meta via incremental update workflow
- Input or parameters used: default metadata concurrency/rate/retries
- Environment details: macOS, Eastmoney remote API

### Suggested Fix
Lower metadata concurrency/rate and increase retries/backoff to reduce transient connection drops.

### Metadata
- Reproducible: unknown
- Related Files: /Users/shichaopeng/Work/code/github/a-finder-cli/sync_service.py, /Users/shichaopeng/Work/code/github/a-finder-cli/sync_incremental_pick.sh
- See Also: ERR-20260312-003

---

## [ERR-20260312-007] test_ma_backtest.sh

**Logged**: 2026-03-12T00:00:00Z
**Priority**: medium
**Status**: pending
**Area**: tests

### Summary
Empty array expansion still triggered nounset in current bash runtime

### Error
```
bash test_ma_backtest.sh
test_ma_backtest.sh: line 15: EXTRA[@]: unbound variable
```

### Context
- Command/operation attempted: bash test_ma_backtest.sh
- Input or parameters used: EXTRA initialized as empty array and expanded via "${EXTRA[@]}"
- Environment details: macOS, bash with set -u

### Suggested Fix
Avoid expanding optional arrays under nounset by branching command execution on $#.

### Metadata
- Reproducible: yes
- Related Files: /Users/shichaopeng/Work/code/github/a-finder-cli/test_ma_backtest.sh
- See Also: ERR-20260312-005, ERR-20260312-006

---

## [ERR-20260312-006] test_ma_backtest.sh

**Logged**: 2026-03-12T00:00:00Z
**Priority**: medium
**Status**: pending
**Area**: tests

### Summary
Default-safe array expansion passed an empty string argument and broke argparse parsing

### Error
```
bash test_ma_backtest.sh
usage: ma_backtest.py [-h] [--db DB] [--top TOP] [--days DAYS] [--tune]
                      [--walk-forward]
ma_backtest.py: error: unrecognized arguments:
```

### Context
- Command/operation attempted: bash test_ma_backtest.sh
- Input or parameters used: "${EXTRA[@]:-}" with empty array
- Environment details: macOS, bash with set -euo pipefail

### Suggested Fix
Keep EXTRA initialized to an empty array and use plain "${EXTRA[@]}" so empty args are omitted.

### Metadata
- Reproducible: yes
- Related Files: /Users/shichaopeng/Work/code/github/a-finder-cli/test_ma_backtest.sh
- See Also: ERR-20260312-005

---

## [ERR-20260312-005] test_ma_backtest.sh

**Logged**: 2026-03-12T00:00:00Z
**Priority**: medium
**Status**: pending
**Area**: tests

### Summary
test_ma_backtest.sh fails with nounset when no extra CLI arguments are passed

### Error
```
bash test_ma_backtest.sh
test_ma_backtest.sh: line 11: EXTRA[@]: unbound variable
```

### Context
- Command/operation attempted: bash test_ma_backtest.sh
- Input or parameters used: default args only, no extra flags
- Environment details: macOS, bash with set -euo pipefail

### Suggested Fix
Initialize EXTRA defensively and expand with default-safe array syntax under nounset.

### Metadata
- Reproducible: yes
- Related Files: /Users/shichaopeng/Work/code/github/a-finder-cli/test_ma_backtest.sh

---

## [ERR-20260312-001] apply_patch

**Logged**: 2026-03-12T00:00:00Z
**Priority**: medium
**Status**: pending
**Area**: backend

### Summary
apply_patch failed to find expected lines in stock_strategies.py

### Error
```
/Users/shichaopeng/Work/code/github/a-finder-cli/stock_strategies.py: Failed to find expected lines in /Users/shichaopeng/Work/code/github/a-finder-cli/stock_strategies.py:
from typing import Dict, List

from candidate_rules import ma_strategy_candidates
from decision_rules import primary_signal
from signal_rules import detect_signals
from scoring import score_stocks


def primary_signal(signals: List[Dict[str, str]]) -> Tuple[str, str]:
Please read /Users/shichaopeng/Work/code/github/a-finder-cli/stock_strategies.py and try again.
```

### Context
- Command/operation attempted: apply_patch to update stock_strategies.py
- Input or parameters used: attempted to replace primary_signal signature after it was moved out
- Environment details: macOS, Trae IDE

### Suggested Fix
Re-read the target file before applying changes; avoid patching removed sections.

### Metadata
- Reproducible: yes
- Related Files: /Users/shichaopeng/Work/code/github/a-finder-cli/stock_strategies.py

---
## [ERR-20260312-004] ruff

**Logged**: 2026-03-12T00:00:00Z
**Priority**: medium
**Status**: pending
**Area**: config

### Summary
Attempted lint command failed because ruff is not installed in runtime

### Error
```
uv run ruff check .
error: Failed to spawn: `ruff`
Caused by: No such file or directory (os error 2)
```

### Context
- Command/operation attempted: uv run ruff check .
- Input or parameters used: repository root
- Environment details: macOS, uv

### Suggested Fix
Install ruff in project dependencies or document an available lint command.

### Metadata
- Reproducible: yes
- Related Files: /Users/shichaopeng/Work/code/github/a-finder-cli/pyproject.toml

---
## [ERR-20260312-003] sync-hs300-meta

**Logged**: 2026-03-12T00:00:00Z
**Priority**: high
**Status**: pending
**Area**: backend

### Summary
sync-hs300-meta fails to fetch HS300 constituents

### Error
```
uv run a-finder sync-hs300-meta --db hs300.db
errors.FetchError: 无法获取沪深300成分股列表
```

### Context
- Command/operation attempted: uv run a-finder sync-hs300-meta --db hs300.db
- Input or parameters used: default rate/concurrency/retries
- Environment details: macOS, uv, Eastmoney data source

### Suggested Fix
Provide a fallback source for HS300 constituents or allow importing a local code list when remote fetch fails.

### Metadata
- Reproducible: unknown
- Related Files: /Users/shichaopeng/Work/code/github/a-finder-cli/data_providers.py, /Users/shichaopeng/Work/code/github/a-finder-cli/sync_service.py

---
## [ERR-20260312-002] uv-run

**Logged**: 2026-03-12T00:00:00Z
**Priority**: high
**Status**: pending
**Area**: config

### Summary
uv run a-finder fails because console script not installed

### Error
```
uv run a-finder overview
error: Failed to spawn: `a-finder`
Caused by: No such file or directory (os error 2)
```

### Context
- Command/operation attempted: uv run a-finder overview
- Input or parameters used: project without build-system/py-modules
- Environment details: macOS, uv

### Suggested Fix
Add build-system and py-modules in pyproject.toml, then run uv sync --reinstall.

### Metadata
- Reproducible: yes
- Related Files: /Users/shichaopeng/Work/code/github/a-finder-cli/pyproject.toml

---
## [2026-10-25] 选股榜单全 0 条：K 线历史不足 221 个交易日

**Area**: data / strategy

### Summary
`run_picks` 对所有日期返回 0 条（ma/buy/signal/multi 全 0），站点数据停在旧日期

### Error
```
run_picks(db, top=20, do_sync=False, trade_date='2026-10-23')
{'date': '2026-10-23', 'ma': 0, 'buy': 0, 'signal': 0, 'multi': 0}
```

### Context
- Command/operation attempted: 同步 2 周行情后回填 daily_picks
- Input or parameters used: sync-hs300-range 2026-04-25~2026-10-24（仅 120 交易日）
- Environment details: SQLite DB, pick_history.run_picks → build_market_from_db(min_days=221, max_days=520)

### Suggested Fix
选股策略要求 221~520 个交易日的 K 线历史。同步时至少覆盖 2 年
（约 2024-10 起，~500 交易日 / 148k 行），否则 4 个榜单全部为空。
另注意 `build_plan(plan_date, db_path)` 参数顺序传反会按日期名创建
散落 SQLite 文件。

### Metadata
- Reproducible: yes
- Related Files: pick_history.py, plan_builder.py

---

## [2026-10-25] 东财被封导致探测误判为"可用"

**Area**: data / sync

### Summary
SourceHealthTracker.probe() 报告 eastmoney 可用，实际它已被限流

### Error
```
Priority: eastmoney > sina > tencent
  eastmoney: available=True   # 实际 RemoteDisconnected
```

### Context
- Command/operation attempted: SourceHealthTracker.probe()
- Input or parameters used: 600519 探针
- Environment details: 东财 push2his 被限流，新浪/腾讯正常

### Suggested Fix
探测与 smart 路由必须走各源**直连**接口，不能用
`fetch_daily_kline_with_name`（它失败会静默回退到新浪/腾讯，
导致无法区分"东财可用"和"东财挂了但被备源救回"）。
已新增 `fetch_daily_kline_eastmoney`（无 fallback，失败抛异常）。

### Metadata
- Reproducible: yes
- Related Files: data_providers.py, sync_service.py

---

## [2026-10-29] CI 缓存固定 key 冻结 → hs300.db 谱系断在 10-13

**Area**: ci / infra

### Summary
deploy-site.yml 建档时用了固定缓存 key `hs300-db`（2026-10-25 14:40），
10-26 首次运行把该 key 写死成"每天从同一份旧库增量"；10-26 17:49 改成
`hs300-db-<run_id>` + `restore-keys: hs300-db-` 后，旧条目因
`'hs300-db'.startswith('hs300-db-')` 为 False 而**永久不可达**，
CI 行情谱系就此断在 2026-10-13。

### Error
```
🚨 2026-10-29 巡检发现无法自动修复的缺口
剩余：L1 2026-10-14~2026-10-16（3 天）、2026-10-19~2026-10-23（5 天）、2026-10-26~2026-10-29（4 天）
```

### Context
- 判定方法：把 daily_prices 截止日设为 2026-10-13 的库喂给 audit_coverage，
  缺失日渲染成**完全相同**的三个区间 → 谱系断点确认在 10-13
- 陷阱：**站点是本地 `a-finder.sh site --push` 发的**（gh-pages 唯一提交
  "update site data 2026-10-27-20:26" 来自 a-finder.sh:157 的 commit-tree），
  与 CI 缓存库是两个谱系。export_json.py 只从 daily_picks/trade_plan 出 JSON，
  不看 daily_prices → 站点健康**不能**证明 CI 库健康
- 通知误导：原因行被"超出 7 天自动修复窗"去重后覆盖，而 10-22~10-29 这 6 天
  实际在窗口内（已尝试但失败）；"建议 fallback" 指向的是本地库（当时是好的）

### Suggested Fix
- 缓存 key 永远带 run_id；改 key 之后要确认旧条目是否还能被 restore-keys 命中
- 恢复后加**新鲜度校验**：MAX(daily_prices.trade_date) 落后太多 → 判定谱系孤儿，
  走冷启动重建，别指望增量补
- 通知按日期分别给出原因，别用去重后的单句覆盖全部缺口

### Metadata
- Reproducible: yes（用截止日 10-13 的合成库可复现告警的日期分组）
- Related Files: .github/workflows/deploy-site.yml, .github/workflows/daily-audit.yml,
  scripts/audit_coverage.py, scripts/repair_gaps.py, scripts/notify_audit.py

---

## [2026-10-29] 两谱系分叉：CI 的同步判据读错了对象（已修）

**Area**: ci / infra

### Summary
上一条事故的**根因不是缓存 key，而是「CI 的 freshness 判据读的是线上站点」**。
站点可以被另一个写者（本地 `a-finder.sh site --push`）发布，而 `dates.json` 只反映
`daily_picks`/`trade_plan`、**不看 `daily_prices`**。于是：

  本地先发布当天 → 线上已有该日期 → CI 判 skip → 跳过同步**且**跳过缓存保存
  → CI 行情谱系永远不推进（断在 2026-10-13），两个库持续分叉。

缓存 key 冻结只是让这条谱系**停住**，判据错位让它**不可能自己恢复**。

### Fix（现在的不变量，别退回）
1. 同步判据 = **CI 自己的库**：新增 `scripts/db_freshness.py`，落后按**交易日**算。
   退出码 0 新鲜 / 1 可增量补 / 2 无库 / 3 **陈旧谱系**（落后 >3 交易日 → 冷启动 400 天）。
2. **Restore 提到 freshness 之前** —— 判据要读自己的库，得先恢复。
3. **`skip_sync` 与「要不要发布」分开**：另有 `site_stale`，线上缺当天就仍要发布。
   混成一个 skip 会让「库新鲜但站点缺当天」变成没人发布。
4. **没同步就不重复存缓存**：8 班/天 × 200MB × 7 天 ≈ 11GB，会打爆 10GB 配额。
5. **本地兜底加 deadline**（默认 17:00 北京）：过了 deadline 线上仍落后才动手。
   `needs_fallback.py --deadline` + `a-finder.sh fallback --deadline/--force`。
   本地抢跑本身就在制造分叉。

### 踩坑记录
- macOS bash 3.2：`echo "... $deadline），..."` 会把中文全角括号吞进变量名 →
  `unbound variable`。**必须写 `${deadline}`**（与已有条目同一类坑）。
- `[ "$x" = "2" ] && echo ...` 放在 case 分支末尾时，条件不成立会让该表达式返回 1，
  成为整个 step 的退出码 → **步骤失败**。要写成 `if ...; then ...; fi`。
- 测试里做 workflow 文本断言时，`restore-keys` / `degraded-` 字样常出现在**注释**里，
  子串匹配会误判。要过滤注释行，或只匹配 `key:` 行。

### Metadata
- Reproducible: yes
- Related Files: .github/workflows/deploy-site.yml, scripts/db_freshness.py,
  scripts/needs_fallback.py, a-finder.sh
- Tests: tests/test_db_freshness.py, tests/test_cache_lineage_workflow_contract.py,
  tests/test_needs_fallback.py

---
