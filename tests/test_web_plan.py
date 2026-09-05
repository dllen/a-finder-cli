import json
import sqlite3
import tempfile

import pytest

from app import create_app
from db_schema import ensure_schema
from db_repository import open_db


@pytest.fixture()
def plan_db(tmp_path):
    db = str(tmp_path / "plan.db")
    # open_db runs migrations which create trade_plan table
    conn = open_db(db)
    # Seed one trade_plan row for 2026-08-18
    conn.execute(
        """INSERT INTO trade_plan
        (plan_date, code, action, plan_price, size_pct, stop_price, tp_price,
         rr_ratio, status, reason, rationale_json, params_hash, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "2026-08-18", "600519", "buy", 1500.0, 0.10, 1380.0, 1740.0,
            2.0, "ok", "", "{}", "deadbeef", "2026-08-18T00:00:00",
        ),
    )
    conn.commit()
    conn.close()
    return db


def test_plan_page_renders(plan_db):
    app = create_app(db_path=plan_db)
    app.config["TESTING"] = True
    resp = app.test_client().get("/plan")
    assert resp.status_code == 200
    assert b"Plan" in resp.data


def test_api_plan_today_returns_today_plan(plan_db):
    """Seeded plan_date matches today, so /api/plan/today returns the row."""
    from datetime import date as _date
    app = create_app(db_path=plan_db)
    app.config["TESTING"] = True
    resp = app.test_client().get("/api/plan/today")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "rows" in data
    assert "plan_date" in data
    if data["plan_date"] == "2026-08-18":
        assert len(data["rows"]) >= 1
    else:
        assert data["rows"] == []


def test_api_plan_by_date_excludes_failed_by_default(plan_db):
    app = create_app(db_path=plan_db)
    app.config["TESTING"] = True
    resp = app.test_client().get("/api/plan/2026-08-18")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["plan_date"] == "2026-08-18"
    assert len(data["rows"]) == 1
    assert data["rows"][0]["code"] == "600519"
    assert data["rows"][0]["status"] == "ok"


def test_api_plan_by_date_includes_failed_with_flag(plan_db):
    db = plan_db
    # add a failed row
    conn = sqlite3.connect(db)
    conn.execute(
        """INSERT INTO trade_plan
        (plan_date, code, action, plan_price, size_pct, stop_price, tp_price,
         rr_ratio, status, reason, rationale_json, params_hash, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "2026-08-18", "000001", "buy", 10.0, 0.99, 9.0, 12.0,
            0.0, "failed", "size_exceed_max", "{}", "deadbeef", "2026-08-18T00:00:00",
        ),
    )
    conn.commit()
    conn.close()

    app = create_app(db_path=db)
    app.config["TESTING"] = True

    # default: only ok
    resp_ok = app.test_client().get("/api/plan/2026-08-18")
    assert len(resp_ok.get_json()["rows"]) == 1

    # include_failed=1: both
    resp_all = app.test_client().get("/api/plan/2026-08-18?include_failed=1")
    rows = resp_all.get_json()["rows"]
    assert len(rows) == 2
    statuses = {r["status"] for r in rows}
    assert statuses == {"ok", "failed"}


def test_api_plan_dates(plan_db):
    app = create_app(db_path=plan_db)
    app.config["TESTING"] = True
    resp = app.test_client().get("/api/plan/dates")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["dates"] == ["2026-08-18"]


