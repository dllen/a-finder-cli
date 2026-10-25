"""可转债 Web 页面：API 路由、模板、静态导出。"""
import json
import os
import tempfile

import pytest


@pytest.fixture
def cb_db():
    """带转债数据的临时库。"""
    from db_repository import open_db
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = open_db(path)
    conn.execute(
        "INSERT INTO cb_picks (trade_date, rank, code, name, price, total_score, "
        "a_score, b_score, c_score, size_pct, shares, stop_price, tp_price, reason) "
        "VALUES ('2026-10-25', 1, '113046', '金田转债', 101.17, 83.1, 79, 85, 87, "
        "0.15, 140, 93.08, 121.4, '债底保护充分')"
    )
    conn.execute(
        "INSERT INTO cb_positions (code, name, stock_code, entry_date, "
        "entry_price, shares, stop_price, tp_price) "
        "VALUES ('113059', '转债A', '601000', '2026-10-20', 108.5, 50, "
        "99.82, 130.2)"
    )
    conn.commit()
    conn.close()
    yield path
    os.unlink(path)


@pytest.fixture
def client(cb_db):
    from app import create_app
    app = create_app(db_path=cb_db)
    app.config["TESTING"] = True
    return app.test_client()


class TestCbApiJs:

    def test_api_js_has_cb_fetchers(self):
        """static/js/api.js 需提供 4 个转债取数函数（API + 静态双模式）。"""
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "static", "js", "api.js",
        )
        src = open(path, encoding="utf-8").read()
        for fn in ("fetchCbPicks", "fetchCbPositions", "fetchCbAlerts", "fetchCbDates"):
            assert f"export function {fn}" in src, f"缺少 {fn}"
        # 静态模式分支
        assert "cb-picks-" in src
        assert "cb-positions.json" in src
        assert "cb-alerts.json" in src
        assert "cb-dates.json" in src


class TestCbApi:

    def test_api_cb_picks_returns_rows(self, client):
        resp = client.get("/api/cb/picks?date=2026-10-25")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["date"] == "2026-10-25"
        assert len(data["rows"]) == 1
        row = data["rows"][0]
        assert row["code"] == "113046"
        assert row["name"] == "金田转债"
        assert row["total_score"] == 83.1
        assert row["a_score"] == 79
        assert row["b_score"] == 85
        assert row["c_score"] == 87
        assert row["shares"] == 140
        assert row["reason"] == "债底保护充分"

    def test_api_cb_picks_defaults_to_latest_date(self, client):
        resp = client.get("/api/cb/picks")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["date"] == "2026-10-25"
        assert len(data["rows"]) == 1

    def test_api_cb_picks_empty_db(self):
        from db_repository import open_db
        from app import create_app
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        open_db(path).close()
        app = create_app(db_path=path)
        resp = app.test_client().get("/api/cb/picks")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["rows"] == []
        os.unlink(path)

    def test_api_cb_positions_returns_rows(self, client):
        resp = client.get("/api/cb/positions")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["rows"]) == 1
        row = data["rows"][0]
        assert row["code"] == "113059"
        assert row["shares"] == 50
        assert row["entry_price"] == 108.5
        assert row["stock_code"] == "601000"

    def test_api_cb_dates(self, client):
        resp = client.get("/api/cb/dates")
        assert resp.status_code == 200
        assert resp.get_json()["dates"] == ["2026-10-25"]

    def test_api_cb_alerts_no_positions_no_network(self, monkeypatch):
        """无持仓 → 空数组，且不触发任何网络抓取。"""
        import cb_data_provider
        from db_repository import open_db
        from app import create_app

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        open_db(path).close()
        app = create_app(db_path=path)

        def boom(*a, **kw):
            raise AssertionError("无持仓时不应抓网络")

        monkeypatch.setattr(cb_data_provider, "fetch_universe_snapshot", boom)
        monkeypatch.setattr(cb_data_provider, "fetch_redeem_status_map", boom)
        resp = app.test_client().get("/api/cb/alerts")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["alerts"] == []
        assert "generated_at" in data
        os.unlink(path)

    def test_api_cb_alerts_with_position(self, client, monkeypatch):
        """有持仓 → 实时抓取行情并返回预警。"""
        import cb_data_provider

        monkeypatch.setattr(
            cb_data_provider, "fetch_universe_snapshot",
            lambda: [{"code": "113059", "price": 120.0, "stock_price": 50.0,
                      "convert_price": 40.0, "premium_rate": 10.0}],
        )
        monkeypatch.setattr(
            cb_data_provider, "fetch_redeem_status_map",
            lambda: {"113059": {"redeem_status": "已公告强赎", "redeem_price": 130.0}},
        )
        resp = client.get("/api/cb/alerts")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["alerts"]) == 1
        a = data["alerts"][0]
        assert a["code"] == "113059"
        assert a["level"] == "emergency"


