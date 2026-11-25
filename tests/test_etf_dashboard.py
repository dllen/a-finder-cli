"""ETF 资金看板：抓取器解析、Flask 路由、静态导出、页面渲染的回归测试。

背景（2026-11-24）：把「重点 ETF 资金看板」（jinke.app.workbuddy.host）
复刻进本项目。抓取器 scripts/fetch_etf_dashboard.py 负责把外部看板的
数据层抓下来落成 data/etf/*.json，页面 templates/etf.html + 
static/js/etf-dashboard.js 负责渲染。

这些测试锁住几个**曾经真的踩过**的坑：
  ① 网关要求每个请求都带 ?sharecode=，缺了就是 403（含 POST /api/verify）；
  ② 网关发**两个** Set-Cookie，headers.get() 只返回第一个 ⇒ 取不到 etf_auth；
  ③ Cookie 头要写 "etf_auth=<值>"，光给值是不合法的 Cookie 头；
  ④ 主看板的 data.js 键名不带引号、末行多一个右括号 ⇒ 不能整体 JSON.parse；
  ⑤ 页面 CSS 没被 base.html 引入 ⇒ 图表 canvas 高度为 0（图表全空白）；
  ⑥ 静态站也要有 etf.html 与 data/etf/*.json，否则线上是 404。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# 抓取器：URL / cookie / 解析
# ---------------------------------------------------------------------------

def _load_fetcher():
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import fetch_etf_dashboard as mod
        return mod
    finally:
        sys.path.pop(0)


class TestFetcherRequestShape:
    """请求形状 —— ①②③ 三个坑。"""

    def test_sharecode_is_appended_to_every_url(self):
        mod = _load_fetcher()
        url = mod._with_sharecode(mod.BASE_URL + "/data.js")
        assert "sharecode=" in url, "网关要求每个请求都带 sharecode，缺了就是 403"
        assert url.endswith("?sharecode=" + mod.SHARECODE)

    def test_sharecode_uses_ampersand_when_query_exists(self):
        mod = _load_fetcher()
        url = mod._with_sharecode(mod.BASE_URL + "/api/x?a=1")
        assert url.endswith("&sharecode=" + mod.SHARECODE)
        assert "?" in url and url.count("sharecode=") == 1

    def test_cookie_header_includes_cookie_name(self):
        """Cookie 头必须写成 'etf_auth=<值>'，光给值网关不认。"""
        mod = _load_fetcher()
        captured = {}

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"ok":true}'

        def _fake_open(req, timeout=None):
            captured["cookie"] = req.headers.get("Cookie")
            captured["url"] = req.full_url
            return _Resp()

        import urllib.request
        orig = urllib.request.urlopen
        urllib.request.urlopen = _fake_open
        try:
            mod._request(mod.BASE_URL + "/data.js", cookie="abc123")
        finally:
            urllib.request.urlopen = orig

        assert captured["cookie"] == "etf_auth=abc123", captured
        assert "sharecode=" in captured["url"], captured

    def test_login_reads_second_set_cookie(self):
        """网关发两个 Set-Cookie，headers.get() 只返回第一个。"""
        mod = _load_fetcher()

        class _Resp:
            headers = None

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"ok":true}'

        class _Headers:
            def get_all(self, name):
                assert name == "Set-Cookie"
                return [
                    "__wb_rsc=abc; Path=/; HttpOnly; Secure",
                    "etf_auth=THE_AUTH; Path=/; Max-Age=86400; HttpOnly; Secure",
                ]

            def get(self, name):
                # 只返回第一个 —— 正是原实现漏掉 etf_auth 的原因
                return self.get_all(name)[0]

        _Resp.headers = _Headers()

        import urllib.request
        orig = urllib.request.urlopen
        urllib.request.urlopen = lambda req, timeout=None: _Resp()
        try:
            cookie = mod.login("whatever")
        finally:
            urllib.request.urlopen = orig

        assert cookie == "THE_AUTH", "必须用 get_all() 才能拿到第二个 Set-Cookie"


class TestFetcherParsing:
    """解析 —— ④ 的坑。"""

    def test_parse_islands_handles_unquoted_keys_and_trailing_brace(self):
        mod = _load_fetcher()
        text = (
            "/* 注释 */\n"
            "window.__ISLANDS = {\n"
            '  initData: {"date":"2026-11-23","preClose":3767.23},\n'
            '  cmpData: {"latest":{"date":"2026-11-23"}},\n'
            '  marginData: {"dates":["2026-01-05"]}};\n'
        )
        out = mod.parse_islands(text)
        assert set(out) == {"initData", "cmpData", "marginData"}
        assert out["initData"]["date"] == "2026-11-23"
        # 末行多出来的那个右括号不能留在 JSON 里
        assert out["marginData"] == {"dates": ["2026-01-05"]}

    def test_parse_islands_raises_on_empty(self):
        mod = _load_fetcher()
        with pytest.raises(mod.FetchError):
            mod.parse_islands("window.__ISLANDS = {}\n")

    def test_parse_js_object_reads_assignment(self):
        mod = _load_fetcher()
        out = mod.parse_js_object('window.DASH_DATA = {"index":{"name":"上证"}};', "DASH_DATA")
        assert out["index"]["name"] == "上证"

    def test_to_symbol(self):
        mod = _load_fetcher()
        assert mod._to_symbol("159915.SZ") == "sz159915"
        assert mod._to_symbol("512480.SH") == "sh512480"


# ---------------------------------------------------------------------------
# 抓取器：真实数据文件（如果已经抓过）
# ---------------------------------------------------------------------------

ETF_DIR = ROOT / "data" / "etf"


def _load(name):
    path = ETF_DIR / name
    if not path.exists():
        pytest.skip(f"{name} 尚未抓取（先跑 scripts/fetch_etf_dashboard.py）")
    return json.loads(path.read_text(encoding="utf-8"))


class TestFetchedDataShape:
    def test_islands_has_all_sections(self):
        isl = _load("islands.json")
        for key in (
            "initData", "cmpData", "typData", "dailyData",
            "histData", "postHol", "broadData", "fflowData",
            "cumData", "marginData",
        ):
            assert key in isl, f"缺少 {key}"

    def test_daily_data_dates_are_aligned_with_series(self):
        isl = _load("islands.json")
        d = isl["dailyData"]
        n = len(d["dates"])
        assert n > 100
        assert len(d["amount"]) == n
        assert len(d["netflow"]) == n
        assert len(d["idx"]) == n

    def test_broad_series_lengths_match_dates(self):
        isl = _load("islands.json")
        b = isl["broadData"]
        n = len(b["dates"])
        assert len(b["total"]) == n
        for cat in b["cats"]:
            assert len(b["series"][cat]) == n, cat

    def test_margin_cats_have_all_four_series(self):
        isl = _load("islands.json")
        m = isl["marginData"]
        n = len(m["dates"])
        for cat in m["cats"]:
            for field in ("rzye", "rzjme", "nf", "px"):
                assert len(m[field][cat]) == n, f"{cat}.{field}"

    def test_typ_data_minute_slots_aligned(self):
        isl = _load("islands.json")
        t = isl["typData"]
        n = len(t["slots"])
        for field in ("supAmt", "norAmt", "supP25", "supP75", "norP25", "norP75"):
            assert len(t[field]) == n, field

    def test_themes_have_codes(self):
        themes = _load("themes.json")
        list_ = themes["themes"]
        assert len(list_) >= 5
        for t in list_:
            assert t["key"]
            assert t["codes"], t["key"]

    def test_theme_minutes_cover_every_theme(self):
        themes = _load("themes.json")
        minutes = _load("theme-minutes.json")
        for t in themes["themes"]:
            assert t["key"] in minutes, t["key"]

    def test_meta_has_latest_dates(self):
        meta = _load("meta.json")
        assert meta["latest"]["etf_flow"], meta
        assert meta["latest"]["daily"], meta


# ---------------------------------------------------------------------------
# Flask 路由
# ---------------------------------------------------------------------------

class TestEtfRoutes:
    @pytest.fixture()
    def client(self):
        from app import create_app
        app = create_app(str(ROOT / "hs300.db"), 10)
        app.config.update(TESTING=True)
        return app.test_client()

    def test_etf_page_renders(self, client):
        r = client.get("/etf")
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        assert "重点 ETF 资金看板" in body
        # 五个分区都要在
        for cid in ("chart", "chartCum", "chartTypCase", "chartTypNorm",
                    "chartDAmt", "chartDNf", "chartBroad",
                    "chartMarginBal", "chartMarginFlow",
                    "chartThemeMin", "chartThemeCum"):
            assert f'id="{cid}"' in body, cid

    def test_etf_page_loads_echarts(self, client):
        """ECharts 必须被引入，否则图表全空白（canvas 高度 0）。"""
        body = client.get("/etf").get_data(as_text=True)
        assert "echarts.min.js" in body
        assert "etf-dashboard.js" in body

    def test_etf_css_is_linked_from_base(self, client):
        """CSS 没被 base.html 引入 ⇒ 图表容器没有高度 ⇒ 全空白。"""
        body = client.get("/etf").get_data(as_text=True)
        assert "etf-dashboard.css" in body

    def test_nav_has_etf_entry(self, client):
        body = client.get("/etf").get_data(as_text=True)
        assert 'data-nav="etf"' in body
        assert "ETF资金看板" in body

    @pytest.mark.parametrize(
        "path,key",
        [
            ("/api/etf/islands", "initData"),
            ("/api/etf/themes", "themes"),
            ("/api/etf/cumdays", None),
            ("/api/etf/theme-cum", "themes"),
            ("/api/etf/theme-minutes", None),
        ],
    )
    def test_etf_api_endpoints(self, client, path, key):
        r = client.get(path)
        assert r.status_code == 200, path
        payload = r.get_json()
        assert payload, path
        if key:
            assert key in payload, path

    def test_missing_data_returns_404_not_500(self, tmp_path, monkeypatch):
        """数据没生成时返回 404 + 提示，而不是 500 traceback。"""
        import app as app_mod

        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.setattr(app_mod, "Path", lambda *a, **k: empty / Path(*a[1:]).name)

        app = app_mod.create_app(str(ROOT / "hs300.db"), 10)
        app.config.update(TESTING=True)
        c = app.test_client()
        r = c.get("/api/etf/islands")
        assert r.status_code == 404, r.status_code
        payload = r.get_json()
        assert "error" in payload
        assert "fetch_etf_dashboard" in payload["error"], payload


class TestRouteOrdering:
    """回归：`return app` 之后注册的路由是**死代码**。

    2026-11-24 真的踩过：把 ETF 路由块插到了 `@app.get("/cb")` **之前**，
    而那块末尾带了一个 `return app` ⇒ `/cb` 变成 `return app` 之后的死代码，
    访问 /cb 直接 404（而 /etf 正常 —— 极易误判成「CB 功能坏了」）。
    """

    def test_no_route_is_registered_after_return_app(self):
        src = (ROOT / "app.py").read_text(encoding="utf-8")
        marker = chr(10) + "    return app" + chr(10)
        ret = src.index(marker)
        tail = src[ret + len(marker):]
        assert "@app." not in tail, (
            "app.py 里 `return app` 之后还有路由注册 —— 那些路由不会生效"
        )

    def test_cb_and_etf_both_registered(self):
        from app import create_app
        app = create_app(str(ROOT / "hs300.db"), 10)
        rules = {r.rule for r in app.url_map.iter_rules()}
        for rule in ("/cb", "/etf", "/api/etf/islands"):
            assert rule in rules, rule

class TestThemeIndexLineSelection:
    """第⑤部分价格参照线的三级选择：东财指数 → 新浪指数 → 等权合成。

    2026-11-25 实测：`/api/idxminute` 的时间戳**没有秒**，而 `/api/ktrends` 有；
    且它对某些 secid（如上证 000001）**稳定失败** ⇒ 回落路径必须能走通，
    且两条线的时间戳格式都要能解析。
    """

    @staticmethod
    def _pick():
        """把前端的选择逻辑抽出来跑（保持与 JS 同规则）。"""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "etf_dash", ROOT / "static" / "js" / "etf-dashboard.js")
        return spec

    def test_pick_index_line_prefers_real_index(self):
        """有 idx 数据时选 idx，source=index。"""
        import subprocess
        import json

        js = """
