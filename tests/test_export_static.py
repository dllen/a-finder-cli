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

    # 快速筛选输入框不被 strip 误删
    assert 'id="q"' in picks
    assert 'id="q"' in plan


def test_export_emits_holdings_json(tmp_path):
    import os, tempfile
    from db_repository import open_db
    from export_json import export
    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    open_db(db).close()
    out = str(tmp_path / "site")
    export(db, out)
    assert os.path.exists(os.path.join(out, "data", "holdings.json"))


def test_cleanup_stale_date_files(tmp_path):
    from export_json import _cleanup_stale_date_files
    data = tmp_path / "data"
    data.mkdir()
    (data / "picks-2026-08-12.json").write_text("{}")
    (data / "picks-2026-08-20.json").write_text("{}")  # 陈旧
    (data / "plan-2026-08-12.json").write_text("{}")
    (data / "plan-2026-08-20.json").write_text("{}")  # 陈旧
    (data / "plan-dates.json").write_text("{}")  # 特殊文件，不应被删
    (data / "dashboard.json").write_text("{}")  # 非日期文件，不应被删

    _cleanup_stale_date_files(data, ["2026-08-12"], ["2026-08-12"])

    names = sorted(p.name for p in data.iterdir())
    assert "picks-2026-08-12.json" in names
    assert "picks-2026-08-20.json" not in names
    assert "plan-2026-08-12.json" in names
    assert "plan-2026-08-20.json" not in names
    assert "plan-dates.json" in names
    assert "dashboard.json" in names
