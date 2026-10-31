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

## [ERR-20261030-001] daily-audit L1 缺口永远修不上（2026-10-30 巡检）

**Logged**: 2026-10-30T21:00:00+08:00
**Priority**: high
**Status**: fixed
**Area**: ci

### Summary
巡检报「无法自动修复的缺口」：L1 2026-10-14~2026-10-16、2026-10-19~2026-10-23、
2026-10-26~2026-10-30。根因是**两个**独立缺陷叠加，不是单一 bug。

### Error
```
🚨 2026-10-30 巡检发现无法自动修复的缺口
剩余：L1 2026-10-14~2026-10-16（3 天）、2026-10-19~2026-10-23（5 天）、2026-10-26~2026-10-30（5 天）
原因：行情缺口超出 7 天自动修复窗（复权口径风险），只报警
建议：手动跑 bash a-finder.sh fallback 或检查数据源
```

### Root causes（两个，缺一不可）

**缺陷 1：L1 修复窗口只有一个，且用日历日。**
`repair_gaps.MAX_PRICE_DAYS = 7` 对**所有** L1 缺口一视同仁。
CI 缓存谱系从 2026-10-14 起断了 13 天 → 这 13 天全部超 7 天窗口 →
每晚重复报警、永远不修、run 永远红。窗口还是**日历日**：国庆 8 个日历日
只有 5 个交易日，长假后必然错判（该修的判成超窗，不该修的判成在窗内）。

**缺陷 2：在窗内的那 8 天（10-23~10-30）尝试过，但补抓失败了。**
10-29 的数据**只有东财有**（腾讯 qfqday / 新浪都只到 10-28，实测）。
东财不可达时备源链落到腾讯 **qfq 前复权**，而东财/新浪是 **fqt=0 不复权** ——
`daily_prices` 没有复权口径列，备源补进来的行会**静默混口径**。

⇒ 只放宽窗口（缺陷 1）**修不好** 10-23~10-30：补抓照样会失败。

**缺陷 3（次要，但让人查错地方）：通知的建议指向了错误的产物。**
`bash a-finder.sh fallback` 修的是**本地库**，而坏掉的是 **CI 缓存库**
（Actions Cache 里的 hs300.db）。本地库当时是健康的（300/300 只到 10-29），
照着建议跑本地兜底毫无作用 —— 真正坏的那个产物一直坏着。

### Fix

1. **L1 分两类，两个窗口**（`repair_gaps.py`）：
   - 零行缺口（当天 0 只 = 那天从没抓过，**没有序列可污染**）→ 宽窗
     `MISSING_DAYS_CAP = 60` 个交易日，可自动补抓。
   - 部分缺口（当天 1~279 只 = 抓了但没抓全，补它会与既有序列**混复权口径**）
     → 窄窗 `MAX_PRICE_DAYS = 7` 个交易日，超期只报警。
   - 分类依据是新增的 `Gap.codes` 字段（体检报告落盘当天实际只数）。
     以前只能从 detail 字符串反解「行情只有 N 只」—— 那是拿文案当数据源。
   - 报告里没有 `codes`（旧报告）→ 按**保守**口径当部分缺口处理，宁可少修。
2. **窗口按交易日算**（`_trading_days_back`）：日历日窗口在长假后必然错判。
   日历不可用 → 一律当超窗（只报警），绝不退化成日历日。
3. **复权口径可检测**：`daily_prices` 加 `adj_basis` 列（migration），
   备源链按口径优先级选（不复权优先），混口径事后可查。
4. **通知说人话**：原因按**类别**归组（不再把「尝试过但失败」和
   「压根没试」混成一句）；建议**点名正确的产物**（CI 缓存库 vs 本地库）。
   修复失败时记 `failed_command`，通知里能看出是「拉行情」那步挂了。
5. **陈旧谱系自愈**：`daily-audit.yml` 加谱系检测（`db_freshness` rc=3）
   + 冷启动同步（400 天），命中就弃库重建，而不是在烂谱系上打补丁。

### 踩坑记录
- **口径只放进返回值 ≠ 落库带上了口径**（复核时发现并修掉）：第一版实现让
  `fetch_daily_kline_smart` 返回 `(rows, name, source, basis)`，但**没有把 basis
  盖到每一行上**。`PriceRow.adj_basis` 的默认值是 `"none"` ⇒ 腾讯（前复权）抓回来
  的行落库时带着默认的 `"none"` ⇒ **比没有这一列更坏**：库里的标记在说「没混口径」，
  实际已经混了。这比「没有列」更隐蔽 —— 检测看起来是绿的。
  正确做法：在 `fetch_daily_kline_smart` 里对返回的每一行 `row.adj_basis = basis`，
  并加测试守住（`tests/test_price_adj_basis.py::TestBasisStampedOnRows`）。
  ⇒ **凡是「新增一列用来标记某种状态」，都要问一句：谁在写这一列？**
