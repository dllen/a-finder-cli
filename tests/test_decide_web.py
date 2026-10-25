"""10w 决策台：API 路由与页面。"""
import json
import os
import tempfile

import pytest


@pytest.fixture
def plan_db():
    """含「买入信号」榜单的库（决策台数据源）。"""
    from db_repository import open_db
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = open_db(path)
    rows = [
        # rank, code, name, buy, stop, target, score
        (1, "600519", "贵州茅台", 100.0, 95.0, 110.0, 88.0),
        (2, "600036", "招商银行", 40.0, 38.0, 44.0, 75.0),
    ]
    for rank, code, name, buy, stop, target, score in rows:
        conn.execute(
            "INSERT INTO daily_picks (date, rank, kind, code, name, strategy, "
            "buy, stop, target, score, updated_at) "
            "VALUES ('2026-10-25',?, '买入信号',?,?, '买入信号',?,?,?,?, '2026-10-25T00:00:00')",
            (rank, code, name, buy, stop, target, score),
        )
    conn.execute(
        "INSERT INTO hs300_metadata (code, name) VALUES ('600519','贵州茅台')"
    )
    conn.execute(
        "INSERT INTO hs300_metadata (code, name) VALUES ('600036','招商银行')"
    )
    conn.commit()
    conn.close()
    yield path
    os.unlink(path)


@pytest.fixture
def client(plan_db):
    from app import create_app
    app = create_app(db_path=plan_db)
    app.config["TESTING"] = True
    return app.test_client()


class TestDecideApi:

    def test_api_decide_plan_returns_top8_with_sizing(self, client):
        """榜单 → 建仓计划：算股数、按一手取整、给出资金汇总。"""
        resp = client.get("/api/decide/plan?date=2026-10-25")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["date"] == "2026-10-25"
        assert len(data["rows"]) == 2
        codes = [r["code"] for r in data["rows"]]
        assert codes == ["600519", "600036"], "按 rank 排序"

        # 单票目标 = min(10%×10w, 8w/8) = 10000
        assert data["target_amount"] == 10000.0
        assert data["stock_capital"] == 80000.0

        maotai = data["rows"][0]
        assert maotai["plan_price"] == 100.0
        assert maotai["shares"] == 100          # 10000/100 = 100 股
        assert maotai["amount"] == 10000.0
        assert maotai["stop_price"] == 95.0
        assert maotai["tp_price"] == 110.0

        # 600036: 10000/40 = 250 → 一手取整 200 股 × 40 = 8000
        cmb = data["rows"][1]
        assert cmb["shares"] == 200
        assert cmb["amount"] == 8000.0

        assert data["planned_amount"] == 18000.0
        assert data["remaining_stock_cash"] == 80000.0 - 18000.0

    def test_api_decide_plan_reports_skipped_field(self, client):
        """skipped 字段存在（跳过买不起一手的高价股）。"""
        data = client.get("/api/decide/plan?date=2026-10-25").get_json()
        assert isinstance(data["skipped"], list)

    def test_api_decide_positions_crud(self, client):
        resp = client.post("/api/decide/positions", json={
            "code": "600519", "name": "贵州茅台", "shares": 100,
            "entry_price": 1500.0, "stop_price": 1400.0,
        })
        assert resp.status_code == 200

        resp = client.get("/api/decide/positions")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["rows"]) == 1
        assert data["rows"][0]["code"] == "600519"

        resp = client.delete("/api/decide/positions/600519")
        assert resp.status_code == 200
        assert client.get("/api/decide/positions").get_json()["rows"] == []

    def test_api_decide_positions_rejects_invalid(self, client):
        resp = client.post("/api/decide/positions", json={
            "code": "600519", "shares": 0, "entry_price": 100.0,
        })
        assert resp.status_code == 400

    def test_api_decide_comparison(self, client):
        client.post("/api/decide/positions", json={
            "code": "600519", "name": "贵州茅台", "shares": 100,
            "entry_price": 100.0, "stop_price": 95.0,
        })
        resp = client.get("/api/decide/comparison?date=2026-10-25")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "deviations" in data
        assert "stock_used" in data
        assert "cash" in data
        kinds = [d["kind"] for d in data["deviations"]]
        assert "missing_buy" in kinds, "600036 计划买入但未持有"

    def test_api_decide_comparison_empty_db(self):
        from db_repository import open_db
        from app import create_app
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        open_db(path).close()
        app = create_app(db_path=path)
        resp = app.test_client().get("/api/decide/comparison")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["deviations"] == []
        os.unlink(path)


