# 10w 本金决策体系报告 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 基于平台 2 年回测数据，产出一份《10w 本金决策体系报告》+ 回测原始数据 JSON，给出选股/仓位（8w股+2w债）/退出/复盘的可执行 SOP，胜率≥40% 为筛选线。

**Architecture:** 分两条数据线——(1) 均线策略用 `ma_backtest.py` 的 walk-forward + top 3/5/8 对比；(2) 其余四个榜单用 `daily_picks.buy` 价 vs `daily_prices.close` 自算"持仓 N 日笔胜率"。两者汇入 `baseline.json`，再写成 7 章报告。

**Tech Stack:** Python 3.11, SQLite, ma_backtest.py（已有）

**Spec:** `docs/superpowers/specs/2026-10-25-10w-decision-system-design.md`

---

### Task 1: 分析脚本 — 各榜单×周期笔胜率

**Files:**
- Create: `docs/reports/10w_backtest.py`（报告附属分析脚本，非平台代码）

- [ ] **Step 1: 写分析脚本**

创建 `docs/reports/10w_backtest.py`：

```python
"""10w 决策体系报告的分析脚本（可复现）。

计算五个榜单（均线/买入信号/信号策略/多因子/高胜率）在
不同持仓周期（5/20/60 交易日）下的「笔胜率 + 平均收益」，
从 daily_picks.buy 价出发，查 daily_prices 后续收盘价。
结果写 docs/reports/data/10w-backtest-baseline.json。

用法: python3 docs/reports/10w_backtest.py [db_path]
"""
import json
import sqlite3
import sys
from pathlib import Path

DB = sys.argv[1] if len(sys.argv) > 1 else "hs300.db"
HOLD_DAYS = [5, 20, 60]  # 短线/波段/中线
KINDS = ["均线", "买入信号", "信号策略", "多因子", "高胜率"]
OUT = Path(__file__).parent / "data" / "10w-backtest-baseline.json"


def load_prices(conn):
    """code -> 按 trade_date 升序的 [(date, close)] 列表。"""
    out = {}
    for code, d, close in conn.execute(
        "SELECT code, trade_date, close FROM daily_prices ORDER BY code, trade_date"
    ):
        out.setdefault(code, []).append((d, close))
    return out


def forward_close(prices, date, n_days):
    """给定 code 的价格序列，返回 date 之后第 n_days 个交易日的收盘价。

    找不到（上市不久 / 已停牌 / 数据不足）返回 None。
    """
    closes_after = [c for (d, c) in prices if d > date]
    if len(closes_after) < n_days:
        return None
    return closes_after[n_days - 1]


def compute(conn):
    prices = load_prices(conn)
    picks = [
        (r[0], r[1], r[2], r[3])
        for r in conn.execute(
            "SELECT kind, code, date, buy FROM daily_picks"
        )
    ]
    results = {}
    for kind in KINDS:
        results[kind] = {}
        for n in HOLD_DAYS:
            rets = []
            for k, code, date, buy in picks:
                if k != kind:
                    continue
                px = prices.get(code)
                if not px or not buy:
                    continue
                exit_close = forward_close(px, date, n)
                if exit_close is None:
                    continue
                rets.append(exit_close / buy - 1.0)
            if not rets:
                results[kind][n] = {"n": 0, "win_rate": None, "avg_ret": None}
                continue
            wins = sum(1 for r in rets if r > 0)
            results[kind][n] = {
                "n": len(rets),
                "win_rate": round(wins / len(rets) * 100, 1),
                "avg_ret": round(sum(rets) / len(rets) * 100, 2),
            }
    return results


def main():
    conn = sqlite3.connect(DB)
    try:
        baseline = compute(conn)
    finally:
        conn.close()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"写成 {OUT}")
    for kind, table in baseline.items():
        line = "  ".join(
            f"{n}日:胜率{table[n]['win_rate']}%/均{table[n]['avg_ret']}%/样本{table[n]['n']}"
            for n in HOLD_DAYS
        )
        print(f"{kind:6} {line}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 跑脚本**

Run:
```bash
cd /Users/shichaopeng/Work/self-dir/projects/a-finder-cli
python3 docs/reports/10w_backtest.py hs300.db
```

Expected: 输出 `docs/reports/data/10w-backtest-baseline.json` + 五个榜单 × 三周期的胜率/均值表格

- [ ] **Step 3: 检查输出合理性**

Run:
```bash
python3 -c "
import json
d = json.load(open('docs/reports/data/10w-backtest-baseline.json'))
for kind in d:
    print(kind, {k: v['win_rate'] for k, v in d[kind].items()})
