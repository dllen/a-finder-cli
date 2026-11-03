"""刷新按钮的前端契约：登录才显示、静态站不显示、轮询用 setTimeout。

静态站没有 Worker API（`/api/refresh` 在 Cloudflare Worker 上，不在 gh-pages 里），
所以按钮必须在**服务端**就不渲染进静态产物 —— 靠 JS 隐藏是假隐藏
（元素还在 DOM 里，只是 display:none）。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _js(name):
    return (ROOT / "static" / "js" / name).read_text(encoding="utf-8")


def test_api_exposes_refresh_helpers():
    """api.js 必须导出两个函数，路径与 Worker 约定一致。"""
    src = _js("api.js")
    assert "export function fetchRefresh()" in src
    assert "export function fetchRefreshStatus(" in src
    assert "/api/refresh/status" in src


def test_decide_js_has_refresh_button_wiring():
    """decide.js 必须把按钮绑到 fetchRefresh，并轮询状态。"""
    src = _js("decide.js")
    assert "fetchRefresh" in src
    assert "fetchRefreshStatus" in src
    assert "btn-refresh-data" in src


def test_decide_js_uses_recursive_settimeout_not_setinterval():
    """轮询必须用 setTimeout 递归 —— setInterval 会在慢网下堆积请求。

    注意查的是**调用** `setInterval(`，不是这个词本身：
    注释里写「为什么不用 setInterval」是完全正常的，不该因此失败。
    """
    src = _js("decide.js")
    assert "setTimeout(" in src
    assert "setInterval(" not in src, "轮询不要用 setInterval（请求会堆积）"


def test_decide_js_clears_timer_on_visibility_change():
    """页面隐藏时应暂停轮询，回来再继续。"""
    src = _js("decide.js")
    assert "visibilitychange" in src


def test_decide_js_clears_timer_on_logout():
    """登出必须清定时器，否则登出后还在轮询（拿到 401 刷日志）。"""
    src = _js("decide.js")
    # stopRefreshPolling 必须存在，且在登出路径被调用
    assert "stopRefreshPolling" in src
    m = re.search(r"function doLogout\([^)]*\)\s*\{(.*?)\n\}", src, re.S)
    if m:
        assert "stopRefreshPolling" in m.group(1), "登出没清轮询定时器"


def test_template_guards_button_server_side():
    """按钮必须用 show_write_controls 服务端守卫（静态产物里根本不该有它）。"""
    html = (ROOT / "templates" / "decide.html").read_text(encoding="utf-8")
    assert 'id="btn-refresh-data"' in html
    assert 'id="refresh-status"' in html
    # 按钮必须被 show_write_controls 包住
    idx = html.index('id="btn-refresh-data"')
    before = html[:idx]
    assert before.rstrip().endswith("{% if show_write_controls|default(true) %}") or \
        "show_write_controls" in before[-300:], \
        "按钮没有 show_write_controls 服务端守卫"


def test_static_export_omits_refresh_button():
    """静态站产物里不能出现刷新按钮（静态站没有 Worker API）。"""
    from config import PORTFOLIO_10W
    from export_json import _render_static_page

    html = _render_static_page(
        "decide.html",
        today="2026-11-03",
        config=PORTFOLIO_10W,
        year=2026,
    )
    assert "btn-refresh-data" not in html, (
        "静态产物里出现了刷新按钮 —— 静态站没有 /api/refresh，点了必然报错"
    )
