# CI Cache Defect Fix — Design & Patch Plan

> DESIGN ONLY — no source files were modified. Baseline test suite: **668 passed**.

---

## 0. Verification performed (all re-verified against the live repo)

- Local `hs300.db`: `MAX(trade_date)=2026-10-29`, 149,581 price rows, 300/300 codes for every
  trading day 10-12..10-29, `MAX(daily_picks.date)=2026-10-29`, `MAX(trade_plan.plan_date)=2026-10-29`. Healthy.
- `git log origin/gh-pages` → exactly one commit: `d159591 update site data 2026-10-29-1510`.
  Message format matches `a-finder.sh:157` `git commit-tree -m "update site data $(date +%F-%H%M)"`
  → **local publish path confirmed**, CI never published.
- Live `https://ycu.space/data/dates.json` → 281 dates, latest `2026-10-29`. User-visible data is fixed.
- `'hs300-db'.startswith('hs300-db-')` → `False`. Orphan confirmed.
- **Data-source probe (new, decisive):**
  - Tencent `qfqday` for 600519 → last row `2026-10-28` (15 rows)
  - Sina → last row `2026-10-28` (15 rows)
  - Eastmoney `push2his` → last row `2026-10-29` (16 rows, has `amount`/`turnover`)
  - ⇒ **10-29 exists only on Eastmoney.** Confirmed empirically, not assumed.
- **`_build_missing_segments` on a 10-13 orphan DB** → `[('20261014','20261029')]` — one segment.
- **Actual `plan_repairs` output for the 12-day gap** (reproduced against the real code):
  - L1 sync command is `--start 2026-10-22 --end 2026-10-29` (7-day window only)
  - 10-14..10-21 land in `skipped` with reason `行情缺口超出 7 天自动修复窗（复权口径风险），只报警`
  - ⇒ **ISSUE D's mis-attribution confirmed exactly**: the notification's deduped "原因" clause
    describes only the 6 out-of-window dates, masking the 6 in-window dates that were attempted and failed.

### Three findings that materially change the plan (not in the brief)

**F1 — There is a FOURTH workflow on the same cache: `.github/workflows/weekly-evolve.yml`.**
It restores `hs300-db-${{ github.run_id }}` / `restore-keys: hs300-db-` and saves
`hs300-db-${{ github.run_id }}` with **no `if:` guard at all** (lines 39-43). It is an uncontrolled
lineage-root writer, runs Fridays 08:30 UTC, and must be included in every cache-key/guard change.
Any plan that touches only the three named workflows will leave this one writing a possibly-degraded
DB into the shared lineage.

**F2 — The fallback chain mixes adjustment bases, and the DB records none of it.**
- Eastmoney: `fqt=0` (不复权) — `data_providers.py:199`
- Sina: 不复权 — `data_providers.py:127-129`
- **Tencent: `qfq` (前复权)** — `data_providers.py:86,103`
- `daily_prices` schema (`db_schema.py:17-31`) has **no adjustment-basis column**.
⇒ When Eastmoney is unreachable, the chain silently writes 前复权 prices into a 不复权 table, with no
marker and no way to detect it later. `repair_gaps.py`'s "复权口径风险" is therefore a real structural
hazard, not a hypothetical. Probe shows EM/TX currently agree for 600519 (no recent dividend) but they
diverge the instant a split/dividend lands. This matters directly to ISSUE A option (i) — "widen the L1
window" is *more* dangerous than the brief implies, because it increases the number of rows that may be
written by a mixed-basis fallback.

**F3 — The 10-29 CI failure was a data-source fallback, not a calendar or gap-detection bug.**
The 13:00 UTC audit run had Eastmoney unreachable/blocked → chain fell to Tencent/Sina → 10-29
(Eastmoney-only) could never appear → the 7-day-window L1 repair for 10-22..10-29 was *attempted but
could not succeed*. This is consistent with the brief's own note and with the empirical probe. It means
**fixing only the gap-detection window (option i) would not have fixed 10-29** — a source-priority /
basis-awareness change is required.

---

## 1. ISSUE C — Recommended overall strategy

### Recommendation: **CI-authoritative for the price/行情 lineage, local-authoritative for the site publish — with an explicit, tested "CI is dead" confirmation gate before the local leg publishes.**

This is a hybrid, and it is the only option consistent with all three hard constraints in the repo.

**Why not "local-authoritative" (the current de-facto state):**
- `CLAUDE.md` states the design intent explicitly: 「**唯一存储**：GitHub Actions 的 SQLite 缓存」.
  The local DB is described as a *debug* artifact, and `本地 hs300.db 与 CI 的库会分叉` is documented
  as expected-but-unwanted.
- The local leg is a **single laptop**. `fallback-install` generates a launchd plist that
  「launchd 只在机器开机时触发（睡眠会延后到唤醒）」. The user's actual need — "site updated every
  trading day" — is met only if *some* leg runs daily.
- The local DB is the one that had to be hand-repaired; a single-disk single-machine lineage is the
  weakest possible guarantee for a published financial dataset.
- Making the local DB authoritative formally would mean rewriting `CLAUDE.md`'s architecture section,
  the "行情数据路径" section, and every comment in the three workflows that says the cache is the store.
  That is a large, semantically expensive change to fix a defect that is actually a *triggering* defect.

**Why not "pure CI-authoritative" (remove the local publish leg):**
- Documented, reproducible: 10-26's 07:30 shift ran at 14:27 UTC (7h late); 10-27's three shifts
  **never fired at all**. The `Cloudflare Workers Cron` leg needs secret `GITHUB_DISPATCH_TOKEN`, which
  per `CLAUDE.md` is **not configured** ⇒ that leg is empty. `weekly-evolve.yml` proves workflows do run
  on this repo, but only on some triggers.
- If the local leg is removed, a missed GitHub schedule = a stale site, silently. The current 3-leg
  design exists precisely because all legs are unreliable.

**Why the hybrid works:** the two lineages diverge on *what is authoritative*, and the freshness
short-circuit is what *fused* them wrongly.
- `deploy-site.yml:82-90` reads the **live site's** `dates.json` and skips the whole run when it already
  has today. But `dates.json` is produced by `export_json.py` from `daily_picks`/`trade_plan`
  (`export_json.py:365-374` → `app.py:135 list_dates`), **never from `daily_prices`**. So the local
  publish makes the CI's *own freshness oracle* lie to it: the site says "10-29 published" (true, from
  the local DB) while the CI cache DB is still frozen at 10-13. The CI then skips its sync **and its
  cache save** (lines 93 and 173 both carry `steps.freshness.outputs.skip != 'true'`), so the CI DB can
  never advance even if the schedule does fire. **That is the self-lock.**
- The fix is to make the freshness oracle read the **CI's own DB**, not the site.

### Concrete design

**C1. `deploy-site.yml` freshness reads the restored CI DB, not the live site.**
Replace the site probe with a probe of the *cache-restored* `hs300.db`. But the restore step currently
runs **after** the freshness step (line 92 vs 73-90), so the DB is not available yet. Two options:
- **(C1a, recommended)** Move the restore step **above** the freshness step, and make freshness ask
  *"is my CI DB already current?"* → `MAX(daily_prices.trade_date) >= EXPECT` **and**
  `COUNT(DISTINCT code) on EXPECT >= 280`. If yes → skip (this is the *legitimate* short-circuit: this
  shift has nothing to do). If no → run.
- **(C1b)** Keep the site probe but *only* as a secondary signal, and never let it skip the **cache save**.

C1a is cleaner and removes the fusion entirely. The cost: the restore now happens even for shifts that
will be skipped — but that is a cheap local cache read (~seconds), whereas the current behaviour costs a
full 400-day cold start.

**C2. The site freshness check becomes a *publish* decision, not a *sync* decision.**
The current code uses one `skip` output for both "don't sync" and "don't save cache". Split into two
outputs:
- `skip_sync` — "this shift has no new market data to fetch" (drives the sync/plan/export/deploy steps)
- `skip_cache_save` — **never true when the DB was modified**. The cache save must always run after a
  restore+sync, otherwise the lineage dies silently.

**C3. The local fallback gains a "CI is confirmed dead" gate.**
`cmd_fallback` (`a-finder.sh:379-411`) currently publishes whenever the *site* lags. Change it so the
local leg publishes only when the site lags **and** CI has demonstrably failed to publish — i.e. after a
grace window. Concretely, `scripts/needs_fallback.py` should gain a `--min-wait-hours` notion, and
`cmd_fallback` should require the site to have been stale for longer than the CI leg's full retry window
(8 cron shifts 07:30–09:00 UTC + the 13:00 UTC audit ⇒ **≥ 8 hours** after the Beijing close). This
preserves the user's "site updated every trading day" guarantee (the local leg still fires, just not at
17:10 the same evening — it fires after CI has demonstrably had its chances) while removing the same-day
local publish that poisons the CI freshness oracle.

**C4. `scripts/needs_fallback.py` must judge staleness against the *price* lineage, not the *pick* lineage.**
Today `needs_fallback.decide` compares `dates.json`'s latest (a `daily_picks` date) against the expected
trade date. That is the metric that lies. Add a second, authoritative signal:
`MAX(daily_prices.trade_date)` in the local DB. The site is "current" only if **both** the published picks
date and the local price date are current.