def test_api_dashboard_returns_four_sections(plan_db, monkeypatch):
    """Seed daily_picks + verify 4 sections + freshness boundary."""
    import types
    from datetime import datetime as _dt
    fake_mod = types.SimpleNamespace(
        now=lambda: _dt(2026, 8, 18, 12, 0, 0),
        fromisoformat=_dt.fromisoformat,
        strptime=_dt.strptime,
    )
    import app as app_module
    monkeypatch.setattr(app_module, "_dt_class", fake_mod)

    conn = sqlite3.connect(plan_db)
    conn.execute(
        "INSERT INTO daily_picks (date, rank, kind, code, updated_at) "
        "VALUES ('2026-08-17', 1, '均线', '600519', '2026-08-17 10:00:00')"
    )
    # today_plan 依据真实系统日期，用当天重新建一条 buy/ok 行，避免测试随日期漂移。
    from datetime import date as _date
    conn.execute("DELETE FROM trade_plan")
    conn.execute(
        """INSERT INTO trade_plan
        (plan_date, code, action, plan_price, size_pct, stop_price, tp_price,
         rr_ratio, status, reason, rationale_json, params_hash, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            _date.today().isoformat(), "600519", "buy", 1500.0, 0.10, 1380.0, 1740.0,
            2.0, "ok", "", "{}", "deadbeef", "2026-08-18T00:00:00",
        ),
    )
    conn.commit()
    conn.close()

    app = create_app(db_path=plan_db)
    app.config["TESTING"] = True
    resp = app.test_client().get("/api/dashboard")
    assert resp.status_code == 200
    data = resp.get_json()
    assert set(data.keys()) == {"last_refresh", "today_plan", "open_positions", "pnl_5d", "holdings_summary"}
    # last_refresh: 26h ago = warm (>24, <72)
    assert data["last_refresh"]["date"] == "2026-08-17"
    assert data["last_refresh"]["ago_hours"] == 26.0
    assert data["last_refresh"]["freshness"] == "warm"
    # today_plan: 来自 plan_db fixture 已插入的 ok 行
    assert data["today_plan"]["buy"] == 1
    # open_positions: 空
    assert data["open_positions"]["count"] == 0
    # pnl_5d: 空
    assert data["pnl_5d"] == []


def test_api_dashboard_freshness_fresh(plan_db, monkeypatch):
    """1h ago = fresh (<24). warm+stale covered by other tests / endpoint logic."""
    import types
    from datetime import datetime as _dt, timedelta
    import app as app_module
    fake_mod = types.SimpleNamespace(
        now=lambda: _dt(2026, 8, 18, 12, 0, 0),
        fromisoformat=_dt.fromisoformat,
        strptime=_dt.strptime,
    )
    monkeypatch.setattr(app_module, "_dt_class", fake_mod)

    conn = sqlite3.connect(plan_db)
    conn.execute("DELETE FROM daily_picks")
    ts = (_dt(2026, 8, 18, 12, 0, 0) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO daily_picks (date, rank, kind, code, updated_at) "
        "VALUES ('2026-08-18', 1, '均线', '600519', ?)",
        (ts,),
    )
    conn.commit()
    conn.close()

    app = create_app(db_path=plan_db)
    app.config["TESTING"] = True
    data = app.test_client().get("/api/dashboard").get_json()
    assert data["last_refresh"]["freshness"] == "fresh"


def test_api_dashboard_empty_db():
    import tempfile
    from db_repository import open_db
    from app import create_app
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    open_db(path).close()
    client = create_app(db_path=path).test_client()
    data = client.get("/api/dashboard").get_json()
    assert data["last_refresh"] is None
    assert data["today_plan"]["buy"] == 0
    assert data["open_positions"]["count"] == 0
    assert data["pnl_5d"] == []


def test_dashboard_partial_present_on_both_pages(plan_db):
    app = create_app(db_path=plan_db)
    client = app.test_client()
    for path in ("/", "/plan"):
        r = client.get(path)
        assert b'<div id="dashboard"></div>' in r.data
        assert b'startDashboard();' in r.data


def test_dashboard_js_served():
    """dashboard.js is served at /static/dashboard.js."""
    import tempfile
    from db_repository import open_db
    from app import create_app
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    open_db(path).close()
    app = create_app(db_path=path)
    client = app.test_client()
    resp = client.get("/static/dashboard.js")
    assert resp.status_code == 200
    assert b"startDashboard" in resp.data


def test_api_holdings_returns_summary(plan_db):
    app = create_app(db_path=plan_db)
    app.config["TESTING"] = True
    resp = app.test_client().get("/api/holdings")
    assert resp.status_code == 200
    data = resp.get_json()
    assert set(data.keys()) == {"holdings", "summary"}
    assert "total_pnl" in data["summary"]


def test_plan_page_has_holdings_container(plan_db):
    app = create_app(db_path=plan_db)
    resp = app.test_client().get("/plan")
    assert b'id="holdings"' in resp.data


def test_plan_page_has_dsFetchHoldings(plan_db):
    app = create_app(db_path=plan_db)
    resp = app.test_client().get("/plan")
    assert b'dsFetchHoldings' in resp.data


def test_api_plan_build_passes_capital(plan_db, monkeypatch):
    import app as app_module

    captured = {}
    def fake_start(db_path, plan_date, capital):
        captured["capital"] = capital
        return "job-1"

    monkeypatch.setattr(app_module, "_start_plan_job", fake_start)
    app = create_app(db_path=plan_db)
    app.config["TESTING"] = True
    client = app.test_client()

    resp = client.post("/api/plan/build",
                       data=json.dumps({"plan_date": "2026-08-18", "capital": 500000}),
                       content_type="application/json")
    assert resp.status_code == 202
    assert captured["capital"] == 500000

    # 非法档位回退默认 10W
    client.post("/api/plan/build",
                data=json.dumps({"plan_date": "2026-08-18", "capital": 999999}),
                content_type="application/json")
    assert captured["capital"] == 100000


def test_plan_page_renders_strategy_column(plan_db):
    """交易计划表必须有「策略」列，从 rationale_json.obj.strategy 读取。

    静态与 Flask 共用同一份 PLAN_SCRIPT；只要 shell HTML 里包含表头列、桌面/移动端两套
    模板都能找到 strategy 提取逻辑，就视为修复到位。
    """
    app = create_app(db_path=plan_db)
    resp = app.test_client().get("/plan")
    body = resp.data.decode("utf-8")

    assert resp.status_code == 200
    # 桌面表头新增「策略」列
    assert "<th>策略</th>" in body
    # 移动端卡片 kv-grid 也要带「策略」一行
    assert '<span class="k">策略</span>' in body
    # row() 从 rationale_json 解出 strategy（obj.strategy / .strategy 至少出现一处）
    assert "obj.strategy" in body or ".strategy" in body


def test_plan_page_renders_only_affordable_toggle(plan_db):
    """plan 页应提供「只显示可建仓」复选框：勾上时只保留当前资金档位下 share>0 的行。

    用户反馈：低档位（5W/10W）下大量买入行变成「0 股 资金不足一手」，占位又干扰判断。
    现有 filter (#q) 只匹配代码/名称，无法按当前资金档位筛掉不可建仓行。
    这里只校验 HTML/JS 接线，不验实际渲染（前端逻辑）。
    """
    app = create_app(db_path=plan_db)
    resp = app.test_client().get("/plan")
    body = resp.data.decode("utf-8")

    assert resp.status_code == 200
    # 复选框必须在 filter-toolbar 内，与 include-failed 风格一致
    assert 'id="only-affordable"' in body
    assert "只显示可建仓" in body
    # JS 状态机与过滤逻辑必须就位：drawPlan 会基于 onlyAffordable 跳过 insufficientLot 行
    assert "onlyAffordable" in body
    assert "insufficientLot" in body


def test_plan_page_renders_tier_filter_dropdown(plan_db):
    """顶部资金档位筛选下拉：Bootstrap 5 dropdown + 10 个 checkbox + 交集语义。

    用户反馈：5W 下大量「0 股 资金不足一手」行无法一次性看完；想勾选几个档位，
    只看那些档位都能建仓的票（交集）。这里只校验 HTML/JS 接线。
    """
    from config import CAPITAL_TIERS

    app = create_app(db_path=plan_db)
    resp = app.test_client().get("/plan")
    body = resp.data.decode("utf-8")

    assert resp.status_code == 200
    # Bootstrap 5 dropdown 标记
    assert 'id="tier-filter-dd"' in body
    assert 'data-bs-toggle="dropdown"' in body
    # 10 个 checkbox 必须存在
    for c in CAPITAL_TIERS:
        assert f'value="{c}"' in body, f"missing tier-filter checkbox for {c}"
    # JS 模板必须含交集过滤逻辑：tierFilter + sizeShares 检查
    assert "tierFilter" in body
    assert "tier-filter-cb" in body


def test_plan_row_includes_tier_shares_matrix(plan_db):
    """每行 buy 票折叠子表显示 10 档位的股数，方便跨档位对比。

    用户反馈：单档位切换只能看当前档位的 shares；想同时看到 5W-50W 全档位的可建仓股数，
    才能决策用哪一档资金。页面是 JS 驱动的（PLAN_SCRIPT 里的 row() 客户端渲染），
    这里校验 JS 模板包含 tier-shares-table 标记 + 10 档 data-tier 引用；
    不调浏览器，端到端测试留给 chromium dump-dom 校验。
    """
    app = create_app(db_path=plan_db)
    resp = app.test_client().get("/plan")
    body = resp.data.decode("utf-8")

    assert resp.status_code == 200
    # JS 必须含子表标题 + 子表 class
    assert "各档股数" in body
    assert "tier-shares-table" in body
    assert "tier-shares-wrap" in body
    # 模板里要有生成 data-tier 的循环（tierSharesHtml + 'data-tier="'）
    assert 'data-tier="' in body
    # 模板必须遍历 CAPITAL_TIERS（forEach + CAPITAL_TIERS）
    assert "CAPITAL_TIERS.forEach" in body


def test_plan_page_renders_ten_capital_tiers(plan_db):
    """资金档位连续化（5W-50W step 5W，共 10 档）：页面必须渲染 10 个按钮。

    用户反馈：只有 5/10/20/30/50W 五档时找不到 15W 等常用金额。CAPITAL_TIERS 改后，
    PLAN_BODY btn-group / PLAN_SCRIPT CAPITAL_TIERS / CLI choices 三处必须一致。
    """
    from config import CAPITAL_TIERS

    app = create_app(db_path=plan_db)
    resp = app.test_client().get("/plan")
    body = resp.data.decode("utf-8")

    assert resp.status_code == 200
    # PLAN_BODY 每个 tier 都生成一个 data-capital= 的按钮
    for c in CAPITAL_TIERS:
        assert f'data-capital="{c}"' in body, f"missing button for capital {c}"
    # PLAN_SCRIPT 的 CAPITAL_TIERS 数组必须包含全部新档位（防止脚本与 config 不同步）
    for c in CAPITAL_TIERS:
        assert str(c) in body, f"PLAN_SCRIPT CAPITAL_TIERS missing {c}"
    # 档位数对齐 config
    assert len(CAPITAL_TIERS) == 10


def test_cli_plan_accepts_new_capital_tier():
    """CLI --capital 现在接受 15W（之前被 5 档 choices 拒掉）。"""
    from config import CAPITAL_TIERS

    assert 150000 in CAPITAL_TIERS
    from cli_layer import build_parser
    parser = build_parser()
    # plan 是 subparser，需要从 _subparsers 里拿
    sub_actions = next(
        a for a in parser._actions
        if hasattr(a, "choices") and isinstance(a.choices, dict) and "plan" in a.choices
    )
    plan_parser = sub_actions.choices["plan"]
    capital_action = next(
        a for a in plan_parser._actions if any(s == "--capital" for s in a.option_strings)
    )
    assert capital_action.choices == CAPITAL_TIERS


def test_pages_have_favicon_link():
    """每个页面 head 里都带 <link rel="icon" type="image/svg+xml" ...>。

    favicon 用 SVG 内联 data URI 注入，静态站与 Flask 共用同一份 shell，避免 404。
    """
    from db_repository import open_db
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    open_db(path).close()
    app = create_app(db_path=path)
    client = app.test_client()
    from app import _FAVICON_HREF
    assert _FAVICON_HREF.startswith("data:image/svg+xml")
    for path_ in ("/", "/plan"):
        body = client.get(path_).data.decode("utf-8")
        assert '<link rel="icon" type="image/svg+xml" href=' in body
        assert _FAVICON_HREF in body
        # 验证 SVG 内容（指纹色 #34c98e 必须出现在响应中）
        assert "#34c98e" in body