- 旧的 `test_price_window_follows_report_today_not_wall_clock` 用 **2026-10-18**
  做断言 —— 那是**周日**，从来不会出现在体检报告里。改成交易日口径后
  它的边界变了，才暴露出来。**测试里的日期要用真实交易日。**
- macOS bash 3.2：`[ "$x" = "3" ] && echo ...` 放在 if 外面，条件不成立时
  整个 step 的退出码变成 1 → 步骤失败。要写成 `if ...; then ...; fi`。
- workflow 里 `steps.trade-day.outputs.today_bj` 必须在**引用它的步骤之前**产出。
  把谱系检测插到 trade-day 之前 → 那个变量是空 → 冷启动永远不触发。

### Metadata
- Reproducible: yes
- Related Files: scripts/repair_gaps.py, scripts/audit_coverage.py,
  scripts/notify_audit.py, scripts/db_freshness.py, data_providers.py,
  db_repository.py, db/migrations/2026_10_30_daily_prices_adj_basis.sql,
  .github/workflows/daily-audit.yml
- Tests: tests/test_repair_gaps.py, tests/test_audit_coverage.py,
  tests/test_notify_audit.py, tests/test_daily_audit_workflow.py,
  tests/test_price_adj_basis.py
- See Also: ERR-20261029-001（两谱系分叉）

---

## [ERR-20261031-001] 「零行 vs 部分」两类分法不够细（10-30 修复的后续）

**Logged**: 2026-10-31T08:50:00+08:00
**Priority**: medium
**Status**: fixed
**Area**: ci

### Summary
10-30 的修复把 L1 缺口分成「零行（宽窗）/ 部分（窄窗）」两类。10-31 的巡检
暴露出：CI 缓存里的缺口**全是部分缺口**（65~143 只），所以新代码仍然
把它们判成「窄窗只报警」—— 修复没解决实际问题。

### Error
```
跳过 [L1] 2026-10-14 — 行情缺口超出 7 天自动修复窗（复权口径风险），只报警（行情只有 143 只）
跳过 [L1] 2026-10-23 — 行情缺口超出 7 天自动修复窗（复权口径风险），只报警（行情只有 65 只）
```
（只数单调递减 143→140→137→132→132→124→100→65）

### Root cause
「部分缺口」这个标签把两种**成因完全不同**的情况混成了一类：

1. **同步被中断**（真实主因）：并发处理 300 只票时进程被打断，前面写进去了、
   后面没写完。已知的行**本来就是不复权**（东财主源）⇒ 补齐剩下的票
   **不会混口径** ⇒ 本来就该补，不该只报警。
   只数单调递减（143→140→…→65）正是这个形状的指纹。

2. **备源口径不同**：当天的行来自腾讯 qfq（前复权）⇒ 补不复权的行会混口径
   ⇒ 只能报警（这是窄窗当初要防的真风险）。

只数分不出这两者 —— **要看的不是「有多少只」，而是「已有行是什么口径」**。

### Fix
- 体检报告的 `Gap` 新增 `bases`（当天已有行用到的复权口径集合），
  由 `audit_coverage._read_layer_counts` 按 `daily_prices.adj_basis` 读出。
- 修复脚本按口径再分一次：
  - `bases == ["none"]`（口径一致）⇒ 走**宽窗**，自动补齐
  - `bases` 含 `"qfq"` 或混了两种 ⇒ 保留**窄窗**，只报警
  - `bases` 不可得（旧报告没有该字段）⇒ 保守按窄窗处理
- 新增原因类别 `partial_uniform_out_of_window`（「口径一致但超窗，
  没有混口径风险，人工补抓即可」），通知里不与真有风险的混在一起说。

### 踩坑记录
- **通知文案版本 ≠ 代码版本**：10-31 早上收到的通知用的是旧措辞
  （「超出 7 天自动修复窗」「手动跑 bash a-finder.sh fallback」），
  查 GitHub API 才发现那次 run 的 `head_sha` 是 `cd80ce38` —— 修复的推送
  在那次 run **开始 15 分钟后**才落地。看到「代码里明明改了但行为没变」，
  先确认 **run 用的 commit**（`actions/runs/<id>` 的 `head_sha`），
  再怀疑代码。
- GitHub Actions 的 **artifacts / logs 下载需要认证**（401/403），匿名只能看
  run 与 job 的元数据（`head_sha`、step 结论）。要拿 DB 快照就得本地复现。

### Metadata
- Reproducible: yes
- Related Files: scripts/audit_coverage.py, scripts/repair_gaps.py,
  scripts/notify_audit.py
- Tests: tests/test_repair_gaps.py, tests/test_audit_coverage.py,
  tests/test_notify_audit.py
- See Also: ERR-20261030-001

---