class TestCbTemplates:

    def test_cb_page_renders(self, client):
        resp = client.get("/cb")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8")
        assert "可转债" in body
        assert "转债榜单" in body
        assert "持仓" in body
        assert "预警" in body

    def test_cb_page_no_jquery_no_cdn(self, client):
        body = client.get("/cb").data.decode("utf-8").lower()
        assert "jquery" not in body
        assert "bootcdn" not in body
        assert "cdn.jsdelivr" not in body

    def test_cb_page_loads_es6_module(self, client):
        body = client.get("/cb").data.decode("utf-8")
        assert 'type="module"' in body
        assert "js/cb.js" in body

    def test_nav_has_cb_link(self, client):
        body = client.get("/").data.decode("utf-8")
        assert 'data-nav="cb"' in body
        assert "可转债" in body

    def test_dashboard_includes_cb_summary(self, client):
        resp = client.get("/api/dashboard")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "cb" in data
        cb = data["cb"]
        assert cb["picks_count"] == 1
        assert cb["positions_count"] == 1
        assert cb["latest_date"] == "2026-10-25"


class TestCbJs:

    def _js(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "static", "js", "cb.js",
        )
        return open(path, encoding="utf-8").read()

    def test_cb_js_exists_and_is_module(self):
        src = self._js()
        assert "import" in src
        assert "export function" in src

    def test_cb_js_imports_cb_fetchers(self):
        src = self._js()
        for fn in ("fetchCbPicks", "fetchCbPositions", "fetchCbAlerts", "fetchCbDates"):
            assert fn in src, f"未使用 {fn}"

    def test_cb_js_has_no_jquery(self):
        src = self._js()
        # jQuery 专属写法不应出现；$ 是 dom.js 导出的 getElementById 别名，不算 jQuery
        assert "$.getJSON" not in src
        assert ".html(" not in src
        assert "jQuery" not in src
        assert "$.ajax" not in src

    def test_cb_js_renders_three_sections(self):
        src = self._js()
        assert "cb-summary" in src
        assert "cb-alerts" in src
        assert "cb-positions" in src
        assert "cb-board" in src


class TestCbExport:

    def test_export_includes_cb_files(self, cb_db, tmp_path):
        from export_json import export
        out = str(tmp_path / "site")
        export(cb_db, out)
        data_dir = os.path.join(out, "data")
        assert os.path.exists(os.path.join(data_dir, "cb-dates.json"))
        assert os.path.exists(os.path.join(data_dir, "cb-picks-2026-10-25.json"))
        assert os.path.exists(os.path.join(data_dir, "cb-positions.json"))
        assert os.path.exists(os.path.join(data_dir, "cb-alerts.json"))

        with open(os.path.join(data_dir, "cb-picks-2026-10-25.json"), encoding="utf-8") as f:
            picks = json.load(f)
        assert picks["date"] == "2026-10-25"
        assert picks["rows"][0]["code"] == "113046"

        with open(os.path.join(data_dir, "cb-positions.json"), encoding="utf-8") as f:
            pos = json.load(f)
        assert pos["rows"][0]["code"] == "113059"

    def test_export_writes_cb_html(self, cb_db, tmp_path):
        from export_json import export
        out = str(tmp_path / "site")
        export(cb_db, out)
        html_path = os.path.join(out, "cb.html")
        assert os.path.exists(html_path)
        body = open(html_path, encoding="utf-8").read()
        assert "可转债" in body
        assert "js/cb.js" in body
        assert 'window.APP_MODE="static"' in body