import { readFile } from "node:fs/promises"
const src = await readFile(process.argv[1], "utf8")
// 抽出 pickIndexLine / syntheticIndexLine / FULL_T 三个片段单独求值
// 按大括号配对截出单个函数（比 slice 到下一个函数名可靠）
const grab = (name) => {
  const i = src.indexOf("function " + name)
  if (i < 0) throw new Error("no " + name)
  let depth = 0, started = false, end = -1
  for (let k = i; k < src.length; k++) {
    if (src[k] === "{") { depth++; started = true }
    else if (src[k] === "}") {
      depth--
      if (started && depth === 0) { end = k + 1; break }
    }
  }
  return src.slice(i, end)
}
const pick = grab("pickIndexLine")
const synth = grab("syntheticIndexLine")
const FULL_T = (() => {
  const ts = []
  const segs = [["09:30", 121], ["13:01", 120]]
  segs.forEach(([start, count]) => {
    let hh = parseInt(start.slice(0, 2), 10), mm = parseInt(start.slice(3), 10)
    for (let i = 0; i < count; i++) {
      ts.push(("0" + hh).slice(-2) + ":" + ("0" + mm).slice(-2))
      mm += 1
      if (mm === 60) { mm = 0; hh += 1 }
    }
  })
  return ts
})()
const pickIndexLine = eval("(" + pick + ")")
const syntheticIndexLine = eval("(" + synth + ")")

