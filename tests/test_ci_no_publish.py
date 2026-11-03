"""CI 的发布权限收敛：deploy-site 不再推 gh-pages，deploy-worker 同步新 secret。

背景：本机（refresh_agent + a-finder.sh site --push）成为 gh-pages 的**唯一日常写者**
之后，CI 再发布就是双写分叉 —— 2026-10-29 事故的根因正是「两个写者互不知情」。

同时守住「不能顺手删掉缓存保存 / freshness 判据 / 同步步骤」——
那是 4 个 workflow 共享的谱系根，删了会让 CI 行情谱系冻结。

**不用 YAML 解析器**：venv 里没有 PyYAML（pyproject.toml 也没这个依赖），
照 tests/test_daily_audit_workflow.py 的既有做法按缩进切 step 文本块。
"""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WF = ROOT / ".github" / "workflows"


def _text(name):
    return (WF / name).read_text(encoding="utf-8")


def test_deploy_site_does_not_publish_to_gh_pages():
    """deploy-site 不得再推 gh-pages（本机是唯一日常写者）。"""
    src = _text("deploy-site.yml")
    assert "peaceiris/actions-gh-pages" not in src, (
        "deploy-site 还在推 gh-pages —— 会与本机双写，造成分叉"
    )


def test_deploy_site_still_saves_cache():
    """缓存保存必须保留 —— 删了会让 CI 行情谱系冻结。"""
    src = _text("deploy-site.yml")
    assert "actions/cache@v4" in src, "缓存保存被误删"
    assert "hs300-db-" in src, "缓存 key 规则被误改"


def test_deploy_site_keeps_freshness_gate():
    """freshness 判据（skip_sync / site_stale）必须保留。"""
    src = _text("deploy-site.yml")
    assert "db_freshness.py" in src, "freshness 判据被误删"
    assert "skip_sync" in src
    assert "site_stale" in src


def test_deploy_site_keeps_sync_step():
    """同步步骤必须保留 —— CI 仍需维护自己的缓存库。"""
    src = _text("deploy-site.yml")
    assert "sync-incremental" in src or "sync-hs300-range" in src


def test_deploy_site_run_blocks_pass_bash_syntax_check():
    """每个 run 块都要过 bash -n（缩进 0 会让块标量截断整个文件）。"""
    import sys

    sys.path.insert(0, str(ROOT / "tests"))
    from test_daily_audit_workflow import _run_script, _step_block, _step_names

    text = _text("deploy-site.yml")
    checked = 0
    for name in _step_names(text):
        script = _run_script(_step_block(text, name))
        if not script.strip():
            continue
        checked += 1
        proc = subprocess.run(["bash", "-n"], input=script,
                              capture_output=True, text=True)
        assert proc.returncode == 0, (name, proc.stderr)
    assert checked >= 5, "应该有不少于 5 个带脚本的步骤"


def test_daily_audit_still_publishes():
    """daily-audit 仍保留发布权（极低频，只在修复缺口时）。"""
    assert "peaceiris/actions-gh-pages" in _text("daily-audit.yml"), (
        "daily-audit 的发布被误删 —— 设计上它保留发布权"
    )


def test_deploy_worker_syncs_refresh_agent_secret():
    """deploy-worker 必须同步 REFRESH_AGENT_SECRET。

    否则线上 Worker 拿不到这个 secret ⇒ agent 端点一律 401 ⇒
    刷新按钮永远停在 pending（前端看起来像「本机没响应」）。
    """
    src = _text("deploy-worker.yml")
    assert "REFRESH_AGENT_SECRET" in src, (
        "deploy-worker 没同步 REFRESH_AGENT_SECRET —— 线上刷新按钮会是死的"
    )
    assert "secret put REFRESH_AGENT_SECRET" in src
