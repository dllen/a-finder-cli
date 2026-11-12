"""`a-finder.sh site` 必须在导出前同步可转债榜单（2026-11-12 加）。

背景：dsh 插件 `dsh-a-finder-sync` 一键同步调用的就是 `bash a-finder.sh site`，
而 `cmd_site` 之前**完全没有**转债调用（`grep -n cb- a-finder.sh` 为空）
⇒ 插件同步后转债页永远停在上一份榜单。插件只透传 `--no-cb`，
所以流水线的唯一真相源必须是这个脚本，不能改契约、不能挪顺序。

契约：
  [1/3] sync-incremental（同步 + 选股）
  [2/3] cb-picks —— `--date "$plan_date"` 是防错标守卫
  [3/3] export_json.py
  cb-picks 失败只告警，绝不中止导出 / 发布。

测试策略：
- 静态：直接读脚本，锁死顺序 / 参数 / --no-cb 分支 / 失败容忍 / 步骤编号。
- 功能：`source ./a-finder.sh` 后把外部依赖（run_cmd/run_py/run_pyc/
  cmd_sync_incremental/curl/git）换成记录器，真跑 `cmd_site`，
  断言 trace 顺序与失败容忍。不跑真实同步，不碰网络。
"""
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SH = ROOT / "a-finder.sh"

# 让 run_pyc 固定吐这个日期：plan_date 与线上最新日（live_latest）都用它，
# 于是发布前的单调性守卫（产物不得比线上旧）恒通过，不会干扰本次断言。
FIXED_DATE = "2026-11-12"


def _text():
    return SH.read_text(encoding="utf-8")


def _cmd_site_body():
    """抽出 cmd_site 的函数体（与 test_site_publish_guard.py 同一取法）。"""
    m = re.search(r"^cmd_site\(\) \{(.*?)^\}", _text(), re.S | re.M)
    assert m, "cmd_site 函数体找不到"
    return m.group(1)


def _cb_segment(body):
    """cb-picks 调用点到 export_json.py 之间的代码段。"""
    assert "cb-picks" in body, "cmd_site 里没有 cb-picks 调用（转债榜单缺失）"
    assert "export_json.py" in body, "cmd_site 里没有 export_json.py 调用"
    # 锚定真实调用（函数头注释里也提到 cb-picks，不能取第一次出现）
    start = body.index("run_cmd cb-picks")
    end = body.index("export_json.py")
    assert start < end, "cb-picks 必须在 export_json.py 之前"
    return body[start:end]


# ---------------------------------------------------------------- 静态契约


def test_cb_picks_runs_between_plan_date_check_and_export():
    """顺序：plan_date 非空检查 < cb-picks < export_json.py。"""
    body = _cmd_site_body()
    assert "cb-picks" in body, "cmd_site 没有转债调用 —— 插件的「一键同步」会漏转债"
    plan_check = body.index('if [[ -z "$plan_date" ]]')
    cb = body.index("run_cmd cb-picks")
    export = body.index("export_json.py")
    assert plan_check < cb < export, (
        "cb-picks 必须放在 plan_date 非空检查之后、export_json.py 之前\n"
        f"位置: plan_check={plan_check} cb={cb} export={export}"
    )


def test_cb_picks_uses_plan_date_as_mislabel_guard():
    """--date "$plan_date" 是防错标守卫，不是可选参数。"""
    seg = _cb_segment(_cmd_site_body())
    assert '--date "$plan_date"' in seg, (
        "cb-picks 缺 --date 参数（$plan_date）：同步失败/非交易日时会把今天的转债快照"
        "错标到旧日期，比缺失更糟"
    )


def test_cb_picks_matches_plugin_contract_args():
    """插件侧依赖的命令形态（top/db/并发/限流）不可改名。"""
    seg = _cb_segment(_cmd_site_body())
    cb_line = [ln for ln in seg.splitlines() if "cb-picks" in ln]
    assert cb_line, seg
    line = cb_line[0]
    for piece in (
        '--top "$top"',
        '--db "$db"',
        "--concurrency 2",
        "--rate 4",
    ):
        assert piece in line, f"cb-picks 调用缺 {piece!r}: {line}"


def test_no_cb_flag_is_parsed_in_existing_arg_loop():
    """--no-cb 在既有 for a in "$@" 循环里解析，默认 cb=1。"""
    body = _cmd_site_body()
    assert "--no-cb" in body, "cmd_site 不认 --no-cb（插件会传它）"
    assert '[[ "$a" == "--no-cb" ]]' in body, (
        "应与 --push 同一参数循环解析，而不是自创一套参数解析"
    )
    assert "cb=1" in body and "cb=0" in body, "缺默认 cb=1 / --no-cb 置 cb=0"


def test_cb_failure_is_tolerated():
    """cb-picks 失败只告警：不 return / exit，导出继续。"""
    seg = _cb_segment(_cmd_site_body())
    assert "||" in seg, "cb-picks 没有 || 兜底 —— set -e 会直接中断整条流水线"
    assert "WARN" in seg, "失败时没有 WARN 提示（静默失败最难查）"
    assert "return 1" not in seg and "exit 1" not in seg, (
        "cb-picks 失败分支不允许中止流水线"
    )


def test_site_reports_three_steps_not_two():
    """步骤编号 [1/3] / [2/3] / [3/3]（旧的 [2/2] 要消失）。"""
    body = _cmd_site_body()
    for tag in ("[1/3]", "[2/3]", "[3/3]"):
        assert tag in body, f"缺步骤编号 {tag}"
    assert "[2/2]" not in body, "仍是旧的两步编号"