"
```

Expected: 每个榜单每个周期有样本数 > 0 或如实标注 n=0（样本不足）

- [ ] **Step 4: Commit**

```bash
git add docs/reports/10w_backtest.py docs/reports/data/10w-backtest-baseline.json
git commit -m "report(10w): 分析脚本 + 各榜单×周期笔胜率基线"
```

---

### Task 2: 均线策略 walk-forward + 仓位对比

**Files:**
- Create: `docs/reports/data/10w-ma-walkforward.json`（均线回测结果）
- （均线走 ma_backtest.py 现成命令，不写新代码）

- [ ] **Step 1: 跑 walk-forward（均线，默认 top=10）**

Run:
```bash
cd /Users/shichaopeng/Work/self-dir/projects/a-finder-cli
python3 ma_backtest.py --db hs300.db --days 240 --walk-forward 2>&1 | tail -40
```

Expected: 输出 walk-forward 训练/验证段的总收益、超额收益、win_rate 等

- [ ] **Step 2: 跑 top 3 / 5 / 8 仓位对比**

Run:
```bash
cd /Users/shichaopeng/Work/self-dir/projects/a-finder-cli
for top in 3 5 8; do
  echo "===== top=$top ====="
  python3 ma_backtest.py --db hs300.db --days 240 --top $top 2>&1 | grep -iE "总收益|win|return|回撤|excess" | head -8
done
```

Expected: 三组数字，top 越大通常回撤越小、单票越分散

- [ ] **Step 3: 记录结果到 JSON**

把 walk-forward 与 top 3/5/8 的关键数字手工汇总到 `docs/reports/data/10w-ma-walkforward.json`：

```json
{
  "walk_forward": {
    "total_return": 0.0,
    "excess_return": 0.0,
    "win_rate": 0.0
  },
  "top_comparison": {
    "3": {"total_return": 0.0, "win_rate": 0.0},
    "5": {"total_return": 0.0, "win_rate": 0.0},
    "8": {"total_return": 0.0, "win_rate": 0.0}
  }
}
```

（用 Step 1/2 实际输出替换 0.0 占位；win_rate 是 ma_backtest 的「盈利日/持仓日」日胜率，报告里须注明口径。）

- [ ] **Step 4: Commit**

```bash
git add docs/reports/data/10w-ma-walkforward.json
git commit -m "report(10w): 均线 walk-forward + top 3/5/8 回测结果"
```

---

### Task 3: 写主报告（7 章）

**Files:**
- Create: `docs/reports/10w-decision-system.md`

- [ ] **Step 1: 写报告骨架 + 第 1-2 章**

创建 `docs/reports/10w-decision-system.md`：

```markdown
# 10w 本金决策体系报告

> 生成日期：2026-10-25
> 数据区间：daily_prices 2024-10-08 ~ 2026-10-23（497 交易日）
> **重要声明**：本报告基于 paper trading 回测，无真实资金战绩；历史回测 ≠ 未来保证。

## 1. 数据与方法

- 回测数据：daily_prices 2 年日线
- 口径：滑点 0.1%、单票上限 15%、RR=2.0、ATR 止损 2x
- 胜率定义：持仓 N 交易日后 exit_close / buy - 1 > 0 的比例（笔胜率）
- 均线策略额外用 ma_backtest.py walk-forward 交叉验证
- 筛选线：胜率 ≥40% 且平均收益为正

## 2. 策略回测基线

| 榜单 | 5日胜率 | 20日胜率 | 60日胜率 | 结论 |
|------|--------|--------|--------|------|
| 均线 | ... | ... | ... | ... |
| 买入信号 | ... | ... | ... | ... |
| 信号策略 | ... | ... | ... | ... |
| 多因子 | ... | ... | ... | ... |
| 高胜率 | ... | ... | ... | ... |

（数据来自 data/10w-backtest-baseline.json）
```

- [ ] **Step 2: 填入回测数字**

用 `docs/reports/data/10w-backtest-baseline.json` 和 `10w-ma-walkforward.json` 的实际数字替换第 2 章的 "..."，并给每个策略写一句结论（胜率是否 ≥40%、哪个周期最优）。

- [ ] **Step 3: 写第 3-7 章**

按 spec §3.2 结构，补全：

```markdown
## 3. 选股规则
（回测选出的 1-2 个主力榜单 + top N + 是否策略共振）