const idxRows = []
for (let i = 0; i < 60; i++) idxRows.push({ day: "2026-11-25 09:30", close: "1000" + i })
// 造出带分钟的真实形状
// 从 09:30 开始的连续分钟（跨过 11:30/13:00 也无所谓，只需前 N 个落在 FULL_T 内）
const mk = (n, base) => Array.from({length: n}, (_, i) => {
  const total = 9 * 60 + 30 + i
  const hh = String(Math.floor(total / 60)).padStart(2, "0")
  const mm = String(total % 60).padStart(2, "0")
  return { day: "2026-11-25 " + hh + ":" + mm, close: String(base + i) }
})

const out = {}
out.withIdx = pickIndexLine("芯片", { "芯片": { idx: mk(60, 9000), sina: null } }, {})
out.sinaOnly = pickIndexLine("芯片", { "芯片": { idx: null, sina: mk(60, 5000) } }, {})
out.synthetic = pickIndexLine("芯片", { "芯片": { idx: null, sina: null } }, {
  sz159310: { base: 2.0, m: { "09:30": 2.0, "09:31": 2.1 } },
})
out.emptyIdx = pickIndexLine("芯片", { "芯片": { idx: [], sina: null } }, {})
process.stdout.write(JSON.stringify(out))
"""
        import shutil
        if shutil.which("node") is None:
            pytest.skip("没装 node，跳过")
        r = subprocess.run(
            ["node", "--input-type=module", "-e", js,
             str(ROOT / "static" / "js" / "etf-dashboard.js")],
            capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        out = json.loads(r.stdout)

        # 有 idx ⇒ 选 idx
        assert out["withIdx"]["source"] == "index", out["withIdx"]
        assert out["withIdx"]["label"] == "主题指数"
        assert out["withIdx"]["px"][0] is not None

        # 只有 sina ⇒ 选 sina（label 仍是主题指数）
        assert out["sinaOnly"]["source"] == "sina", out["sinaOnly"]

        # 都没有 ⇒ 回落等权合成
        assert out["synthetic"]["source"] == "synthetic", out["synthetic"]
        assert out["synthetic"]["label"] == "主题ETF等权合成"
        # 合成线的值域在 1000 附近（基期 1000）
        vals = [v for v in out["synthetic"]["px"] if v is not None]
        assert vals, out["synthetic"]
        assert 900 < vals[0] < 1100, vals[:3]

        # idx 是空数组 ⇒ 视作没有 ⇒ 也回落
        assert out["emptyIdx"]["source"] == "synthetic", out["emptyIdx"]

    def test_index_rows_with_seconds_and_without_both_parse(self):
        """idxminute 无秒 / ktrends 有秒 —— 两种时间戳都要能取到 HH:MM。"""
        import subprocess
        import json
        import shutil

        if shutil.which("node") is None:
            pytest.skip("没装 node，跳过")

        js = """
