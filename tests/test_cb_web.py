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
        "INSERT INTO cb_positions (code, name, stock_code, stock_name, entry_date, "
        "entry_price, shares, stop_price, tp_price) "
        "VALUES ('113059', '转债A', '601000', '浦发银行', '2026-10-20', 108.5, 50, "
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