**Net effect:** CI owns the price lineage (per `CLAUDE.md`); the local DB owns nothing but can *rescue the
site* when CI is provably dead. The two lineages stop cross-poisoning because the CI's freshness oracle
stops reading the site.

---

## 2. Patch plan per issue

### ISSUE A — Make the orphaned CI DB converge

**A1. Add a staleness/orphan detector: `scripts/db_freshness.py` (new file).**
A single reusable predicate used by all four workflows.

```python
#!/usr/bin/env python3
"""缓存库谱系新鲜度判定：MAX(daily_prices.trade_date) 落后太多 → 孤儿库。

为什么需要：actions/cache 不允许覆盖已存在的 key。2026-10-26 建档时用了固定
key `hs300-db`，2026-10-26 17:49 改成 `hs300-db-<run_id>` 后，旧条目因
`'hs300-db'.startswith('hs300-db-')` 为 False 而**永久不可达** —— CI 行情谱系
断在 2026-10-13，增量同步永远补不上（缺口在窗口外）。

判定口径：**落后 N 个交易日**（不是 N 个日历日），且 N 用交易日历算 ——
用日历日会在长假后误判成孤儿（国庆 8 天日历日 = 5 个交易日）。
"""
```
API:
- `latest_price_date(db_path) -> Optional[str]` — `SELECT MAX(trade_date) FROM daily_prices`;
  `None` if the table is missing/empty.
- `stale_trading_days(db_path, *, today=None, max_lag=STALE_TRADING_DAYS) -> dict` — returns
  `{"latest": ..., "lag_trading_days": int|None, "stale": bool, "reason": str}`.
- `STALE_TRADING_DAYS = 3` — 3 trading days of lag is far beyond any legitimate schedule delay (a
  shift can be 7h late; 2 shifts cover a weekend) but well within the "the lineage is broken" range
  (the orphan was 12 trading days behind).

`max_lag` must be configurable via CLI (`--max-lag`) and via an env var
(`HS300_STALE_TRADING_DAYS`) so `config.py`-style tunability is respected and tests can pin it.

**A2. `daily-audit.yml` — cold-start rebuild when the restored DB is an orphan.**
After the restore step, add a step that classifies the DB and, if it is an orphan, discards it and forces
a 400-day cold start. Insert after the "Restore hs300.db from cache" step (lines 42-48), before the
trade-day step:

```yaml
      - name: 判定缓存库谱系是否孤儿（落后太多 → 冷启动重建）
        id: lineage
        env:
          MAX_LAG: "3"
        run: |
          set -uo pipefail
          if [ ! -f hs300.db ]; then
            echo "orphan=true" >> "$GITHUB_OUTPUT"
            echo "cold_start=true" >> "$GITHUB_OUTPUT"
            echo "::notice::无缓存 DB → 冷启动"
            exit 0
          fi
          .venv/bin/python scripts/db_freshness.py --db hs300.db \
            --max-lag "$MAX_LAG" --github-output || {
              echo "orphan=true" >> "$GITHUB_OUTPUT"
              echo "cold_start=true" >> "$GITHUB_OUTPUT"
              echo "::warning::新鲜度判定失败 → 按孤儿处理（冷启动）"
              exit 0
            }
          if grep -qx 'orphan=true' "$GITHUB_OUTPUT"; then
            echo "cold_start=true" >> "$GITHUB_OUTPUT"
            echo "::error::缓存库谱系落后过多（孤儿）—— 丢弃该库，改走冷启动重建"
            rm -f hs300.db
          fi
```
(The script writes `orphan=true|false`, `cold_start=true|false`, `latest=<date>`, `lag=<n>` to
`$GITHUB_OUTPUT`; the shell block above consumes them. The `rm -f hs300.db` is the load-bearing step —
it forces `deploy-site`'s existing `COLD=1` detection (`deploy-site.yml:118`) and, in `daily-audit`,
forces the sync to start from nothing.)

**A3. `daily-audit.yml` — the repair step must know about cold start.**
When `cold_start == true`, `repair_gaps.py`'s 7-day L1 window is irrelevant (the whole 400-day history is
missing). The audit itself will report L1 gaps for the entire 30-day window. The fix:
- When `cold_start == true`, run a **400-day cold sync first**, before the audit.
- Insert a "冷启动同步" step **after the trade-day step** (so `EXPECT` is available):

```yaml
      - name: 冷启动同步（孤儿谱系重建，400 天）
        if: steps.lineage.outputs.cold_start == 'true'
        env:
          EXPECT: ${{ steps.trade-day.outputs.today_bj }}
          SYNC_DAYS: "400"
          SYNC_CONCURRENCY: "8"
          SYNC_RATE: "8"
          SYNC_RETRIES: "3"
        run: |
          set -uo pipefail
          bash a-finder.sh sync-incremental hs300.db 20 pick-history 2>&1 | tail -60 || true
```

**A4. `scripts/repair_gaps.py` — distinguish "missing rows" from "adjustment-basis risk".**
With F2 in hand, the distinction is now *measurable*: an L1 gap with **0 rows** for that date is a "the
day was never fetched" case — filling it cannot corrupt a *series* (there is no series to corrupt). An L1
gap with **1..279 rows** is a *partial* day — filling it **can** mix bases mid-series and is genuinely
dangerous.

- `audit_coverage.py`: add a per-gap **row count** so the repair planner can classify. `Gap` currently
  carries `detail` (a formatted string). **Do not parse the string.** Add a new field
  `codes: Optional[int] = None` to `Gap` and to `Report.to_dict()`/`from_dict()`.
  - `audit_coverage.py:151-153`: `codes = prices.get(d, 0)` →
    `Gap(d, "L1", f"行情只有 {codes} 只（阈值 {min_codes}）", codes=codes)`.
  - `Gap.from_dict` (`audit_coverage.py:53-57`) must accept the extra key without breaking
    (`Gap(**payload)` is already avoided — good — so add `codes=payload.get("codes")`).
  - `Gap.to_dict` adds `"codes": self.codes`.
- Add module constants in `repair_gaps.py`:
  ```python
  MAX_PRICE_DAYS = 7          # 行情缺口自动修复的时间窗（天）—— 部分缺口（有行但不全）仍受此限制
  MISSING_DAYS_CAP = 60      # 「整天没有一行」的缺口放宽到 60 个交易日（无序列可污染）
  ```
- In `plan_repairs` (`repair_gaps.py:222`), split the L1 dates:
  ```python
  l1_all = sorted(by_layer.get("L1", []))
  l1_zero = [d for d in l1_all if _gap_codes(report, d) == 0]      # 整天缺 → 可放宽
  l1_partial = [d for d in l1_all if d not in set(l1_zero)]          # 部分缺 → 保留 7 天窗
  ```
  and apply `MISSING_DAYS_CAP` to `l1_zero` and `MAX_PRICE_DAYS` to `l1_partial`.
- `_horizon` (`repair_gaps.py:85-86`) must become **trading-day** based, not calendar-day:
  ```python
  def _horizon(today: dt.date, max_price_days: int) -> str:
      """窗口基准日：按**交易日**回看 N 个交易日（不是日历日）。

      用日历日会在长假后误判：国庆 8 个日历日只有 5 个交易日，
      7 个日历日的窗会把长假前的正常日判成「超窗」。
      """
      days = trading_days(today - dt.timedelta(days=max_price_days * 3 + 14), today)
      ...
  ```
  ⚠️ This is a behavioural change to a well-tested function. `tests/test_repair_gaps.py` has
  `test_price_window_follows_report_today_not_wall_clock` and `test_old_l1_gap_not_planned` with
  `TODAY = dt.date(2026,10,27)`. **Both must still pass** — with a trading-day horizon, 10-18 vs 10-27
  is the 7th trading day back ⇒ `test_old_l1_gap_not_planned` (10-10) still passes, and
  `test_price_window_follows_report_today_not_wall_clock` (10-18 with today=10-20) still passes.
  **Verify both explicitly.**
- `skipped_repairs` (`repair_gaps.py:244-261`) must mirror the same split, so the notification's per-date
  reasons are correct (this feeds ISSUE D).

**A5. Source priority + basis safety (the actual reason 10-29 could not be fetched).**
F3 shows the CI's 10-29 failure was Eastmoney being unreachable and the chain silently falling to
Tencent. Two changes in `data_providers.py`:
1. **Never silently mix bases.** `fetch_daily_kline_smart` (`data_providers.py:483-514`) currently returns
   the first non-empty source. Add a basis tag to the source registry:
   ```python
   SOURCES = ["eastmoney", "sina", "tencent"]
   SOURCE_BASIS = {"eastmoney": "none", "sina": "none", "tencent": "qfq"}
   # 前复权源（腾讯 qfq）与主源（东财 fqt=0）口径不同，而 daily_prices
   # 没有口径字段 —— 混写会让序列在除权日出现假跳空。
   PREFERRED_BASIS = "none"
   ```
   and have `fetch_daily_kline_smart` skip sources whose basis ≠ `PREFERRED_BASIS` **unless** no preferred
   source is available, in which case it returns `(rows, name, basis)` so the caller can log the basis used.
   ⚠️ **This is the highest-risk change in the whole plan.** It can make the sync fetch *nothing* where it
   previously fetched (wrong-basis) data. Mitigation: (a) keep the fallback but **log the basis
   explicitly** and **record it in the DB**; (b) ship it behind a flag.
