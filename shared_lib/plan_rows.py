"""trade_plan 读路径的「一轮计划」提取规则。

**为什么需要（2026-11-05 实测）**
-------------------------------
`trade_plan` 是多档位 × 多轮次的宽表，同一个 `(日期, 档位, code)` 可能有
**多条**行：

- 不同策略各写一轮（`rationale_json.strategy` 不同）—— 这是**设计如此**；
- 回补（`plan build-all --backfill`）逐轮重放，会再写一轮 `hold` 行。

结果：**同一只票既可能是 `buy` 又是 `hold`**（买过、现在持有）。

**旧实现按 `(code, action)` 去重 —— 这是错的**
---------------------------------------
`buy` 和 `hold` 是**不同的 action**，所以同一只票能同时通过去重，
在组合页上**渲染两次**。

实测线上（2026-11-05，清理前）：

    档位   rows  同一 code 出现多次
    5W      34        3
    10W     43        3
    30W     46        4
    50W     46        4

⇒ 页面上同一只票出现两行（一次 buy、一次 hold）。
这正是 CLAUDE.md 记的「读取 10W 档位必须按 code 去重」——
但代码只做到一半（按 code+action），所以 bug 一直还在。

**正确规则：一套计划 = 每个 code 一行**
---------------------------------
同一只票无论 `buy`/`hold`/`exit`，在**一套计划**里只该出现一次
（`MAX_POSITIONS=8` 的语义就是「最多 8 只票」）。
同一只票多行时，保留**更可执行**的那条：

    buy > hold > exit

理由：`buy` 是当天要下单的动作，`hold` 是「不动」，`exit` 是卖出。
一只票当天既被标记 buy 又被标记 hold 时（买了并持有），
展示 `buy` 更有信息量 —— 组合页要告诉用户「今天要买什么」。

**为什么这个模块要独立**
------------------
`export_json.py`（静态站导出）与 `app.py`（Flask 决策台）**各有一份**
同样的去重代码。两份各写一遍的必然后果就是：改了一处忘了另一处，
两个页面的同一只票显示不一致。本模块是**唯一真相源**。
"""

from typing import Any, Dict, Iterable, List


#: 同一只票有多行时，按这个优先级保留一条（数字小者优先）。
#: buy=当天要下单 → hold=不动 → exit=卖出 → 其它兜底。
ACTION_PRIORITY = {"buy": 0, "hold": 1, "exit": 2}


def dedupe_plan_rows(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """从 `trade_plan` 行里取出「每个 code 一行」的一套计划。

    **这是读路径的唯一去重规则**（`export_json.py` 与 `app.py` 都调它）。

    规则：

    1. 同一个 `code` 只保留一行；
    2. 多行时保留 action 优先级最高的（buy > hold > exit）；
    3. 同 action 时保留**输入顺序里第一个**（调用方按 `action DESC, code`
       排序 ⇒ 顺序稳定、可复现）。

    参数
    ----
    rows: `trade_plan` 的行（dict 列表，至少含 `code` 与 `action`）。

    返回
    ----
    每个 code 最多一行的列表，**保持输入顺序**。
    """
    kept: Dict[Any, Dict[str, Any]] = {}
    priority: Dict[Any, int] = {}
    for row in rows:
        code = row.get("code")
        action = row.get("action")
        rank = ACTION_PRIORITY.get(action, 99)
        if code not in kept or rank < priority[code]:
            kept[code] = row
            priority[code] = rank
    return list(kept.values())


__all__ = ["ACTION_PRIORITY", "dedupe_plan_rows"]
