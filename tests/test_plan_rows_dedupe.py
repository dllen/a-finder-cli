"""shared_lib.plan_rows —— trade_plan 读路径的「一套计划」提取规则。

**这个测试守住的是用户可见的正确性**
------------------------------
2026-11-05 实测：线上组合页**同一只票渲染两次**（一次 buy、一次 hold）。
根因：去重键用了 `(code, action)`，而 `buy` 与 `hold` 是不同的 action。

修法：去重键改成 `code`，多行时保留 action 优先级最高的（buy > hold > exit）。
规则放在 `shared_lib` 是因为 `export_json.py` 与 `app.py` **各有一份**读路径，
两份各写一遍必然漂移 —— 本模块是唯一真相源。
"""

from shared_lib.plan_rows import ACTION_PRIORITY, dedupe_plan_rows


class TestDedupeByCode:

    def test_same_code_buy_and_hold_keeps_one(self):
        """**回归测试**：同一只票既 buy 又 hold ⇒ 只剩一行（旧实现会留两行）。"""
        rows = [
            {"code": "300316", "action": "hold", "plan_price": 37.6},
            {"code": "300316", "action": "buy", "plan_price": 37.6},
        ]
        out = dedupe_plan_rows(rows)
        assert len(out) == 1, "同一只票出现两次 —— 这就是线上那个 bug"
        assert out[0]["code"] == "300316"

    def test_keeps_buy_over_hold(self):
        """买更可执行 ⇒ 保留 buy。"""
        rows = [
            {"code": "601058", "action": "hold"},
            {"code": "601058", "action": "buy"},
        ]
        out = dedupe_plan_rows(rows)
        assert len(out) == 1
        assert out[0]["action"] == "buy"

    def test_hold_wins_over_exit(self):
        rows = [
            {"code": "000001", "action": "exit"},
            {"code": "000001", "action": "hold"},
        ]
        out = dedupe_plan_rows(rows)
        assert len(out) == 1
        assert out[0]["action"] == "hold"

    def test_distinct_codes_all_kept(self):
        rows = [
            {"code": "000001", "action": "buy"},
            {"code": "000002", "action": "buy"},
            {"code": "000003", "action": "hold"},
        ]
        assert len(dedupe_plan_rows(rows)) == 3

    def test_input_order_preserved(self):
        rows = [
            {"code": "c3", "action": "buy"},
            {"code": "c1", "action": "buy"},
            {"code": "c2", "action": "buy"},
        ]
        assert [r["code"] for r in dedupe_plan_rows(rows)] == ["c3", "c1", "c2"]

    def test_same_action_keeps_first(self):
        """同 action 多行 ⇒ 保留输入里第一个（顺序稳定、可复现）。"""
        rows = [
            {"code": "000001", "action": "buy", "plan_id": 10},
            {"code": "000001", "action": "buy", "plan_id": 20},
        ]
        out = dedupe_plan_rows(rows)
        assert len(out) == 1
        assert out[0]["plan_id"] == 10

    def test_empty_input(self):
        assert dedupe_plan_rows([]) == []

    def test_unknown_action_is_kept_and_lowest_priority(self):
        rows = [
            {"code": "000001", "action": "weird"},
            {"code": "000001", "action": "buy"},
        ]
        out = dedupe_plan_rows(rows)
        assert len(out) == 1
        assert out[0]["action"] == "buy"


class TestActionPriority:

    def test_priority_order(self):
        assert ACTION_PRIORITY["buy"] < ACTION_PRIORITY["hold"]
        assert ACTION_PRIORITY["hold"] < ACTION_PRIORITY["exit"]