2. **Record the basis in the DB.** Add a migration
   `db/migrations/2026_10_29_daily_prices_adj_basis.sql`:
   ```sql
   ALTER TABLE daily_prices ADD COLUMN adj_basis TEXT;
   ```
   plus a `db_repository.py` writer change so every inserted row carries `adj_basis`. This makes future
   mixed-basis incidents *detectable* (`SELECT DISTINCT adj_basis, trade_date FROM daily_prices`) instead
   of invisible. (Per `CLAUDE.md`: additive changes via migration files; **do not** edit `db_schema.py`
   for the existing table — its `CREATE TABLE IF NOT EXISTS` only applies to fresh DBs anyway.)
   - `insert_prices`/`upsert_prices` in `db_repository.py` must write the basis; `PriceRow` needs
     `adj_basis: str = "none"`.
   - `PriceRow` is also produced by `akshare_data_provider.py` and `market_data.py` — all constructors need
     the default.

**A6. Recommendation on the brief's three options.** Given A4+A5, I recommend **(iii) staleness detector +
cold-start rebuild** (A1/A2/A3) as the *primary* mechanism, with **(i) a widened window for zero-row gaps**
(A4) as the *secondary* mechanism. I recommend **against (ii) importing the local DB into CI**: the local
DB is 149k rows / ~30 MB, well within the 10 GB cache budget, but importing it makes the CI's lineage
depend on a laptop's disk, contradicting `CLAUDE.md`'s design intent and reintroducing exactly the
two-lineage problem we are trying to kill. If the operator wants a one-time rescue instead of a
mechanism, A2/A3's cold start achieves the same result in ~10 minutes of CI time with zero new trust
assumptions — strictly better than importing the local DB.

---

### ISSUE B — The unreachable `hs300.db` cache, and the "restore too old" risk

**B1. Delete the orphan via the Actions UI — acceptable, and the *only* right move.**
`'hs300-db'.startswith('hs300-db-')` is `False`, so the 2026-10-26 fixed-key entry is **unreachable by every
workflow** and is now pure garbage occupying cache quota. It cannot be "migrated" in any useful sense (its
content is the 10-13 orphan, which A2/A3 will rebuild from cold start anyway).
- **Action (manual, one time):** GitHub UI → repo → Actions → Caches → find the entry with key `hs300-db`
  → **Delete**.
- **Rationale for not migrating it:** its price data ends 2026-10-13 and its `daily_picks`/`trade_plan` are
  the *other* lineage's; importing it into the current lineage would reintroduce the mixed-lineage
  contamination that caused the incident.
- **Note on the alternative:** the *gap-heal.yml artifact* backup (`hs300-db-<STAMP>.db.gz`, 90-day
  retention) could be restored instead — but that artifact may be from the **local** lineage, so it is the
  "(ii) import local DB" option in disguise. The cold start is cleaner.

**B2. The "restore-keys could pick an ancient entry" risk is REAL and must be closed.**
`restore-keys: hs300-db-` is a **prefix match with no time bound**. `actions/cache` picks the **most recently
created** matching entry. With `run_id` keys, the newest matching entry is normally fine. But there are
three concrete ways an ancient entry wins:
- If all recent workflows' saves failed, the newest matching entry could be weeks old.
- `weekly-evolve.yml` saves unconditionally — if it saves a *degraded* DB on Friday, then Monday's
  `deploy-site` restores the Friday entry (5 days old).
- `gap-heal.yml` saves unconditionally too (lines 110-115).

⇒ **A prefix restore alone is not a freshness guarantee.** This is why A1 (the explicit freshness check
after restore) is mandatory, not optional.

**B3. The explicit post-restore freshness check (all four workflows).**
After every `actions/cache` restore, insert a guard step that classifies the restored DB and, if stale,
**discards it and forces a cold start** (the same `db_freshness.py` from A1).

`deploy-site.yml` — **BEFORE** (lines 92-101):
```yaml
      - name: Restore hs300.db from cache
        if: steps.trade-day.outputs.is_trade_day == 'true' && steps.freshness.outputs.skip != 'true'
        uses: actions/cache@v4
        with:
          path: hs300.db
          key: hs300-db-${{ github.run_id }}
          restore-keys: |
            hs300-db-
```

**AFTER** (note: restore moves above freshness):
```yaml
      - name: Restore hs300.db from cache
        if: steps.trade-day.outputs.is_trade_day == 'true' && steps.freshness.outputs.skip != 'true'
        uses: actions/cache@v4
        with:
          path: hs300.db
          key: hs300-db-${{ github.run_id }}
          restore-keys: |
            hs300-db-

      - name: 缓存库新鲜度校验（落后太多 → 丢弃并冷启动）
        id: lineage
        if: steps.trade-day.outputs.is_trade_day == 'true' && steps.freshness.outputs.skip != 'true'
        env:
          MAX_LAG: "3"
        run: |
          set -uo pipefail
          if [ ! -f hs300.db ]; then
            echo "stale=false" >> "$GITHUB_OUTPUT"
            echo "cold_start=true" >> "$GITHUB_OUTPUT"
            echo "::notice::无缓存 DB → 冷启动"
            exit 0
          fi
          .venv/bin/python scripts/db_freshness.py --db hs300.db \
            --max-lag "$MAX_LAG" --github-output || {
              echo "stale=true" >> "$GITHUB_OUTPUT"
              echo "cold_start=true" >> "$GITHUB_OUTPUT"
              echo "::warning::新鲜度判定失败 → 按孤儿处理"
              exit 0
            }
          if grep -qx 'stale=true' "$GITHUB_OUTPUT"; then
            echo "cold_start=true" >> "$GITHUB_OUTPUT"
            echo "::error::缓存库落后过多（谱系孤儿）—— 丢弃，走冷启动重建"
            rm -f hs300.db
          fi
```

And the sync step's cold-start detection (lines 117-122):
**BEFORE**
```bash
          COLD=0
          [ -f hs300.db ] || COLD=1
```
**AFTER**
```bash
          COLD=0
          [ -f hs300.db ] || COLD=1
          [ "${{ steps.lineage.outputs.cold_start }}" = "true" ] && COLD=1
```

**B4. Also add the freshness guard to `gap-heal.yml` and `weekly-evolve.yml`.**
Both restore with `restore-keys: hs300-db-` and both save unconditionally. See ISSUE E.

**B5. Explicit note on `restore-keys` scope.** Consider narrowing `restore-keys` to a *generation* prefix
(e.g. `hs300-db-v2-`) so that a future key-format migration is atomic and a stale generation can never be
restored. Combined with deleting the `hs300-db` orphan and A2's cold start, this makes the lineage
self-describing. ⚠️ This requires the key format change to land in **all four** workflows atomically
(otherwise the workflows restore *different* lineages from each other). Recommend it as a follow-up, not in
the first patch.

---

### ISSUE C — see §1 above. Concrete YAML before/after for `deploy-site.yml`

**C1 + C2: freshness reads the CI DB, and the cache save is never skipped by freshness.**

**BEFORE** (lines 73-90, the freshness step):
```yaml
      - name: 检查是否需要跑（补救 cron 遇到「已发布」则跳过）
        id: freshness
        if: steps.trade-day.outputs.is_trade_day == 'true'
        env:
          EXPECT: ${{ steps.trade-day.outputs.today_bj }}
        run: |
          # 密集排班下每一班都先看线上是否已发布当天：
          # 已发布 → 秒退（避免重复消耗 CI 分钟与数据源配额）
          # 未发布 → 继续跑（这正是我们想要的补救）
          published=$(curl -sf --max-time 20 https://ycu.space/data/dates.json \
            | python3 -c "import json,sys;d=json.load(sys.stdin).get('dates') or [''];print(d[0])" 2>/dev/null || echo "")
          if [ "$published" = "$EXPECT" ]; then
            echo "::notice::线上已发布 $EXPECT，本班跳过"
            echo "skip=true" >> "$GITHUB_OUTPUT"
          else
            echo "线上最新 ${published:-无} ≠ 期望 $EXPECT，继续"
            echo "skip=false" >> "$GITHUB_OUTPUT"
          fi
```

