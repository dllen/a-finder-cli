"""dsh-a-finder-sync 插件的可转债同步契约测试。

插件自己**不重写**同步流水线：它只给 `a-finder.sh site` 透传 `--no-cb`，转债步骤由宿主
shell 侧负责。所以这里测的都是「插件有没有把开关正确传下去 / 有没有把转债日期正确读出来
并放进摘要」，而不是转债逻辑本身（那由 tests/test_site_cb_sync.py 覆盖）。

node 模式沿用 tests/test_refresh_worker.py：没有 node 就 skip，用 `node --input-type=module -e`
跑真代码；插件路径一律用 file:// import（不 import 任何 @deepseek-ai 宿主包）。
"""

import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_LIB = ROOT / "plugins" / "dsh-a-finder-sync" / "lib"
SYNC_JS = PLUGIN_LIB / "sync.js"
INDEX_JS = PLUGIN_LIB / "index.js"

# 假 a-finder.sh：把收到的参数逐行写到 args.txt（`"$@"` 不含脚本名 $0）。
# 用 printf 而不是 echo，保证参数边界清楚；exit 0 让 pipeline 判成功。
FAKE_A_FINDER = "#!/bin/bash\nprintf '%s\\n' \"$@\" > args.txt\n"

# node 侧共用：把 args.txt 读成数组（不写 '\\n'，避开 Python/JS 双重转义）
READ_ARGS_JS = """
import { readFile } from 'node:fs/promises'
const readArgs = async () => {
  const raw = await readFile('args.txt', 'utf8')
  return raw.split(String.fromCharCode(10)).filter((line) => line.length > 0)
}
"""


def _require_node():
    if shutil.which("node") is None:
        pytest.skip("node 不在 PATH，无法跑插件契约测试")


def _run_node(source, cwd):
    """跑一段 ESM 脚本，要求它把结果以 JSON 写到 stdout。"""
    _require_node()
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", source],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"node 失败: {proc.stderr}"
    return json.loads(proc.stdout)


@pytest.fixture()
def project(tmp_path):
    """一个只含假 a-finder.sh 的临时仓库根目录。"""
    script = tmp_path / "a-finder.sh"
    script.write_text(FAKE_A_FINDER, encoding="utf-8")
    script.chmod(0o755)
    return tmp_path


def _sync_uri():
    return SYNC_JS.as_uri()


def _make_db(path, *, prices, cb_picks, with_cb_table=True):
    """建一个最小库：daily_prices 必有，cb_picks 按参数决定。"""
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE daily_prices (code TEXT, trade_date TEXT)")
        conn.executemany(
            "INSERT INTO daily_prices (code, trade_date) VALUES (?, ?)",
            [("000001", date) for date in prices],
        )
        if with_cb_table:
            conn.execute("CREATE TABLE cb_picks (trade_date TEXT, rank INTEGER, code TEXT)")
            conn.executemany(
                "INSERT INTO cb_picks (trade_date, rank, code) VALUES (?, ?, ?)",
                [(date, idx + 1, "113059") for idx, date in enumerate(cb_picks)],
            )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1. runSitePipeline：只有 cb=false 才追加 --no-cb
# ---------------------------------------------------------------------------


def test_run_site_pipeline_cb_flag(project):
    """默认带转债（无 --no-cb）；cb:false 才追加 --no-cb；--push 仍然只在 push 时加。"""
    source = READ_ARGS_JS + """
const sync = await import('__SYNC_JS__')
const out = []
const calls = [
  {},                              // 全默认：push + cb 都开
  { cb: true, push: true },        // 显式开转债
  { cb: false },                   // 关转债
  { push: false, cb: false },      // 只更新本地、不推、不同步转债
]
for (const options of calls) {
  const result = await sync.runSitePipeline({
    projectDir: process.cwd(),
    timeoutMs: 20000,
    ...options,
  })
  out.push({ args: await readArgs(), ok: result.ok, code: result.code })
}
process.stdout.write(JSON.stringify(out))
""".replace("__SYNC_JS__", _sync_uri())

    got = _run_node(source, project)
    base = ["site", "hs300.db", "20", "site"]

    assert [step["ok"] for step in got] == [True, True, True, True], got
    assert [step["code"] for step in got] == [0, 0, 0, 0], got
    # 默认：转债步骤由流水线自己跑，插件**不**传 --no-cb
    assert got[0]["args"] == base + ["--push"], got
    assert got[1]["args"] == base + ["--push"], got
    # cb=false：--no-cb 在 --push 之后
    assert got[2]["args"] == base + ["--push", "--no-cb"], got
    # 不推送时连 --push 都没有，但 --no-cb 照旧透传
    assert got[3]["args"] == base + ["--no-cb"], got


# ---------------------------------------------------------------------------
# 2. 日期读取：readLatestTradeDate / readCbLatestDate
# ---------------------------------------------------------------------------


