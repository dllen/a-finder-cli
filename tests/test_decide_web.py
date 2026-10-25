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
        assert "deviations" in snap
        assert "cash" in snap

        html = open(os.path.join(out, "decide.html"), encoding="utf-8").read()
        assert "决策台" in html
        assert "js/decide.js" in html
        assert 'window.APP_MODE="static"' in html