**AFTER** — the step is **moved below the restore + lineage steps**, and its oracle becomes the CI's own DB:
```yaml
      - name: 检查是否需要跑（以**缓存库**行情为准，不看线上站点）
        id: freshness
        if: steps.trade-day.outputs.is_trade_day == 'true'
        env:
          EXPECT: ${{ steps.trade-day.outputs.today_bj }}
          MIN_CODES: "280"
        run: |
          # 为什么不看线上 dates.json：dates.json 来自 daily_picks/trade_plan，
          # **不看 daily_prices**（export_json.py）。本地兜底把当天发到线上后，
          # 这里会看到「已发布」→ 跳过同步 **和跳过缓存保存** → CI 谱系被永久
          # 锁死（2026-10-29 实测）。判据必须是「**我自己的库**到不到位」。
          #
          # 缓存库已被上面的 lineage 步判为孤儿并删除 → 一律不跳过，走冷启动。
          if [ "${{ steps.lineage.outputs.cold_start }}" = "true" ]; then
            echo "::notice::缓存库谱系孤儿 → 本班必须冷启动重建，不跳过"
            echo "skip=false" >> "$GITHUB_OUTPUT"
            echo "skip_cache_save=false" >> "$GITHUB_OUTPUT"
            exit 0
          fi
          if [ ! -f hs300.db ]; then
            echo "skip=false" >> "$GITHUB_OUTPUT"
            echo "skip_cache_save=false" >> "$GITHUB_OUTPUT"
            exit 0
          fi
          latest=$(.venv/bin/python -c "import sqlite3;r=sqlite3.connect('hs300.db').execute('SELECT MAX(trade_date) FROM daily_prices').fetchone();print(r[0] if r and r[0] else '')" 2>/dev/null || echo "")
          codes=$(.venv/bin/python -c "import sqlite3,sys;d=sys.argv[1];r=sqlite3.connect('hs300.db').execute('SELECT COUNT(DISTINCT code) FROM daily_prices WHERE trade_date=?',(d,)).fetchone();print(r[0] if r and r[0] else 0)" "$latest" 2>/dev/null || echo 0)
          echo "缓存库行情最新日=${latest:-无}，当日 ${codes:-0} 只"
          if [ -n "$latest" ] && [ "$latest" \> "$EXPECT" ]; then
            echo "::notice::缓存库已覆盖 $EXPECT 及以后，本班无需再抓"
            echo "skip=true" >> "$GITHUB_OUTPUT"
          elif [ "$latest" = "$EXPECT" ] && [ "${codes:-0}" -ge "$MIN_CODES" ]; then
            echo "::notice::缓存库已完整覆盖 $EXPECT（${codes} 只），本班跳过"
            echo "skip=true" >> "$GITHUB_OUTPUT"
          else
            echo "缓存库未覆盖 $EXPECT（最新 ${latest:-无} / ${codes:-0} 只）→ 继续"
            echo "skip=false" >> "$GITHUB_OUTPUT"
          fi
          # 缓存保存永远跟随「本班是否动过库」，与 skip 解耦：
          echo "skip_cache_save=false" >> "$GITHUB_OUTPUT"
```
Then the save step (lines 172-177) drops the `freshness` condition but keeps the trade-day condition:
**BEFORE**
```yaml
      - name: Save hs300.db to cache
        if: always() && steps.trade-day.outputs.is_trade_day == 'true' && steps.freshness.outputs.skip != 'true'
```
**AFTER**
```yaml
      - name: Save hs300.db to cache
        # 不看 freshness：只要本班动过库就必须落缓存，
        # 否则「已发布就跳过」会把 CI 谱系锁死（2026-10-29 实测）。
        # 是否落「好」的库由 ISSUE E 的门禁决定。
        if: always() && steps.trade-day.outputs.is_trade_day == 'true'
```

**C3: the local fallback must confirm CI is dead before publishing.**
`a-finder.sh` `cmd_fallback` (lines 379-411) — **BEFORE**:
```bash
  local need=0 tmp
  tmp="$(mktemp)"
  if curl -sf --max-time 20 "https://ycu.space/data/dates.json" -o "$tmp"; then
    python3 "$ROOT_DIR/scripts/needs_fallback.py" --dates-json "$tmp" || need=$?
  else
    echo "⚠ 无法读取线上日期，按需要兜底处理"
    need=1
  fi
```
**AFTER** — add a CI-dead grace gate:
```bash
  local need=0 tmp
  tmp="$(mktemp)"
  if curl -sf --max-time 20 "https://ycu.space/data/dates.json" -o "$tmp"; then
    python3 "$ROOT_DIR/scripts/needs_fallback.py" --dates-json "$tmp" \
      --price-date "$(max_price_date "$DEFAULT_DB")" \
      --min-wait-hours "${FALLBACK_MIN_WAIT_HOURS:-8}" || need=$?
  else
    echo "⚠ 无法读取线上日期，按需要兜底处理"
    need=1
  fi
```
with a new helper in `a-finder.sh`:
```bash
max_price_date() {
  # 本地库行情最新日（daily_picks 的日期不能代表行情 —— export_json 就是这么出 dates.json 的）
  local db="${1:-$DEFAULT_DB}"
  [ -f "$db" ] || return 0
  python3 -c "import sqlite3,sys;r=sqlite3.connect(sys.argv[1]).execute('SELECT MAX(trade_date) FROM daily_prices').fetchone();print(r[0] if r and r[0] else '')" "$db" 2>/dev/null || echo ""
}
```
and `scripts/needs_fallback.py` gains `--price-date` (the local DB's price date) and `--min-wait-hours`:
- `decide()` becomes a 3-input decision: the site is current only if **the published picks date is current
  AND the local price date is current**.
- The grace gate: the site must have been stale for ≥ `--min-wait-hours` before the local leg publishes.
  This is what "CI is confirmed dead" means operationally: CI's whole retry window (8 shifts
  07:30–09:00 UTC + the 13:00 UTC audit) has elapsed.
- **Trade-off to state explicitly:** with an 8-hour grace, the site publishes ~01:00 Beijing (next day)
  instead of 17:10 Beijing. If the user prefers same-evening publication, set `--min-wait-hours 0` and
  accept that the CI freshness oracle will again see a published site. **Because C1/C2 change the CI oracle
  to read the CI's own DB, `--min-wait-hours 0` is now safe** — the local publish no longer poisons the CI.
  Recommend `--min-wait-hours 0` **after** C1/C2 land, and `--min-wait-hours 8` **before** them (as a
  stopgap). **Order matters: C1/C2 must land before or together with the grace period.**

**C4: `scripts/needs_fallback.py` — judge by price lineage, not pick lineage.** Covered above;
`decide()` signature changes from `decide(latest, expect)` to `decide(latest, expect, *, price_date=None,
...)`. Existing tests `test_up_to_date_needs_no_fallback` / `test_stale_needs_fallback` /
`test_empty_dates_needs_fallback` must still pass — with `--price-date` defaulting to `None` meaning "don't
check the price lineage", the old behaviour is preserved. **Add new tests for the price-date path.**

---

### ISSUE D — Per-date reasons in the notification, and naming the right artifact

**D1. The root defect is in `notify_audit.py:249-261`** — `format_message`'s `notes` list dedupes by
*text*, so 6 out-of-window dates collapse to one clause and the in-window failures vanish.

**BEFORE** (`scripts/notify_audit.py:249-264`):
```python
    notes: List[str] = []
    for item in remaining:
        reason = _reason(item)
        if reason:
            text = reason
        elif item.get("detail"):
            text = f"{item.get('layer')} {item.get('date')}：{item['detail']}"
        else:
            continue
        if text not in notes:
            notes.append(text)
    if notes:
        lines.append(f"原因：{'；'.join(notes)}")
    if side_line:
        lines.append(side_line)
    lines.append("建议：手动跑 bash a-finder.sh fallback 或检查数据源")
    return title, "\n".join(lines)
```

**AFTER** — group by (layer, reason-class) but **list the dates explicitly**:
```python
    # 原因按「类别」分组，但**日期必须逐个列出**：
    # 2026-10-29 的现场里，6 个超窗日期（10-14~10-21）和 6 个窗内失败日期
    # （10-22~10-29）的 reason 文本不同；按文本去重后只剩一条
    # 「超出 7 天窗口，只报警」，把窗内那 6 天「试过但没修好」的事实整个盖住了。
    notes: List[str] = []
    grouped: Dict[str, List[str]] = {}
    order: List[str] = []
    for item in remaining:
        layer = item.get("layer") or "?"
        date = item.get("date") or "?"
        reason = _reason(item)
        if reason:
            text = reason
        elif item.get("detail"):
            text = f"{layer} {date}：{item['detail']}"
        else:
            continue
        # 分类键 = 层 + 原因骨架（同一原因共用一个 key），
        # 但每一类的日期集合要完整保留。
        key = f"{layer}|{text}"
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(date)
    for key in order:
        layer, text = key.split("|", 1)
        dates = sorted(set(grouped[key]))
        notes.append(f"{layer} {'、'.join(dates)}：{text}")
    if notes:
        lines.append("原因：")
        for n in notes:
            lines.append(f"  - {n}")
```

**D2. `scripts/notify_audit.py:427-468` `_remaining_gaps` must carry a per-date reason that survives.**
`_remaining_gaps` builds `reasons[(layer, date)]` from failed outcomes and skipped items — good, the per-date
mapping already exists. The bug is purely in `format_message`'s rendering. But there is a second, subtler
defect: **a failed outcome covers multiple dates with one reason**, so a 6-day L1 action that failed on its
first command marks all 6 dates with the *same* reason — including dates that may have been fixed by an
earlier command. The docstring at `notify_audit.py:437-439` explicitly acknowledges this ("failed outcome
的 dates 里可能含前半段已修好的日期") and deliberately does not subtract them (correctly — the recheck is
the only ground truth). So the *reason* is legitimately "an action covering these dates failed", and the
fix is to say so **with the dates named**, not to re-derive per-date causality.