def test_read_date_helpers(project):
    """两个日期读取函数各查各的表；空转债表返回 ''，缺表则 reject（由调用方 catch）。"""
    main_db = project / "hs300.db"
    _make_db(
        main_db,
        prices=["2026-11-11", "2026-11-12"],
        cb_picks=["2026-11-11", "2026-11-12"],
    )
    empty_cb_db = project / "empty-cb.db"
    _make_db(empty_cb_db, prices=["2026-11-12"], cb_picks=[])
    missing_table_db = project / "missing-cb.db"
    _make_db(missing_table_db, prices=["2026-11-12"], cb_picks=[], with_cb_table=False)

    source = """
const sync = await import('__SYNC_JS__')
const out = { main: {}, emptyCb: {}, missingTable: {} }
out.main.latest = await sync.readLatestTradeDate(__MAIN__)
out.main.cb = await sync.readCbLatestDate(__MAIN__)
out.emptyCb.cb = await sync.readCbLatestDate(__EMPTY__)
try {
  await sync.readCbLatestDate(__MISSING__)
  out.missingTable.rejected = false
} catch {
  out.missingTable.rejected = true
}
process.stdout.write(JSON.stringify(out))
""".replace("__SYNC_JS__", _sync_uri()).replace(
        "__MAIN__", json.dumps(str(main_db))
    ).replace("__EMPTY__", json.dumps(str(empty_cb_db))).replace(
        "__MISSING__", json.dumps(str(missing_table_db))
    )

    got = _run_node(source, project)

    assert got["main"]["latest"] == "2026-11-12", got
    assert got["main"]["cb"] == "2026-11-12", got
    # 空 cb_picks 表：MAX() 是 NULL，读成 ''，而不是 'null' 之类的东西
    assert got["emptyCb"]["cb"] == "", got
    # 缺表时抛错，由调用方决定怎么降级（插件里 catch 成 ''）
    assert got["missingTable"]["rejected"] is True, got


# ---------------------------------------------------------------------------
# 3. summarize：转债日期非空才出现「可转债榜单 X」
# ---------------------------------------------------------------------------


def test_summarize_includes_cb_only_when_present(project):
    """有转债日期时句子通顺地带上一段；空/未传时不出现，且原句式不变。"""
    source = """
const sync = await import('__SYNC_JS__')
const ok = { ok: true, code: 0, output: '', timedOut: false }
const out = {
  withCb: sync.summarize(ok, '2026-11-12', '2026-11-12', '2026-11-12'),
  emptyCb: sync.summarize(ok, '2026-11-12', '2026-11-12', ''),
  omittedCb: sync.summarize(ok, '2026-11-12', '2026-11-12'),
  cbOnly: sync.summarize(ok, '', '', '2026-11-12'),
}
process.stdout.write(JSON.stringify(out))
""".replace("__SYNC_JS__", _sync_uri())

    got = _run_node(source, project)

    assert got["withCb"] == "同步完成，数据库最新交易日 2026-11-12，站点最新日期 2026-11-12，可转债榜单 2026-11-12。", got
    assert "可转债榜单" in got["withCb"], got
    assert got["emptyCb"] == "同步完成，数据库最新交易日 2026-11-12，站点最新日期 2026-11-12。", got
    assert "可转债榜单" not in got["emptyCb"], got
    # 老调用方（只传 3 个参数）行为不变
    assert got["omittedCb"] == got["emptyCb"], got
    # 只有转债日期时句子仍然只有一段
    assert got["cbOnly"] == "同步完成，可转债榜单 2026-11-12。", got


# ---------------------------------------------------------------------------
# 4. index.js：工具 schema 的 cb 是可选 boolean（不许写 required:false），
#    且 execute / 配置默认值都真的把 cb 透传到流水线
# ---------------------------------------------------------------------------