def test_help_documents_no_cb():
    text = _text()
    help_pos = text.index("print_help() {")
    assert "--no-cb" in text[help_pos:], "print_help 的 site 说明没写 --no-cb"


# ---------------------------------------------------------------- 功能契约

_SCRIPT = r"""
source ./a-finder.sh
set +e
TRACE="$TRACE_FILE"
# 覆盖外部依赖为记录器：不跑真实同步/网络/git，只记录调用顺序。
run_cmd() {
  printf 'run_cmd %s\n' "$*" >> "$TRACE"
  # 只让 cb-picks 失败（模拟转债数据源不可用），其余（若有）成功。
  if [[ "$*" == cb-picks* && "${FAIL_CB:-0}" == "1" ]]; then
    return 3
  fi
  return 0
}
run_py()  { printf 'run_py %s\n' "$*" >> "$TRACE"; return 0; }
run_pyc() { printf 'run_pyc\n' >> "$TRACE"; printf '%s\n' "__FIXED_DATE__"; }
cmd_sync_incremental() { printf 'sync %s\n' "$*" >> "$TRACE"; return 0; }
curl() { return 0; }
git()  { return 0; }
cmd_site "$DB" "$TOP" "$OUT" __EXTRA__
rc=$?
echo "RC=$rc"
"""


def _run_site(tmp_path, extra="", fail_cb=False):
    """在独立 bash 里 source + 打桩后真跑一次 cmd_site。返回 (rc, trace, proc)。"""
    out = tmp_path / "site"
    (out / "data").mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text("<html>stub</html>", encoding="utf-8")
    trace_file = tmp_path / "trace.log"
    trace_file.write_text("", encoding="utf-8")

    script = (
        _SCRIPT.replace("__EXTRA__", extra)
        .replace("__FIXED_DATE__", FIXED_DATE)
    )
    env = dict(os.environ)
    env.update(
        {
            "TRACE_FILE": str(trace_file),
            "DB": "hs300.db",
            "TOP": "20",
            "OUT": str(out),
            "FAIL_CB": "1" if fail_cb else "0",
        }
    )
    # $0 传 "a-finder.sh" ⇒ 被 source 的脚本里 dirname "$0" == "." ⇒
    # ROOT_DIR == cwd == 仓库根（cwd 显式指定）。
    proc = subprocess.run(
        ["bash", "-c", script, "a-finder.sh"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    trace = [
        ln for ln in trace_file.read_text(encoding="utf-8").splitlines() if ln.strip()
    ]
    rcs = [ln for ln in proc.stdout.splitlines() if ln.startswith("RC=")]
    assert rcs, (
        "cmd_site 没跑到最后（脚本可能被 set -e/-u 打断）\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}\ntrace={trace}"
    )
    return int(rcs[-1].split("=", 1)[1]), trace, proc


def test_default_run_orders_sync_cb_export(tmp_path):
    """默认：sync → cb-picks → export，且 --date 用库里的 plan_date。"""
    rc, trace, proc = _run_site(tmp_path)
    assert rc == 0, f"rc={rc}\ntrace={trace}\nstderr={proc.stderr}"

    sync_i = next((i for i, ln in enumerate(trace) if ln.startswith("sync ")), None)
    cb_i = next(
        (i for i, ln in enumerate(trace) if ln.startswith("run_cmd cb-picks")), None
    )
    export_i = next(
        (i for i, ln in enumerate(trace) if "export_json.py" in ln), None
    )
    assert sync_i is not None, f"没跑同步: {trace}"
    assert cb_i is not None, f"没跑转债榜单: {trace}"
    assert export_i is not None, f"没跑导出: {trace}"
    assert sync_i < cb_i < export_i, f"顺序错: {trace}"

    cb_line = trace[cb_i]
    assert f"--date {FIXED_DATE}" in cb_line, (
        f"cb-picks 的 --date 不是 plan_date: {cb_line}"
    )
    assert "--top 20" in cb_line and "--db hs300.db" in cb_line, cb_line


def test_no_cb_skips_cb_but_still_exports(tmp_path):
    """--no-cb：不调转债，但同步与导出照常。"""
    rc, trace, proc = _run_site(tmp_path, extra="--no-cb")
    assert rc == 0, f"rc={rc}\ntrace={trace}\nstderr={proc.stderr}"
    assert not any("cb-picks" in ln for ln in trace), f"--no-cb 仍然调了转债: {trace}"
    assert any(ln.startswith("sync ") for ln in trace), trace
    assert any("export_json.py" in ln for ln in trace), trace


def test_cb_failure_warns_but_export_still_runs(tmp_path):
    """转债失败（rc=3）时：只告警，导出照跑，整体 rc=0。"""
    rc, trace, proc = _run_site(tmp_path, fail_cb=True)
    assert rc == 0, (
        f"转债失败把整条流水线拖红了（rc={rc}）—— 违约\n"
        f"trace={trace}\nstderr={proc.stderr}"
    )
    assert any(ln.startswith("run_cmd cb-picks") for ln in trace), trace
    export_i = next(
        (i for i, ln in enumerate(trace) if "export_json.py" in ln), None
    )
    assert export_i is not None, f"转债失败后没有导出: {trace}"
    assert "WARN" in proc.stderr, f"失败没有 WARN 告警\nstderr={proc.stderr}"
