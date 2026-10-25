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