def test_index_tool_schema_and_cb_wiring(project):
    _require_node()
    source = READ_ARGS_JS + """
const mod = await import('__INDEX_JS__')

const applyPlugin = (config) => {
  const registered = {}
  mod.apply(
    { tools: { register(tool) { registered.tool = tool } }, inject() {} },
    { projectDir: process.cwd(), autoSchedule: false, ...config },
  )
  return registered.tool
}

const tool = applyPlugin({})
const schema = tool.parameters.properties
const out = {
  description: tool.description,
  hasCb: Boolean(schema && schema.cb),
  cbType: schema && schema.cb ? schema.cb.type : null,
  topLevelRequired: tool.parameters.required ?? null,
  // 仓库踩过的坑：可选参数写 required:false 会被 JsonSchema 校验拒绝。
  // 这里同时盯两级：参数对象里、以及 parameters 顶层。
  cbHasRequiredKey: Boolean(schema && schema.cb && 'required' in schema.cb),
  runs: [],
}

// 工具参数：{push:false, cb:false} → --no-cb 且没有 --push
out.runs.push({
  args: (await tool.execute({ push: false, cb: false }), await readArgs()),
})

// 工具参数留空：走配置默认值（cb 默认 true）→ 有 --push、无 --no-cb
out.runs.push({ args: (await tool.execute({}), await readArgs()) })

// 配置里 cb:false ⇒ 工具不传 cb 时也走 --no-cb（settings.cb !== false 的反面）
const toolNoCb = applyPlugin({ cb: false })
out.configDefault = (await toolNoCb.execute({ push: false }), await readArgs())

// push:false + cb:true（显式）⇒ 只有 --no-cb 不出现，--push 也没有
const toolPushOff = applyPlugin({ push: false })
out.pushOff = (await toolPushOff.execute({ cb: true }), await readArgs())

process.stdout.write(JSON.stringify(out))
""".replace("__INDEX_JS__", INDEX_JS.as_uri())

    got = _run_node(source, project)
    base = ["site", "hs300.db", "20", "site"]

    # 工具描述确实告诉模型它同步可转债榜单
    assert "可转债榜单" in got["description"], got
    # 可选参数：存在、boolean、且不能在 parameters 上写 required 键
    assert got["hasCb"] is True, got
    assert got["cbType"] == "boolean", got
    assert got["topLevelRequired"] is None, got
    assert got["cbHasRequiredKey"] is False, got

    assert got["runs"][0]["args"] == base + ["--no-cb"], got
    assert got["runs"][1]["args"] == base + ["--push"], got
    assert got["configDefault"] == base + ["--no-cb"], got
    assert got["pushOff"] == base, got


# ---------------------------------------------------------------------------
# 5. HTTP 路由：/status 暴露转债日期，/sync 从 body 读 cb 并在响应里回显
# ---------------------------------------------------------------------------


def test_http_routes_expose_cb(project):
    """/status 返回 cb 最新日；/sync 读 body.cb 传下去、响应里也带 cb。"""
    _make_db(project / "hs300.db", prices=["2026-11-12"], cb_picks=["2026-11-12"])
    site_data = project / "site" / "data"
    site_data.mkdir(parents=True)
    (site_data / "dates.json").write_text(
        json.dumps({"dates": ["2026-11-12"]}), encoding="utf-8"
    )

    source = READ_ARGS_JS + """
const mod = await import('__INDEX_JS__')

let handler = null
const webCtx = {
  effect(fn) { fn() },
  webServer: { register(options) { handler = options.handler } },
}
mod.apply(
  {
    tools: { register() {} },
    inject(deps, fn) {
      if (deps.includes('webServer')) fn(webCtx)
    },
  },
  { projectDir: process.cwd(), autoSchedule: false },
)

// 最小 node:http 请求/响应桩：handler 是异步的，所以 end 用 Promise 收回
const call = (method, path, body) => new Promise((resolve) => {
  const listeners = {}
  const req = {
    method,
    url: path,
    on(event, cb) {
      (listeners[event] ||= []).push(cb)
      return req
    },
  }
  const res = {
    status: 0,
    writeHead(status) { res.status = status },
    end(payload) { resolve({ status: res.status, json: JSON.parse(payload) }) },
  }
  handler(req, res)
  // handler 在第一个 await 前已同步注册好监听器，所以这个微任务能安全投喂 body
  queueMicrotask(() => {
    if (body !== undefined) {
      for (const cb of listeners.data || []) cb(Buffer.from(JSON.stringify(body)))
    }
    for (const cb of listeners.end || []) cb()
  })
})

const status = await call('GET', '/api/a-finder/status')
const syncLocal = await call('POST', '/api/a-finder/sync', { push: false, cb: false })
const args = await readArgs()
process.stdout.write(JSON.stringify({ status, syncLocal, args }))
""".replace("__INDEX_JS__", INDEX_JS.as_uri())

    got = _run_node(source, project)

    assert got["status"]["status"] == 200, got
    assert got["status"]["json"] == {
        "ok": True,
        "running": False,
        "latest": "2026-11-12",
        "published": "2026-11-12",
        "cb": "2026-11-12",
    }, got

    assert got["syncLocal"]["status"] == 200, got
    assert got["syncLocal"]["json"]["ok"] is True, got
    assert got["syncLocal"]["json"]["cb"] == "2026-11-12", got
    assert "可转债榜单 2026-11-12" in got["syncLocal"]["json"]["message"], got
    # body.cb=false 必须真的落到命令行上
    assert got["args"] == ["site", "hs300.db", "20", "site", "--no-cb"], got


def test_node_available():
    """node 必须可用（否则上面的测试是假绿）。"""
    _require_node()
    proc = subprocess.run(["node", "--version"], capture_output=True, text=True)
    assert proc.returncode == 0, "node 不可用，无法跑插件契约测试"
