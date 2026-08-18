"""静态导出：去掉操作按钮的回归测试。"""
from app import PAGE_BODY, PLAN_BODY
from export_json import _strip_write_controls


def test_strip_write_controls_removes_buttons_and_checkbox():
    picks = _strip_write_controls(PAGE_BODY)
    plan = _strip_write_controls(PLAN_BODY)

    for html in (picks, plan):
        assert "write-control" not in html
        assert "btn-recalc" not in html
        assert "btn-sync" not in html
        assert "btn-build" not in html
        assert "include-failed" not in html

    # 导航与内容仍在
    assert "每日机会" in picks
    assert 'id="dashboard"' in picks
    assert "交易计划" in plan
    assert 'id="board"' in plan