To make the reason accurate, `repair_gaps.py` should record *which* command failed. Currently `Outcome.output`
holds the tail of stdout/stderr but `Outcome` has no `failed_command`. Add:
```python
@dataclass
class Outcome:
    action: RepairAction
    ok: bool
    output: str = ""
    failed_command: Optional[str] = None   # 失败的那条命令（shlex.join 之后）
```
and in `run_repairs` (`repair_gaps.py:336-352`), set `failed_command = shlex.join(cmd)` when a command fails.
Then include it in the payload's `outcomes` (`repair_gaps.py:450-455`) and have `_remaining_gaps` prefer it:
```python
        reason = _reason(o) or "修复命令失败"
        failed_cmd = o.get("failed_command")
        if isinstance(failed_cmd, str) and failed_cmd:
            reason = f"{reason}（失败命令：{_short_cmd(failed_cmd)}）"
```
⚠️ The command string contains absolute paths and is long. Add a `_short_cmd()` helper that keeps only the
subcommand and its non-path args (e.g. `sync-hs300-range --start 2026-10-22 --end 2026-10-29 --gap-fill`).

**D3. The "建议" line must name the right artifact.**
**BEFORE** (`notify_audit.py:264`):
```python
    lines.append("建议：手动跑 bash a-finder.sh fallback 或检查数据源")
```
This points at the **local** DB — which is already fine — and away from the **CI cache** DB — which is the
actual broken artifact. There are two distinct failure classes and they need different actions:

**AFTER**:
```python
    # 建议必须点名**正确的产物**：CI 缓存库坏了 → 要重建 CI 缓存库；
    # 线上站点落后 → 要跑本地兜底。这两个是不同的东西，
    # 混成一句「跑 a-finder.sh fallback」会把人指向已经健康的本地库。
    has_price_gap = any((i.get("layer") == "L1") for i in remaining)
    lines.append("建议：")
    if has_price_gap:
        lines.append("  - 行情缺口 → 重建 **CI 缓存库**（Actions Cache 里的 hs300.db）："
                     "先删掉 Actions 缓存条目让下次 run 冷启动，或手动 dispatch daily-audit")
        lines.append("    （`bash a-finder.sh fallback` 修的是**本地库**，本地库正常时跑它没有用）")
    if any(i.get("layer") in ("L2", "L3") for i in remaining):
        lines.append("  - 选股/计划缺口 → CI 缓存库上跑 `python3 scripts/repair_gaps.py --db hs300.db`"
                     "（L2/L3 不需要外部数据源，本地可重算）")
    lines.append("  - 数据源不可达 → 检查东财是否被限流（10-29 只有东财有，腾讯/新浪只到 10-28）")
```

**D4. The no-db path also names the wrong artifact** (`notify_audit.py:198-203`):
```python
    if no_db:
        return (
            "🚨 需人工：巡检无法执行（没有数据库）",
            f"🚨 {today} 巡检无法执行：缓存里没有数据库（hs300.db）\n"
            f"常见原因：早班 deploy-site 从未成功跑过，或 Actions 缓存已过期\n"
            f"建议：手动跑 bash a-finder.sh fallback，或触发一次 deploy-site",
        )
```
Change the suggestion to name the CI cache DB:
```python
            f"建议：触发一次 deploy-site（workflow_dispatch）让 CI 冷启动重建缓存库；\n"
            f"      `bash a-finder.sh fallback` 只修**本地库**，修不了 Actions 缓存里的 hs300.db",
```

**D5. `scripts/repair_gaps.py` `skipped_repairs` must carry the new reason vocabulary.**
After A4's split, `skipped_repairs` must emit **different reasons** for the two classes, otherwise ISSUE D's
fix has nothing to disambiguate:
- zero-row gap outside the window → `"整天无行情（0 只），超出 N 个交易日自动修复窗，只报警"`
- partial-day gap outside the window → `"部分行情缺失（N 只 < 280），超出 7 天自动修复窗（复权口径风险：填补会与既有序列混口径），只报警"`
⇒ **This is the key coupling:** A4 makes the reason classes meaningful, and D makes them visible.

---

### ISSUE E — Guard the cache save so a failed audit never becomes the lineage root

**E1. `daily-audit.yml:177-182` — the save step must be gated on the recheck.**
**BEFORE**:
```yaml
      - name: 保存修复后的 hs300.db 到缓存
        if: always()
        uses: actions/cache@v4
        with:
          path: hs300.db
          key: hs300-db-${{ github.run_id }}
```
**AFTER**:
```yaml
      - name: 保存修复后的 hs300.db 到缓存
        # 只有复检干净（rc=0）才允许成为谱系根。
        # `if: always()` 会让半修好的/修失败的库成为**最新**缓存条目，
        # 而 restore-keys 是前缀匹配取最新 → 失败库会被后续所有 workflow 捡走。
        if: steps.recheck.outputs.recheck_rc == '0'
        uses: actions/cache@v4
        with:
          path: hs300.db
          key: hs300-db-${{ github.run_id }}
```
⚠️ **But** `steps.recheck.outputs.recheck_rc` is empty when the audit found no gaps (the recheck step is
`if: steps.audit.outputs.audit_rc == '1'`). In that case the DB was **not modified** by the repair, and the
restore already gave us a good DB — we still want to save it (or, better, not save it at all, since it's
identical to what we restored). **Three acceptable policies:**
- **E1a (recommended, simplest):**
  `if: steps.audit.outputs.audit_rc != '1' || steps.recheck.outputs.recheck_rc == '0'` — save when there
  were no gaps (nothing was touched, the restored DB is fine) OR when the recheck came back clean.
- **E1b (strictest):** add a dedicated output. In the "no gaps" case, set `recheck_rc=0` explicitly by
  changing the recheck step's `if:` to always run — that costs one extra audit pass per night but removes
  the ambiguity. Then the save can be unconditionally `if: steps.recheck.outputs.recheck_rc == '0'`.
- **E1c (most conservative):** save a degraded DB under a **different key** so it can never be picked up by
  `restore-keys: hs300-db-`:
  ```yaml
          key: degraded-hs300-db-${{ github.run_id }}
  ```
  This preserves the data for forensics while removing it from the good lineage. ⚠️ The key must NOT start
  with `hs300-db-` (a `degraded-hs300-db-*` key does not), otherwise `restore-keys: hs300-db-` still
  matches it.

**Recommendation: E1a + E1c combined** — E1a as the primary gate, and E1c for the degraded case (save to
`degraded-hs300-db-<run_id>`, which the `hs300-db-` prefix will never match, so it is quarantined but
retained).

**E2. `gap-heal.yml:110-115` — same problem, and it is arguably worse.**
`gap-heal.yml` saves unconditionally and its own post-check (`check_gaps.py --days 30 --min-codes 280`) *can*
exit non-zero (lines 85-88 make the step fail) — yet the save at line 110 is `if: always()`. So a gap-heal
run that *failed to heal* publishes the un-healed DB as the newest lineage root. **This workflow is the
"weekly" self-heal and is precisely the thing that should be allowed to publish a good DB, but must not
publish a bad one.**

**BEFORE** (lines 110-115):
```yaml
      - name: Save hs300.db to cache
        if: always()
        uses: actions/cache@v4
        with:
          path: hs300.db
          key: hs300-db-${{ github.run_id }}
```
**AFTER**:
```yaml
      - name: Save hs300.db to cache
        # 补抓后仍有缺口 → 这个库不是谱系根，隔离到不匹配的 key 下
        # （restore-keys: hs300-db- 不会命中 degraded-hs300-db-*）
        if: always() && steps.gaps_after.outcome == 'success'
        uses: actions/cache@v4
        with:
          path: hs300.db
          key: hs300-db-${{ github.run_id }}

      - name: 保存未修好的库（隔离，不进入谱系）
        if: always() && steps.gaps_after.outcome != 'success'
        uses: actions/cache@v4
        with:
          path: hs300.db
          key: degraded-hs300-db-${{ github.run_id }}
```
This requires the "体检（补抓后）" step to get `id: gaps_after` (currently it has no `id`).

**E3. `weekly-evolve.yml:39-43` — the uncontrolled writer.**
**BEFORE**:
```yaml
      - name: Save hs300.db to cache
        uses: actions/cache@v4
        with:
          path: hs300.db
          key: hs300-db-${{ github.run_id }}
```
**AFTER**:
```yaml
      - name: Save hs300.db to cache
        # evolve 只改 evolution_* 表，不改行情 —— 但它会重写整个 hs300.db 文件。
        # 必须带新鲜度校验：恢复到的库若是孤儿谱系，别把孤儿当成新谱系根传播出去。
        if: steps.lineage.outputs.stale != 'true'
        uses: actions/cache@v4
        with:
          path: hs300.db
          key: hs300-db-${{ github.run_id }}
```
with a `lineage` step added after its restore (same `scripts/db_freshness.py` call). ⚠️ `weekly-evolve` has
`permissions: contents: read`; `actions/cache` needs no extra permission. ✅

**E4. `deploy-site.yml:172-177` — the save must reflect the gate outcome, not just `always()`.**
Currently `if: always() && ... && steps.freshness.outputs.skip != 'true'`. With C2, drop the freshness
condition. But should it save when the sync failed? **Yes** — a partially-synced DB is strictly better than
the one we restored (it has *more* data). The important thing is that a **failed** sync isn't mistaken for a
good one. The `数据完整性门禁` step (lines 219-231) already turns a bad DB red. So:
- Keep saving (more data is better).
- **But** the *lineage root* problem is about ordering: `restore-keys` picks the newest. If deploy-site saves
  a bad DB on Monday and daily-audit saves a good DB on Monday night, Tuesday's restore picks the audit's
  (newer) — fine. If the audit's save is skipped (E1), the deploy-site's bad DB remains the newest — also
  fine, because it at least has the sync attempt.