## 4. 仓位规则（10w = 8w 股 + 2w 债）
（股票买几只/单票%/留现金 + 转债几只/单只上限 + 3/5/8 只对比结论）

## 5. 退出规则
（止损 ATR 2x 或固定%、止盈 RR 2.0、移动/分批止盈，对应 plan 的 stop_price/tp_price）

## 6. 复盘节奏
（每周看 /api/stats、每月跑 evolve、调参触发条件）

## 7. 固化清单
（哪些规则 → config.py，哪些 → 下一步 10w 决策面板 spec）
```

每章结论先行 + 数据证据，胜率≥40% 是硬筛选线。

- [ ] **Step 4: 校验报告数据一致性**

Run:
```bash
cd /Users/shichaopeng/Work/self-dir/projects/a-finder-cli
python3 -c "
import json
b = json.load(open('docs/reports/data/10w-backtest-baseline.json'))
# 报告里每个数字应能在 baseline 里找到
for kind, table in b.items():
    for n, v in table.items():
        assert isinstance(v['win_rate'], (int, float)) or v['win_rate'] is None
        assert isinstance(v['n'], int)
print('baseline JSON 结构 OK')
"
```

Expected: `baseline JSON 结构 OK`

- [ ] **Step 5: Commit**

```bash
git add docs/reports/10w-decision-system.md
git commit -m "report(10w): 10w 本金决策体系报告（7 章）"
```

---

### Task 4: 交叉校验 + 终审

**Files:** None（校验）

- [ ] **Step 1: 交叉校验 — 与 /api/stats 对照**

Run:
```bash
cd /Users/shichaopeng/Work/self-dir/projects/a-finder-cli
python3 -c "
from app import create_app
c = create_app().test_client()
d = c.get('/api/stats').get_json()
print('策略胜率:', d['strategies'])
print('月度胜率:', d['monthly'][:3] if d['monthly'] else '空')
"
```

Expected: 若 strategies 为空（无标注结果），在报告第 1 章已声明"无真实标注，回测为准"；若非空，与报告第 2 章对照，不一致处如实标注。

- [ ] **Step 2: 确认报告无占位符**

Run:
```bash
cd /Users/shichaopeng/Work/self-dir/projects/a-finder-cli
grep -n "\.\.\.\|TBD\|TODO\|待补\|占位" docs/reports/10w-decision-system.md || echo "无占位符 ✓"
```

Expected: `无占位符 ✓`

- [ ] **Step 3: 全量测试无回归**

Run:
```bash
cd /Users/shichaopeng/Work/self-dir/projects/a-finder-cli
python3 -m pytest tests/ -q 2>&1 | tail -2
```

Expected: PASS（报告不改代码，应无回归）

- [ ] **Step 4: Commit + push**

```bash
git add -A
git commit -m "report(10w): 交叉校验完成" 2>&1 | tail -1
git push origin main 2>&1 | tail -2
```

---

## Self-Review

**Spec coverage:**
- §3.1 回测方法学（walk-forward / 5 榜单 / 3 周期 / 3 仓位 / 筛选线）→ Task 1（笔胜率）+ Task 2（均线 walk-forward + top 对比）
- §3.2 章节结构 7 章 → Task 3
- §3.3 产出物（主报告 + baseline.json）→ Task 1 + Task 3
- §3.3 验证（可复现/可复核/交叉校验）→ Task 1（脚本可复现）+ Task 4（交叉校验）
- §二 现状（paper trading 声明）→ Task 3 第 1 章
- §五 风险（过拟合/转债定性/样本局限）→ Task 2（walk-forward）+ Task 3（声明）

**Placeholder scan:** 报告第 2 章表格的 "..." 是"用实际回测数字替换"的明确指令，不是永久占位符；Task 2 的 0.0 占位明确要求用实际输出替换。

**Type consistency:**
- `KINDS = ["均线", "买入信号", "信号策略", "多因子", "高胜率"]` — 与 daily_picks 实际 kind 值一致（已验证）
- `HOLD_DAYS = [5, 20, 60]` — 对应 spec 的短线/波段/中线
- `daily_picks` 字段 `kind/code/date/buy` — 与实际 schema 一致（无 ret_pct，已确认）
- `daily_prices` 字段 `close` — 与实际 schema 一致
- baseline JSON 字段 `n/win_rate/avg_ret` — Task 1 定义，Task 3/4 引用一致