import { readFile } from "node:fs/promises"
const src = await readFile(process.argv[1], "utf8")
// 按大括号配对截出单个函数（比 slice 到下一个函数名可靠）
const grab = (name) => {
  const i = src.indexOf("function " + name)
  if (i < 0) throw new Error("no " + name)
  let depth = 0, started = false, end = -1
  for (let k = i; k < src.length; k++) {
    if (src[k] === "{") { depth++; started = true }
    else if (src[k] === "}") {
      depth--
      if (started && depth === 0) { end = k + 1; break }
    }
  }
  return src.slice(i, end)
}
const pick = grab("pickIndexLine")
const FULL_T = (() => { const ts=[]; [["09:30",121],["13:01",120]].forEach(([s,c])=>{let hh=+s.slice(0,2),mm=+s.slice(3);for(let i=0;i<c;i++){ts.push(("0"+hh).slice(-2)+":"+("0"+mm).slice(-2));mm++;if(mm===60){mm=0;hh++}}});return ts})()
const pickIndexLine = eval("(" + pick + ")")
const mk = (n, withSec) => Array.from({length: n}, (_, i) => {
  const total = 9 * 60 + 30 + i
  const hh = String(Math.floor(total / 60)).padStart(2, "0")
  const mm = String(total % 60).padStart(2, "0")
  return { day: "2026-11-25 " + hh + ":" + mm + (withSec ? ":00" : ""), close: String(9000 + i) }
})
const out = {
  noSec: pickIndexLine("x", { x: { idx: mk(60, false) } }, {}),
  withSec: pickIndexLine("x", { x: { idx: mk(60, true) } }, {}),
}
process.stdout.write(JSON.stringify({
  noSecSource: out.noSec.source, noSecFirst: out.noSec.px[0],
  withSecSource: out.withSec.source, withSecFirst: out.withSec.px[0],
}))
"""
        r = subprocess.run(
            ["node", "--input-type=module", "-e", js,
             str(ROOT / "static" / "js" / "etf-dashboard.js")],
            capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        out = json.loads(r.stdout)
        assert out["noSecSource"] == "index", out
        assert out["withSecSource"] == "index", out
        # 两种格式都解析出同一批分钟点（09:30 起）
        assert out["noSecFirst"] == out["withSecFirst"] == 9000, out

class TestFetcherIndexMinutes:
    """抓取器的主题指数分时（2026-11-25 新增）。

    两个实测事实决定了实现：
      ① idxminute **忽略 datalen** —— 传 4/100/242 返回的都是当日全部分时；
         所以抓取器不传 datalen。
      ② idxminute 对某些 secid（000001）**稳定失败** —— 必须能安全回落，
         不能当成异常炸掉整次抓取。
    """

    def test_fetch_index_minutes_grabs_both_sources(self, monkeypatch):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "etf_fetch", ROOT / "scripts" / "fetch_etf_dashboard.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        calls = []

        def fake_fetch_json(cookie, path):
            calls.append(path)
            if path.startswith("/api/idxminute"):
                if "1.000001" in path:
                    # 稳定失败的 secid：上游返回 error JSON
                    return {"error": "upstream unavailable"}
                return [{"day": "2026-11-25 09:30", "close": "9340.95"}] * 12
            if path.startswith("/api/kline"):
                return [{"day": "2026-11-25 09:30:00", "close": "1.0"}] * 12
            return {}

        monkeypatch.setattr(mod, "fetch_json", fake_fetch_json)

        themes = {"themes": [
            {"key": "芯片", "idxSecid": "2.H30184", "sinaSym": "", "idxName": "半导体"},
            {"key": "上证", "idxSecid": "1.000001", "sinaSym": "sh000001", "idxName": "上证"},
        ]}
        out = mod.fetch_index_minutes("cookie", themes)

        # 芯片：idx 成功
        assert out["芯片"]["idx"] is not None
        assert out["芯片"]["sina"] is None, "没配 sinaSym 就不该请求 kline"

        # 上证：idx 失败 ⇒ 回落新浪
        assert out["上证"]["idx"] is None
        assert out["上证"]["sina"] is not None

        # 断言没有传 datalen（idxminute 忽略它，传了是误导）
        idx_calls = [c for c in calls if c.startswith("/api/idxminute")]
        assert idx_calls, calls
        for c in idx_calls:
            assert "datalen" not in c, c

    def test_fetch_index_minutes_survives_raising_endpoint(self, monkeypatch):
        """端点抛 FetchError 时只降级该主题，不炸掉整次抓取。"""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "etf_fetch2", ROOT / "scripts" / "fetch_etf_dashboard.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        def boom(cookie, path):
            raise mod.FetchError("boom")

        monkeypatch.setattr(mod, "fetch_json", boom)
        out = mod.fetch_index_minutes("cookie", {"themes": [
            {"key": "x", "idxSecid": "2.H30184", "sinaSym": "sh000001"},
        ]})
        assert out["x"]["idx"] is None
        assert out["x"]["sina"] is None

# ---------------------------------------------------------------------------
# 静态导出
# ---------------------------------------------------------------------------

class TestEtfStaticExport:
    def test_export_etf_data_copies_files(self, tmp_path):
        """data/etf/*.json 要被拷到静态站的 data/etf/ 下。"""
        import export_json as ex

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        ok = ex._export_etf_data(data_dir)
        if not ok:
            pytest.skip("仓库里没有 data/etf 数据，跳过")
        for name in ("islands.json", "themes.json", "cumdays.json",
                     "theme-cum.json", "theme-minutes.json"):
            assert (data_dir / "etf" / name).exists(), name

    def test_export_etf_data_returns_false_when_absent(self, tmp_path, monkeypatch):
        """data/etf 不存在时不生成页面（免得静态站出现空看板）。"""
        import export_json as ex

        monkeypatch.setattr(ex, "ETF_SOURCE_DIR", tmp_path / "nonexistent")
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        assert ex._export_etf_data(data_dir) is False
        assert not (data_dir / "etf").exists()

    def test_etf_html_is_renderable(self):
        from export_json import _render_static_page
        html = _render_static_page("etf.html", today="2026-11-24", year=2026)
        assert "重点 ETF 资金看板" in html
        assert 'id="chart"' in html
        assert "etf-dashboard.js" in html

    def test_static_nav_points_to_etf_html(self):
        """静态模式下导航必须指向 etf.html（否则 404）。"""
        from export_json import _render_static_page
        html = _render_static_page("etf.html", today="2026-11-24", year=2026)
        assert 'data-nav="etf"' in html


# ---------------------------------------------------------------------------
# 前端资源
# ---------------------------------------------------------------------------

class TestFrontendAssets:
    def test_etf_js_is_valid_syntax(self):
        """前端 JS 必须能被浏览器解析 —— 语法错误会静默让整页无图。"""
        path = ROOT / "static" / "js" / "etf-dashboard.js"
        assert path.exists()
        src = path.read_text(encoding="utf-8")
        node = shutil_which("node")
        if not node:
            pytest.skip("没装 node，跳过语法检查")
        import subprocess
        # 用 node --check 校验（ES module：--input-type=module 走 stdin）
        r = subprocess.run(
            [node, "--input-type=module", "--check"],
            input=src, capture_output=True, text=True,
        )
        assert r.returncode == 0, r.stderr

    def test_echarts_is_vendored(self):
        """ECharts 必须本地托管（项目不依赖 CDN）。"""
        p = ROOT / "static" / "vendor" / "echarts.min.js"
        assert p.exists(), "缺少 static/vendor/echarts.min.js"
        assert p.stat().st_size > 500_000

    def test_etf_css_exists_and_defines_chart_height(self):
        p = ROOT / "static" / "css" / "etf-dashboard.css"
        assert p.exists()
        css = p.read_text(encoding="utf-8")
        assert ".etf-chart" in css
        assert "height:" in css


def shutil_which(name):
    import shutil
    return shutil.which(name)


# ---------------------------------------------------------------------------
# 流水线
# ---------------------------------------------------------------------------

class TestSitePipeline:
    def test_a_finder_sh_has_etf_step(self):
        sh = (ROOT / "a-finder.sh").read_text(encoding="utf-8")
        assert "fetch_etf_dashboard.py" in sh
        assert "--no-etf" in sh

    def test_shell_syntax_valid(self):
        import subprocess
        r = subprocess.run(["bash", "-n", str(ROOT / "a-finder.sh")],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