⇒ The real invariant is: **a DB that is strictly worse than the one we restored should never become the newest
entry.** In practice this only arises from the *repair* path (which can corrupt via mixed-basis backfill),
not from the sync path. So E1/E2/E3 cover the real cases.

**E5. A stronger, simpler invariant: make the cache key encode the DB's content date.**
Instead of `hs300-db-${{ github.run_id }}`, use `hs300-db-${{ github.run_id }}-<latest_price_date>`. Then:
- The key is self-describing (a human can see the lineage).
- But it does **not** fix the ordering problem by itself (run_id still dominates). **Not recommended as the
  primary mechanism**; keep it as an optional observability improvement.

---

## 3. New tests

The repo has a strong test culture (668 tests). New tests to add:

### `tests/test_db_freshness.py` (NEW)
Covers `scripts/db_freshness.py` (A1).
- `test_latest_price_date_reads_max_trade_date` — a DB with prices on 10-13 and 10-14 ⇒ `latest == "2026-10-14"`.
- `test_latest_price_date_none_on_empty_table` — `daily_prices` empty ⇒ `None`.
- `test_latest_price_date_none_when_file_missing` — missing file ⇒ `None` (no exception, no file created).
- `test_orphan_db_is_stale` — DB whose max price date is 12 trading days behind ⇒ `stale is True`.
- `test_fresh_db_is_not_stale` — DB at today's date ⇒ `stale is False`.
- `test_lag_is_measured_in_trading_days_not_calendar_days` — a DB whose last price date is 3 **calendar** days
  before a Monday, but only 1 **trading** day (the weekend in between) ⇒ `stale is False`. **This is the
  long-holiday guard.**
- `test_max_lag_is_configurable` — `max_lag=0` makes any non-today DB stale; `max_lag=99` makes a very old DB
  fresh.
- `test_cli_writes_github_output` — `--github-output` writes `latest=`, `lag=`, `stale=`, `orphan=` into the
  supplied `$GITHUB_OUTPUT` file.
- `test_cli_missing_db_is_not_a_crash` — missing DB ⇒ rc=0 with `stale=true` (the workflow treats it as cold
  start), or rc=2 with a clear message. **Pick one and pin it in the test** — recommended: rc=0 +
  `stale=true`, because the workflow's `|| { ... }` fallback already handles rc≠0.

### `tests/test_repair_gaps.py` (EXTEND — existing file, 473 lines)
- `test_zero_row_l1_gap_is_repaired_beyond_seven_day_window` — a report with an L1 gap dated 10-14 (8
  trading days before today 10-29) whose `codes == 0` ⇒ `plan_repairs` produces an L1 action covering it.
  **This is the A4 core assertion.**
- `test_partial_l1_gap_beyond_seven_day_window_is_still_skipped` — an L1 gap dated 10-14 whose `codes == 120`
  ⇒ no action, and `skipped_repairs` contains it. **This pins the safety half.**
- `test_skipped_reason_distinguishes_zero_row_from_partial` — the two skipped reasons differ in text and each
  names its class ("整天无行情" vs "部分行情缺失…复权口径风险").
- `test_gap_codes_survive_report_roundtrip` — `Report.to_dict()` → `Report.from_dict()` preserves `codes`
  (guards the new field against the `Gap(**g)` footgun the docstring warns about).
- `test_audit_report_includes_codes_for_l1_gaps` — `audit_coverage.audit()` on a synthetic DB produces
  `codes` in the L1 gap dicts.
- `test_failed_outcome_records_failed_command` — `run_repairs` with a runner that fails on the 2nd command ⇒
  the outcome's `failed_command` is set and names the command.
- `test_horizon_uses_trading_days` — verify `_horizon` skips weekends/holidays.

### `tests/test_notify_audit.py` (EXTEND — existing file, 1183 lines)
- `test_manual_message_lists_dates_per_reason_class` — a `remaining` set with two L1 dates sharing the "超窗"
  reason and one L1 date with a "修复失败" reason ⇒ the body contains **three** dates, and the "超窗" clause
  names **both** dates it applies to. **This is the ISSUE D core assertion** (the current code would show only
  one date per reason class).
- `test_manual_message_does_not_hide_failed_dates_behind_skipped_reason` — the exact 2026-10-29 shape: 6
  skipped L1 dates (10-14..10-21) + 6 failed L1 dates (10-22..10-29) ⇒ the body mentions `2026-10-22` (or
  `2026-10-26`) with the failure reason, and the "原因" section has ≥ 2 distinct classes. **This reproduces
  the actual incident.**
- `test_manual_message_suggests_ci_cache_not_local_fallback_for_price_gaps` — an L1 gap ⇒ the suggestion names
  the CI cache / `daily-audit` / `deploy-site`, and explicitly states that `a-finder.sh fallback` fixes the
  *local* DB. (Assert the string "CI 缓存库" is present and "fallback" is present but disclaimed.)
- `test_manual_message_suggests_repair_gaps_for_l2_l3` — an L2/L3 gap ⇒ the suggestion names
  `scripts/repair_gaps.py`.
- `test_no_db_message_names_ci_cache` — the no-db body names the CI cache and disclaims
  `a-finder.sh fallback` as a fix for it.
- `test_failed_command_appears_in_reason` — an outcome with `failed_command` ⇒ the reason line contains the
  command.

### `tests/test_daily_audit_workflow.py` (EXTEND — existing file, 159 lines)
This file already has the `_step_block` / `_run_script` / `bash -n` helpers — **reuse them exactly.**
- `test_cache_save_is_not_always` — the "保存修复后的 hs300.db 到缓存" step's `if:` must **not** be bare
  `always()`; it must reference `recheck_rc`.
- `test_cache_save_requires_clean_recheck` — the `if:` line contains `recheck_rc == '0'`.
- `test_lineage_freshness_step_exists_before_sync` — a step named "缓存库谱系" / "新鲜度" exists and calls
  `scripts/db_freshness.py`.
- `test_restore_is_not_gated_on_freshness` — **the restore step must NOT carry
  `steps.freshness.outputs.skip != 'true'`**, because freshness now depends on the restored DB. (This is the
  C1 ordering assertion — a circular-dependency regression guard.)
- `test_freshness_step_reads_local_db_not_site` — the freshness step must NOT contain
  `ycu.space/data/dates.json`. **This is the ISSUE C core assertion** — it is the guard against re-introducing
  the site-as-oracle bug.
- `test_deploy_step_still_gated_on_export` — the gh-pages deploy step keeps
  `steps.export.outcome == 'success'`.

### `tests/test_deploy_site_workflow.py` (NEW — mirrors `test_daily_audit_workflow.py`)
There is currently **no** test file for `deploy-site.yml` (only `test_daily_audit_workflow.py` exists).
Given that `deploy-site.yml` is where the C/B changes land, it needs its own structural regression file.
Copy the helper block from `test_daily_audit_workflow.py` (or move it to a shared
`tests/_workflow_helpers.py`).
- `test_freshness_does_not_query_live_site` — no `ycu.space` in the freshness step.
- `test_restore_step_precedes_freshness_step` — the restore step's line index < the freshness step's line
  index. (Ordering guard for C1.)
- `test_cache_save_is_not_gated_on_freshness_skip` — the save step's `if:` must not contain
  `steps.freshness.outputs.skip`.
- `test_lineage_freshness_guard_present` — `scripts/db_freshness.py` is called after the restore.
- `test_cold_start_flag_is_consumed_by_sync_step` — the sync step references
  `steps.lineage.outputs.cold_start`.
- `test_export_is_gated_on_verify_site_export` — the export step calls `scripts/verify_site_export.py`.
- `test_deploy_requires_successful_export` — deploy is gated on `steps.export.outcome == 'success'`.

### `tests/test_needs_fallback.py` (EXTEND — existing file)
- `test_price_date_behind_makes_fallback_needed` — site date is current but the local price date is behind ⇒
  still needs fallback.
- `test_both_current_needs_no_fallback` — both current ⇒ no fallback.
- `test_min_wait_hours_defers_fallback` — site stale for less than the grace ⇒ no fallback yet.
- `test_min_wait_hours_expired_allows_fallback` — stale beyond the grace ⇒ fallback.

### `tests/test_verify_site_export.py` (EXTEND)
- `test_degraded_site_with_one_date_is_rejected_under_stricter_min_dates` — pins the new default. **See the
  risk section — I recommend adding `--expect-latest` / `--not-older-than` rather than raising
  `--min-dates`.**

### `tests/test_source_basis.py` (NEW — for A5/F2)
- `test_tencent_is_tagged_qfq_and_others_none` — the basis registry is correct.
- `test_smart_fetch_prefers_none_basis_source` — when Eastmoney is unavailable and Tencent is, the smart
  fetch **reports** the basis it used (and, under the strict policy, refuses to write it to `daily_prices`).
- `test_adj_basis_column_exists_after_migration` — the migration adds the column to an existing DB.

### `tests/test_cache_lineage_workflow_contract.py` (NEW, optional but recommended)
A cross-workflow contract test asserting all four workflows use the same cache key template and the same
restore-keys:
- `test_all_workflows_use_run_id_key` — every `actions/cache` block with `path: hs300.db` has
  `key: hs300-db-${{ github.run_id }}` (or the documented degraded variant).
