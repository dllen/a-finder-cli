"""刷新接口的前端契约（api.js 仍导出 Worker 刷新函数）。

⚠️ 2026-11-05：「决策台」已下线，刷新按钮所在的 decide.html / decide.js
已删除（用户要求下线交易计划与持仓组合时一并去掉决策台）。

本文件原来断言「按钮必须在服务端就守卫、静态站不渲染」等 6 条 ——
它们全部针对 decide.html / decide.js，目标文件已不存在 ⇒ 已删除。

保留这一条：`api.js` 仍要导出 Worker 刷新函数。刷新链路（Worker 排队 →
本机 refresh_agent 跑流水线）是独立基础设施，只是**当前没有页面挂按钮**；
将来重新接入时，api.js 的这两个函数必须还在、且路径与 Worker 约定一致。
"""
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
