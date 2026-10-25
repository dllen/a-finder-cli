"""静态导出：写入控件在静态模式下被隐藏的回归测试。"""


def test_static_mode_hides_write_controls():
    """Static mode renders templates with show_write_controls=False."""
    from export_json import _render_static_page
    from config import CAPITAL_TIERS

    picks_html = _render_static_page("picks.html", today="2026-10-24", year=2026)
    tier_filter_options = "".join(
        f'<li><label class="dropdown-item"><input type="checkbox" class="form-check-input me-1 tier-filter-cb" value="{c}"> {c // 10000}W</label></li>'
        for c in CAPITAL_TIERS
    )
    plan_html = _render_static_page("plan.html",
        today="2026-10-24", capital_tiers=CAPITAL_TIERS,
        tier_filter_options=tier_filter_options, year=2026)

    for html in (picks_html, plan_html):
        assert "write-control" not in html
        assert "btn-recalc" not in html
        assert "btn-sync" not in html
        assert "btn-build" not in html
        assert "include-failed" not in html

    # Navigation and content still present
    assert "每日机会" in picks_html
    assert 'id="dashboard"' in picks_html
    assert "交易计划" in plan_html
    assert 'id="board"' in plan_html

    # Search input not stripped
    assert 'id="q"' in picks_html


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