- `test_all_workflows_have_lineage_guard` — every workflow that restores `hs300.db` has a `db_freshness.py`
  guard step.
- `test_no_workflow_uses_fixed_cache_key` — no bare `key: hs300-db`.

---

## 4. Risk assessment

### R1. **Could a fix publish an EMPTY or partial site?** — This is the highest-severity risk.

**Current protections:** `scripts/verify_site_export.py` runs in both `deploy-site.yml:170` and
`daily-audit.yml:166`, and the gh-pages deploy steps are gated on `steps.export.outcome == 'success'`
(`deploy-site.yml:181`, `daily-audit.yml:169`). ✅ Good — the 2026-10-27 empty-export incident cannot recur
*as an empty export*.

**But the gate is weak.** `verify_site_export.check` defaults are `min_dates=1`, `min_json=10`. A site with
**one** date and ten JSONs passes. Consider the realistic failure: the CI DB is the 10-13 orphan;
`daily_picks` in it has dates only up to 10-13; `export_json.py` emits `dates.json` = `["2026-10-13"]` +
~10 JSONs ⇒ **the verify gate PASSES**, and `daily-audit.yml` then force-pushes a site whose latest date is
**10-13** — i.e. **the CI would overwrite the healthy local-published site (10-29) with a 12-day-stale site.**
That is a *worse* incident than the empty site: the site looks alive but is 16 days stale, and
`needs_fallback.py` would then say "线上已是最新" for 10-13 and never trigger the local rescue.

⚠️ **This is a live hazard today**, independent of my changes: the next `daily-audit` run (with the orphan CI
DB) satisfies `recheck_rc == '0'` only if the gaps are repaired — but once A2's cold-start rebuild lands, the
export path opens. **Mitigation is mandatory, not optional:**

- **R1a** Add `scripts/verify_site_export.py --expect-latest <date>`: the export must contain a date ≥ the
  expected trade date, or the deploy is blocked.
- **R1b** Add a **monotonicity guard**: the deploy step must not publish a site whose `dates.json[0]` is
  **older** than the currently-live site's `dates.json[0]`. This is the single most valuable guard: it makes
  "CI publishes an older site than the local leg already did" impossible. Implement as
  `scripts/verify_site_export.py --site site --not-older-than <live-date>` (the live date is fetched with
  `curl` exactly as the freshness step does today).
- **R1c** In `daily-audit.yml`, only deploy when the audit was **clean** (`recheck_rc == '0'`) *and* the export
  is verified *and* the export's latest date ≥ the live site's latest date.