class TestDecidePage:

    def test_decide_page_renders(self, client):
        resp = client.get("/decide")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8")
        assert "决策台" in body
        assert "10w" in body.lower()

    def test_decide_page_no_jquery_no_cdn(self, client):
        body = client.get("/decide").data.decode("utf-8").lower()
        assert "jquery" not in body
        assert "bootcdn" not in body

    def test_decide_page_loads_es6_module(self, client):
        body = client.get("/decide").data.decode("utf-8")
        assert 'type="module"' in body
        assert "js/decide.js" in body

    def test_nav_has_decide_link(self, client):
        body = client.get("/").data.decode("utf-8")
        assert 'data-nav="decide"' in body


class TestDecideExport:

    def test_export_includes_decide_files(self, plan_db, tmp_path):
        from export_json import export
        out = str(tmp_path / "site")
        export(plan_db, out)
        assert os.path.exists(os.path.join(out, "decide.html"))
        data_dir = os.path.join(out, "data")
        assert os.path.exists(os.path.join(data_dir, "decide-positions.json"))
        assert os.path.exists(os.path.join(data_dir, "decide-2026-10-25.json"))

        with open(os.path.join(data_dir, "decide-2026-10-25.json"), encoding="utf-8") as f:
            snap = json.load(f)
        assert snap["date"] == "2026-10-25"
        assert snap["rows"]
        assert "skipped" in snap
        # 对比结果改由前端 decide_compare.js 实时算，快照只含 plan
        assert "deviations" not in snap

        html = open(os.path.join(out, "decide.html"), encoding="utf-8").read()
        assert "决策台" in html
        assert "js/decide.js" in html
        assert 'window.APP_MODE="static"' in html


class TestWorkerIntegrationJs:

    def _js(self, name):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "static", "js", name,
        )
        return open(path, encoding="utf-8").read()

    def test_api_js_has_api_base(self):
        src = self._js("api.js")
        assert "export const API_BASE" in src
        assert "ycu.space" in src

    def test_api_js_has_worker_calls(self):
        src = self._js("api.js")
        for fn in ("fetchDecidePositionsRemote", "postDecidePosition",
                   "deleteDecidePosition", "loginDecide", "logoutDecide",
                   "fetchDecideAuthState", "fetchDecideConfig"):
            assert f"export function {fn}" in src, f"缺少 {fn}"

    def test_decide_js_uses_worker_and_compare(self):
        src = self._js("decide.js")
        assert "decide_compare.js" in src
        assert "computeComparison" in src
        assert "fetchDecidePositionsRemote" in src
        assert "loginDecide" in src
        assert "postDecidePosition" in src
        assert "deleteDecidePosition" in src

    def test_decide_js_no_hardcoded_flask_post(self):
        """写操作不能再用硬编码 /api/decide/positions 的裸 fetch。"""
        src = self._js("decide.js")
        assert "fetch('/api/decide/positions'" not in src

    def test_decide_html_has_login_area(self, client):
        body = client.get("/decide").data.decode("utf-8")
        assert 'id="decide-auth"' in body

    def test_api_decide_config(self, client):
        resp = client.get("/api/decide/config")
        assert resp.status_code == 200
        cfg = resp.get_json()
        assert cfg["total_capital"] == 100000.0
        assert cfg["max_positions"] == 8


class TestDecideConfigExport:

    def test_export_writes_decide_config(self, plan_db, tmp_path):
        from export_json import export
        out = str(tmp_path / "site")
        export(plan_db, out)
        cfg_path = os.path.join(out, "data", "decide-config.json")
        assert os.path.exists(cfg_path)
        cfg = json.load(open(cfg_path, encoding="utf-8"))
        assert cfg["total_capital"] == 100000.0
        assert cfg["max_positions"] == 8
        assert cfg["max_single_weight"] == 0.10

    def test_plan_snapshot_excludes_comparison(self, plan_db, tmp_path):
        """对比改由前端算，快照不再含 deviations。"""
        from export_json import export
        out = str(tmp_path / "site")
        export(plan_db, out)
        snap_path = os.path.join(out, "data", "decide-2026-10-25.json")
        snap = json.load(open(snap_path, encoding="utf-8"))
        assert "deviations" not in snap
        assert "pending_build" not in snap
        assert "rows" in snap
        assert "skipped" in snap


