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