**R1d** Every change in this plan must be checked against: "does this ever cause a `gh-pages` force-push whose
`dates.json[0]` is older than the current live one?" — Answer: **the C2 change (cache save decoupled from
freshness) does not** (it doesn't touch deploy gating), and **the E1 change (save gate) does not either**. The
dangerous one would be enabling the `daily-audit` deploy path more often. ⇒ **R1b's monotonicity guard is the
single most important addition in this entire plan.** It converts every "CI publishes something older" scenario
from a silent regression into a blocked step.

### R2. The A5 basis-policy change could make the sync fetch **nothing**.
If Eastmoney is down and we refuse to write Tencent qfq rows, the sync returns 0 rows ⇒ the gate turns red ⇒
the site doesn't update. **But that is the *correct* behaviour**: silently writing qfq prices into a 不复权
table corrupts the series (fake gaps on every ex-dividend date), which then corrupts MA signals, signals, and
plans. Recommendation: **ship the basis *recording* first (migration + column), and the basis *enforcement*
only after a `--allow-basis-mismatch` escape hatch exists and the column has been populated for a full
period.** Order: A5-2 (migration + write) → A5-1 (tagging + logging) → A5-1-strict (enforcement).

### R3. The A4 widened L1 window increases the blast radius of a bad fetch.
Filling 60 days of zero-row gaps in one `sync-hs300-range` call is a large request burst ⇒ higher chance of
hitting rate limits ⇒ more likely to fall back to a lower-tier source. **Mitigation:** the widened window
applies **only** to gaps with **0 rows** (no existing series to corrupt), and the sync command should use a
**bounded, explicit date range** (min..max of the zero-row dates) rather than the whole window. Additionally,
`repair_gaps.py`'s L1 action should **verify after fetching** that the target dates now have ≥ 280 codes, and
report a failed action (not a silent success) if not — the current code trusts the command's exit code, and
`sync-hs300-range` exits 0 even when it writes 0 rows. **This is exactly the "ok outcome ≠ repaired" hazard
that `notify_audit.py` documents but cannot detect from the repair JSON alone.** ⇒ Add a post-fetch count to
the repair outcome.

### R4. The E1 cache-save gate could stall the lineage.
If `daily-audit` never saves (because its recheck is never clean), the lineage root never advances ⇒ every
workflow keeps restoring an old DB ⇒ the freshness guard (B3) eventually declares it stale ⇒ cold start every
night ⇒ 400-day refetch every night ⇒ **rate-limit burn**. **Mitigation:** the cold-start path is bounded (it
runs `sync-hs300-range` with `--gap-fill`, which only fetches *missing* segments) and the freshness guard's
`max_lag` should be generous enough (3 trading days) that a single bad night does not trigger a rebuild.
Also, `deploy-site.yml` saves on every successful shift, so the lineage advances from there even if the audit
never saves.

### R5. The C2 change (cache save always runs) could publish a **worse** DB as the newest.
The save runs even when the sync failed. But a failed sync leaves the DB **unchanged or partially improved** —
never worse. The genuinely dangerous case is the *repair* path (mixed-basis backfill), covered by E1/E2/E3.
⇒ **R5 is acceptable**, provided R1b's monotonicity guard is in place.

### R6. The C3 grace period delays the site.
With `--min-wait-hours 8`, the local fallback publishes ~01:00 Beijing instead of 17:10. **Mitigation:** make
it configurable, and land C1/C2 first (after which `--min-wait-hours 0` is safe). Recommend shipping with the
grace ON, then relaxing it once the CI freshness oracle is fixed.

### R7. The `daily-audit` cold-start path (A3) is expensive and could time out.
A 400-day cold sync is the heaviest operation in the pipeline. `repair_gaps.py`'s `COMMAND_TIMEOUT_S = 1800`
(30 min) would likely fire. **Mitigation:** the cold-start sync runs as a **workflow step** (not through
`repair_gaps.py`), so it is not subject to `COMMAND_TIMEOUT_S`; the workflow-level job timeout is the limit.
Also: the cold start should only trigger when the guard actually detects an orphan, not on every run.

### R8. `audit_coverage.Gap` gaining a field could break other consumers.
`Gap.from_dict` is already the tolerant path (`audit_coverage.py:53-57` reads only known keys) — **good**. But
`Report.to_dict()` output is consumed by `notify_audit.py` (`_items` + `_has_date_layer`) and by
`repair_gaps.py` (`_load_report`). Adding a key to `to_dict()` is additive and safe; `notify_audit`'s
`_has_date_layer` only checks `date`/`layer`. ✅ Low risk. **But** `Gap` is a frozen dataclass with
`date, layer, detail` positionally constructed in several places (`audit_coverage.py:153,156,160,165,172`) —
adding a field with a default at the end is safe, but **positional** constructions like `Gap(d, "L1", "...")`
must keep working. Use `codes: Optional[int] = None` as the 4th field with a default. ⚠️
`tests/test_repair_gaps.py` constructs `audit_mod.Gap(*g)` with 3-tuples ⇒ still works. ✅

### R9. Raising `verify_site_export`'s `--min-dates` default could block legitimate deployments.
If the DB legitimately has < 60 pick dates (a fresh cold-start DB would have ~400 days of prices but possibly
few pick dates), a strict `--min-dates 60` would block every deploy. **Mitigation:** make the default
**relative** (e.g. `--min-dates 1` by default, but the **workflows** pass an explicit `--min-dates 30` and
`--not-older-than <live>`), or better, tie the threshold to the DB's own `MAX(date)` rather than a count.
**Recommended:** implement `--expect-latest` + `--not-older-than` and leave `--min-dates` alone; the
monotonicity guard is what actually protects the site.

### R10. `weekly-evolve.yml` is currently a lineage-root writer we may forget.
⇒ The cross-workflow contract test (`tests/test_cache_lineage_workflow_contract.py`) is the mitigation: it
fails if any workflow that touches `hs300.db` lacks the lineage guard. **Without that test, E3 will regress
the first time someone adds a fifth workflow.**

### R11. Deleting the `hs300.db` cache entry via the UI frees quota but is irreversible.
Acceptable: the entry is a 10-13 orphan and the cold-start rebuild supersedes it. If the operator wants a
safety net, `gap-heal.yml` already uploads a 90-day artifact backup — **but check what that artifact actually
contains** before relying on it (it may be the local-lineage DB).

---

## 5. Ordered implementation sequence

### Phase 0 — Manual, one-time, do FIRST (before any code lands)
**M0.1** GitHub UI → Actions → Caches → **delete the `hs300-db` orphan entry** (the 2026-10-26 fixed-key
one). *Rationale:* it is unreachable garbage; deleting it now stops it from ever being mistaken for a lineage
root and frees quota. *No code depends on this.*
**M0.2** Record the current live `dates.json[0]` (= `2026-10-29`) as the **monotonicity floor** for R1b.
*Rationale:* the guard must not publish anything older than what is live right now.
**M0.3** Verify whether `GITHUB_DISPATCH_TOKEN` is configured (the Cloudflare Workers Cron leg).
*Rationale:* if it is not, the "CI is dead" determination in C3 rests on GitHub schedule alone, which has a
documented 7-hour delay and whole-day misses ⇒ the grace period in C3 must be ≥ 8 hours.
**M0.4** (Optional, recommended) Export the local `hs300.db` as a one-off artifact for forensics.
*Rationale:* it is the reference implementation of the correct state; if a CI rebuild diverges, this is the
ground truth. **Do NOT wire it into the CI as an import source** (that is option (ii), which this plan
rejects).

### Phase 1 — Safety net first (independently shippable, no behaviour change)
**1.1** `scripts/db_freshness.py` (new) + `tests/test_db_freshness.py` (new). *Pure addition, no workflow
changes yet.* **Do this first** because every later phase uses it.
**1.2** `scripts/verify_site_export.py`: add `--expect-latest <date>` and `--not-older-than <live-date>`; keep
the existing defaults unchanged (R9). + `tests/test_verify_site_export.py` additions.
**1.3** Wire `--not-older-than` into `deploy-site.yml`'s export step and `daily-audit.yml`'s export step.
**This is the single most valuable change in the plan** (R1b) and is independently shippable.

### Phase 2 — Cache lineage integrity (A1/A2/B2/B3/E1/E2/E3)
**2.1** Add the lineage guard step to **`gap-heal.yml`** (E2) — it is the most dangerous writer (unconditional
save + can fail its own check). Needs `id: gaps_after` on the post-check step.
**2.2** Add the lineage guard to **`weekly-evolve.yml`** (E3) — currently the only writer with *no* guard at all.
**2.3** Add the lineage guard to **`daily-audit.yml`** (A2/B3) + the cold-start sync step (A3) + the `E1` save
gate + the `E1c` degraded-quarantine save.
**2.4** Add the lineage guard to **`deploy-site.yml`** (B3) + wire `cold_start` into the sync step's `COLD`
detection (A2's sync-side half).
**2.5** Add the cross-workflow contract test (`tests/test_cache_lineage_workflow_contract.py`) — **this is what
keeps 2.1–2.4 from regressing**.
*These are all independent of Phase 3 and of each other, but each touches a different workflow ⇒ no write
conflicts ⇒ can be parallelised.*

### Phase 3 — The freshness oracle (ISSUE C) — **MUST land after Phase 2**
**3.1** `deploy-site.yml`: move the restore above freshness; rewrite freshness to read the CI DB (C1); split
`skip` from `skip_cache_save` (C2); drop the freshness condition from the cache save.
**3.2** `a-finder.sh` `cmd_fallback` + `scripts/needs_fallback.py`: add `--price-date` and `--min-wait-hours`
(C3/C4) + `tests/test_needs_fallback.py` additions.
**3.3** Re-verify the end-to-end ordering: local publish → CI freshness reads the CI DB → CI still syncs → CI
saves → CI deploys. **The local publish must no longer be able to skip the CI's cache save.**
⚠️ **Phase 3 must not land before Phase 2**, because Phase 3's C1 depends on the restore already having the
lineage guard; and C3's grace period is the *stopgap* that makes Phase 3 safe to land later.

### Phase 4 — Repair semantics + notification (A4/D)
**4.1** `audit_coverage.py`: add `Gap.codes` + populate it in `audit()` + `to_dict`/`from_dict`. + test.
**4.2** `repair_gaps.py`: split the L1 window by zero-row vs partial (A4); make `_horizon` trading-day based;
update `skipped_repairs` to emit **two distinct reason classes**; add `Outcome.failed_command`. +
`tests/test_repair_gaps.py` additions.
**4.3** `notify_audit.py`: per-date reasons with the dates named (D1); include `failed_command` (D2); rewrite
the "建议" line to name the CI cache vs the local DB (D3/D4); update the no-db message (D4). +
`tests/test_notify_audit.py` additions.
**4.4** `daily-audit.yml`: pass `--max-price-days` / the new zero-row cap explicitly if the workflow wants to
pin it (recommend: let the script default apply; the workflow should not re-specify policy).
*Phase 4 is independent of Phases 2/3 and can run in parallel with them.*

### Phase 5 — Source basis (A5) — **last, because it is the riskiest**
**5.1** Migration `db/migrations/2026_10_29_daily_prices_adj_basis.sql` + `db_repository.py` writes `adj_basis`
+ `PriceRow.adj_basis` default `"none"`. + test.
**5.2** `data_providers.py`: the `SOURCE_BASIS` registry + basis tagging in `fetch_daily_kline_smart` +
**logging only** (no enforcement). + `tests/test_source_basis.py`.
**5.3** (Later, after 5.1/5.2 have been live for ≥ 1 month and the column is populated) — enforcement: prefer
`none`-basis sources for `daily_prices`, with an `--allow-basis-mismatch` escape hatch.

### Phase 6 — Documentation
**6.1** Update `CLAUDE.md`: the "缓存 key 必须永远带 run_id" bullet → add the orphan case + the
freshness-guard rule; the "⚠️ 但「线上站点由 CI 发布」是错的" bullet → update once CI is authoritative again;
add the basis warning (F2) to the 数据源 section; add the "10-29 只存在于东财" fact (already present) + the
mechanism.
**6.2** Append the new failure mode to `.learnings/ERRORS.md` (the cache-lineage entry at line 314 should gain
the "restore-keys 也会捡到过老条目" corollary).

### Dependencies summary
```
M0.1 ─┐
M0.2 ─┼─→ 1.2 ─→ 1.3 ──────────────────────┐ (R1b — independent, ship early)
M0.3 ─┘                                     │
1.1 ──→ 2.1, 2.2, 2.3, 2.4 ──→ 2.5         │
                │                           │
                └──→ 3.1 ──→ 3.2 ──→ 3.3   │
                                          │
4.1 ──→ 4.2 ──→ 4.3 ──→ 4.4  (parallel)   │
                                          │
5.1 ──→ 5.2 ──→ 5.3 (later)                │
                                          ▼
                                    6.1, 6.2
```
**Critical ordering constraints:**
1. **1.3 (monotonicity guard) before any change that opens the deploy path** — it is the guard that makes the
   rest safe.
2. **Phase 2 before Phase 3** — C1 depends on the lineage guard existing.
3. **Phase 3 before relaxing C3's grace period to 0** — while the site is still the oracle, the grace period is
   mandatory.
4. **Phase 5.3 last** — enforcement without recorded basis would silently drop data.

### Effort estimate
| Phase | Files | New tests | Risk |
|---|---|---|---|
| 1 | 2 scripts + 2 workflows | 1 new + 1 extended | Low |
| 2 | 4 workflows | 1 new + 1 extended | Low |
| 3 | 1 workflow + 1 script + a-finder.sh | 1 extended | **Medium** (freshness reordering) |
| 4 | 3 scripts | 3 extended | Low |
| 5 | 1 migration + 2 modules + db_repository | 1 new | **High** (5.3 only) |

---

## 6. Recommendation summary

1. **ISSUE C: hybrid** — CI owns the price lineage (per `CLAUDE.md`); the local DB owns the *site* only as a
   rescue when CI is provably dead. The essential change is **making the CI's freshness oracle read the CI's
   own DB instead of the live site** (`deploy-site.yml:82-83`). Everything else in Phase 3 follows from that.
2. **ISSUE A: staleness detector + cold-start rebuild (option iii) as primary; zero-row-aware widened window
   (option i) as secondary; reject option (ii)** (importing the local DB) — it reintroduces the two-lineage
   problem and contradicts `CLAUDE.md`.
3. **ISSUE B: delete the orphan via the UI (B1) — it is unreachable and worthless; and the "restore-keys picks
   an ancient entry" risk is REAL** because `weekly-evolve.yml` and `gap-heal.yml` both save unconditionally ⇒
   the explicit post-restore freshness check (B3) is mandatory in all four workflows, not just one.
4. **ISSUE D: the fix is in `format_message`'s rendering, not in `_remaining_gaps`** — the per-date reason
   mapping already exists at `notify_audit.py:441-455`; it is the text-level dedup at lines 249-259 that
   destroys it. Fix = group by reason class but **name every date**, and rewrite the "建议" line to name the
   CI cache (not the local DB).
5. **ISSUE E: gate `daily-audit`'s save on `recheck_rc == '0'`** and quarantine degraded DBs under a key that
   `restore-keys: hs300-db-` can never match. **Do not forget `gap-heal.yml` (E2) and `weekly-evolve.yml`
   (E3)** — they are uncontrolled lineage-root writers that the brief did not mention.
6. **The single most important addition, not in the brief: a monotonicity guard (`--not-older-than`) so no
   workflow can ever force-push a site older than the live one.** Without it, the repaired CI DB (10-13 orphan →
   repaired to something) could silently overwrite the healthy local-published 10-29 site with a 16-day-stale
   one — and `needs_fallback.py` would then report "线上已是最新" and never rescue it. That is a worse incident
   than the empty-site incident of 2026-10-27.

---

*No source files were modified. Baseline test suite: 668 passed.*