class TestDecideUndefinedGuard:
    """回归：fetchDecidePlan 无参时不能拼出 decide-undefined.json，
    renderSummary 缺字段时不能显示 undefined。"""

    def _js(self, name):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "static", "js", name,
        )
        return open(path, encoding="utf-8").read()

    def test_fetch_plan_reads_dates_when_no_date(self):
        src = self._js("api.js")
        assert "decide-dates.json" in src
        # 静态分支必须先从 dates 取最新，再拼文件名
        seg = src.split("export async function fetchDecidePlan")[1].split("export ")[0]
        assert "decide-dates.json" in seg
        assert "decide-' + d" in seg or "decide-2026" not in seg

    def test_render_summary_has_undefined_guard(self):
        src = self._js("decide.js")
        seg = src.split("function renderSummary")[1].split("function ")[0]
        # 不应出现裸插值 ${c.xxx}
        assert "${c.actual_count}" not in seg
        assert "${c.plan_count}" not in seg
        assert "n(c.actual_count)" in seg
        assert "n(c.plan_count)" in seg

    def test_no_undefined_literal_in_decide_js(self):
        src = self._js("decide.js")
        assert "${c.actual_count}" not in src
        assert "计划 ${c.plan_count}" not in src


class TestSetStatusContainer:

    def _js(self, name):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "static", "js", name,
        )
        return open(path, encoding="utf-8").read()

    def test_dom_setstatus_has_container_param(self):
        src = self._js("dom.js")
        assert "containerId" in src, "setStatus 需支持容器参数"
        assert "containerId = 'status'" in src, "默认值应保持 'status'（向后兼容）"

    def test_decide_html_has_status_div(self, client):
        body = client.get("/decide").data.decode("utf-8")
        assert 'id="decide-status"' in body

    def test_decide_js_uses_decide_status(self):
        src = self._js("decide.js")
        assert "'decide-status'" in src, "decide.js 的 setStatus 应指向 #decide-status"


class TestLoginModal:

    def _js(self, name):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "static", "js", name,
        )
        return open(path, encoding="utf-8").read()

    def test_no_prompt_in_decide_js(self):
        """禁用浏览器原生弹窗。"""
        src = self._js("decide.js")
        assert "prompt(" not in src
        assert "alert(" not in src

    def test_login_modal_fields(self, client):
        body = client.get("/decide").data.decode("utf-8")
        assert 'id="login-modal"' in body
        assert 'id="login-user"' in body
        assert 'id="login-pass"' in body
        assert 'id="login-error"' in body

    def test_login_passes_both_credentials(self):
        src = self._js("decide.js")
        assert "loginDecide(user, password)" in src

    def test_api_login_signature(self):
        src = self._js("api.js")
        assert "export function loginDecide(user, password)" in src
        assert "body: JSON.stringify({ user, password })" in src


class TestRenderAllSplit:

    def _js(self, name):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "static", "js", name,
        )
        return open(path, encoding="utf-8").read()

    def test_render_all_exists(self):
        assert "function renderAll" in self._js("decide.js")

    def test_comparison_hidden_when_empty(self):
        src = self._js("decide.js")
        seg = src.split("function renderAll")[1].split("\nfunction ")[0]
        assert "decide-comparison-wrap" in seg
        assert "style.display" in seg or "classList" in seg

    def test_plan_title_switches(self):
        src = self._js("decide.js")
        assert "待建仓清单" in src
        assert "今日执行清单" in src

    def test_skeleton_loading(self):
        src = self._js("decide.js")
        assert "function showLoading" in src
        assert "skeleton" in src

    def test_summary_responsive_classes(self, client):
        body = client.get("/decide").data.decode("utf-8")
        assert "row-cols-md-3" in body
        assert "row-cols-xl-5" in body


class TestStrategyColumn:

    def _js(self, name):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "static", "js", name,
        )
        return open(path, encoding="utf-8").read()

    def test_plan_table_has_strategy_header(self):
        src = self._js("decide.js")
        assert "<th>策略</th>" in src

    def test_plan_renders_strategy(self):
        src = self._js("decide.js")
        assert "r.strategy" in src

    def test_api_plan_rows_carry_strategy(self, client):
        data = client.get("/api/decide/plan").get_json()
        assert data["rows"], "测试库应有 plan 行"
        assert all("strategy" in r for r in data["rows"])

    def test_export_plan_carries_strategy(self, plan_db, tmp_path):
        from export_json import export
        out = str(tmp_path / "site")
        export(plan_db, out)
        snap = json.load(open(os.path.join(out, "data", "decide-2026-10-25.json"), encoding="utf-8"))
        assert snap["rows"]
        assert all("strategy" in r for r in snap["rows"])


class TestOneClickState:

    def _js(self, name):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "static", "js", name,
        )
        return open(path, encoding="utf-8").read()

    def test_positions_state_var(self):
        assert "let POSITIONS = []" in self._js("decide.js")

    def test_worker_ok_state_var(self):
        assert "let WORKER_OK" in self._js("decide.js")

    def test_held_codes_judgement(self):
        src = self._js("decide.js")
        assert "heldCodeSet" in src
        assert "isHeldRow" in src

    def test_positions_synced_from_loadall(self):
        src = self._js("decide.js")
        assert "POSITIONS = posRes.rows" in src or "POSITIONS = (posRes.rows" in src
