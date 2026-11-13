"""静态导出：写入控件在静态模式下被隐藏的回归测试。

⚠️ 2026-11-05：「交易计划」「持仓组合」「决策台」已下线（用户要求），
所以本文件里原来针对 plan.html / holdings.json 的断言已删除或改写：
  - plan.html 模板已删除 ⇒ 只校验 picks.html 的写入控件被隐藏；
  - holdings.json 不再产出 ⇒ 改成断言它**不**存在（下线要彻底）；
  - _cleanup_stale_date_files 的签名去掉了 plan_dates，且它现在负责
    **主动删除**已下线功能的产物（plan-* / decide-* / portfolio/ 等）。
"""


def test_static_mode_hides_write_controls():
    """Static mode renders templates with show_write_controls=False."""
    from export_json import _render_static_page

    picks_html = _render_static_page("picks.html", today="2026-10-24", year=2026)
    cb_html = _render_static_page("cb.html", today="2026-10-24", year=2026)

    # picks 页的写入控件来自 filter_toolbar.html，有 show_write_controls 服务端守卫
    assert "write-control" not in picks_html
    assert "btn-recalc" not in picks_html
    assert "btn-sync" not in picks_html
    assert "btn-build" not in picks_html
    assert "include-failed" not in picks_html

    # 导航与内容仍在
    assert "每日机会" in picks_html
    assert 'id="dashboard"' in picks_html
    assert 'id="board"' in picks_html
    assert 'id="q"' in picks_html

    # cb 页仍能渲染（其「刷新」按钮**没有**服务端守卫 —— 既有问题，
    # 不属本次下线范围：静态站靠 api.js 用 JS 隐藏 .write-control，
    # 那正是本文件 docstring 说的「假隐藏」，见汇总说明）
    assert "可转债" in cb_html
    assert 'id="cb-refresh"' in cb_html


def test_risk_note_renders_above_the_page_content():
    """免责声明必须在页面内容之前（导航下方），不能只躺在页脚。

    2026-11-13 用户要求「免责声明更醒目一些」：原先只有页脚一行 muted 小字，
    长表格要滚到底才看得到。现在提为导航下的全站提示条，两页共用；
    页脚不再重复同一句（一页只说一次）。
    """
    from export_json import _render_static_page

    for page in ("picks.html", "cb.html"):
        html = _render_static_page(page, today="2026-10-24", year=2026)
        assert 'class="risk-band"' in html, page
        assert "数据仅供参考，不构成投资建议" in html, page
        # 位置：在 <main> 之前（导航下方、首屏可见），而不是只在 footer
        assert html.index("risk-band") < html.index('id="main"'), page
        # 只说一次：页脚不再重复
        assert html.count("数据仅供参考，不构成投资建议") == 1, page


def test_export_does_not_emit_retired_artifacts(tmp_path):
    """已下线功能的产物**不能**出现在导出里（含 holdings.json / plan / portfolio）。

    仅「不再生成」不够：老产物若留在 site/ 会被一起推上 gh-pages，
    站点等于没下线。export 必须主动删除它们。
    """
    import os
    import tempfile
    from db_repository import open_db
    from export_json import export

    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        open_db(db).close()
        out = str(tmp_path / "site")
        export(db, out)
        data = os.path.join(out, "data")
        for gone in ("holdings.json", "plan-dates.json"):
            assert not os.path.exists(os.path.join(data, gone)), gone
        assert not os.path.exists(os.path.join(data, "portfolio"))
        for page in ("plan.html", "portfolio.html", "decide.html"):
            assert not os.path.exists(os.path.join(out, page)), page
        assert os.path.exists(os.path.join(out, "index.html"))
        assert os.path.exists(os.path.join(out, "cb.html"))
    finally:
        os.unlink(db)


def test_cleanup_removes_retired_and_stale_files(tmp_path):
    """清理：陈旧 picks-* 要删；已下线功能的产物**无条件**删；无关文件保留。"""
    from export_json import _cleanup_stale_date_files

    data = tmp_path / "data"
    data.mkdir()
    (data / "picks-2026-08-12.json").write_text("{}")
    (data / "picks-2026-08-20.json").write_text("{}")      # 陈旧 picks
    (data / "plan-2026-08-12.json").write_text("{}")       # 已下线：删
    (data / "plan-dates.json").write_text("{}")            # 已下线：删
    (data / "decide-2026-08-12.json").write_text("{}")     # 已下线：删
    (data / "holdings.json").write_text("{}")              # 已下线：删
    (data / "dashboard.json").write_text("{}")             # 保留
    (data / "dates.json").write_text("{}")                 # 保留
    pf = data / "portfolio"
    pf.mkdir()
    (pf / "10W.json").write_text("{}")                     # 整目录删

    _cleanup_stale_date_files(data, ["2026-08-12"])

    names = sorted(p.name for p in data.iterdir())
    assert names == ["dashboard.json", "dates.json", "picks-2026-08-12.json"]


def test_export_copies_self_hosted_fonts(tmp_path):
    """字体也是设计系统的一部分，静态站必须带上。

    和收款码同一个坑：本地 Flask 直接读 static/ 永远正常，静态站是
    export_json.py 逐个目录拷出来的，漏掉 fonts/ 只在线上暴露。
    """
    import os
    import tempfile
    from db_repository import open_db
    from export_json import export

    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        open_db(db).close()
        out = tmp_path / "site"
        export(db, str(out))
        for name in ("host-grotesk.woff2", "atkinson-mono.woff2"):
            p = out / "static" / "fonts" / name
            assert p.is_file(), "静态产物缺少字体 %s" % name
            assert p.stat().st_size > 5_000
    finally:
        os.unlink(db)
