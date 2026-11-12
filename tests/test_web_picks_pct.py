"""每日机会页「涨跌% / 收益%」两列的回归测试（2026-11-12）。

背景（用户报「涨跌都是 0.00%」）：`daily_picks.buy` 就是榜单日收盘价
（`pick_history` 用 `prices[-1]`），而「涨跌%」原来是
`最新收盘 / 买入价 - 1` —— 默认榜单（榜单日 == 最新行情日）整列恒为 0.00%。
现在拆成两列：
  - `day_pct` = 榜单交易日当天涨跌幅（始终有数）
  - `ret_pct` = 自买入以来收益；榜单日 == 最新行情日时返回 None（页面显示 —），
    因为买入价就是当天收盘，还没有「之后」的收盘。
另外 `day_pct` 不能盲信落库的 `pct_change`：2026-11-05 实测整天 300 只全是 0
（备源缺字段），必须退到「收盘/前收盘」自算，否则那天又整列 0.00%。
"""

import json

from app import create_app
from db_repository import open_db


def _make_db(tmp_path, picks, prices):
    """picks: [(date, kind, code, buy)]；prices: [(code, date, close, pct_change)]。"""
    path = str(tmp_path / "picks.db")
    conn = open_db(path)
    for rank, (date, kind, code, buy) in enumerate(picks, start=1):
        conn.execute(
            "INSERT INTO daily_picks "
            "(date, rank, kind, code, name, strategy, buy, stop, target, score) "
            "VALUES (?, ?, ?, ?, '测试', '策略', ?, NULL, NULL, 1.0)",
            (date, rank, kind, code, buy),
        )
    for code, date, close, pct in prices:
        conn.execute(
            "INSERT INTO daily_prices (code, trade_date, close, pct_change) "
            "VALUES (?, ?, ?, ?)",
            (code, date, close, pct),
        )
    conn.commit()
    conn.close()
    return path


def _client(db_path):
    app = create_app(db_path=db_path)
    app.config["TESTING"] = True
    return app.test_client()


def _rows(client, date):
    data = client.get(f"/api/picks?date={date}").get_json()
    return [r for rows in data["groups"].values() for r in rows]


def test_latest_board_ret_pct_is_none_not_zero(tmp_path):
    """默认（最新）榜单：买入价 == 当天收盘 ⇒ 收益无意义，必须是 None 而不是 0.0。"""
    db = _make_db(
        tmp_path,
        picks=[("2026-08-20", "均线", "600519", 100.0)],
        prices=[
            ("600519", "2026-08-19", 100.0, 0.0),
            ("600519", "2026-08-20", 101.0, 1.0),
        ],
    )
    row = _rows(_client(db), "2026-08-20")[0]
    assert row["ret_pct"] is None, (
        "榜单日 == 最新行情日时 ret_pct 必须为 None（页面显示 —），"
        "否则默认视图又是整列 0.00%"
    )
    # 同时必须有真实可看的当日涨跌
    assert row["day_pct"] == 1.0, row


def test_older_board_ret_pct_uses_latest_close(tmp_path):
    """历史榜单：收益% 仍按「最新收盘 / 买入价 - 1」算，行为不变。"""
    db = _make_db(
        tmp_path,
        picks=[("2026-08-19", "均线", "600519", 100.0)],
        prices=[
            ("600519", "2026-08-19", 100.0, 0.5),
            ("600519", "2026-08-20", 110.0, 10.0),
        ],
    )
    row = _rows(_client(db), "2026-08-19")[0]
    assert row["ret_pct"] == 10.0, row        # (110/100-1)*100
    assert row["day_pct"] == 0.5, row         # 榜单日当天涨跌


def test_day_pct_falls_back_when_whole_day_pct_is_zero(tmp_path):
    """整天 pct_change 全是 0 = 备源缺字段（2026-11-05 实测），要用收盘自算。"""
    db = _make_db(
        tmp_path,
        picks=[("2026-08-20", "均线", "600519", 100.0)],
        prices=[
            ("600519", "2026-08-19", 100.0, 0.0),
            ("600519", "2026-08-20", 102.0, 0.0),
        ],
    )
    row = _rows(_client(db), "2026-08-20")[0]
    assert row["day_pct"] == 2.0, (
        "整天全 0 被当成了「全体平盘」—— 应退到收盘/前收盘自算"
    )


def test_day_pct_keeps_stored_value_when_only_some_are_zero(tmp_path):
    """只有个别票 0 是真实平盘，不能触发整天回退。"""
    db = _make_db(
        tmp_path,
        picks=[
            ("2026-08-20", "均线", "600519", 100.0),
            ("2026-08-20", "均线", "000001", 10.0),
        ],
        prices=[
            ("600519", "2026-08-19", 100.0, 0.0),
            ("600519", "2026-08-20", 100.0, 0.0),   # 真实平盘
            ("000001", "2026-08-19", 10.0, 0.0),
            ("000001", "2026-08-20", 10.0, -1.5),
        ],
    )
    got = {r["code"]: r["day_pct"] for r in _rows(_client(db), "2026-08-20")}
    assert got == {"600519": 0.0, "000001": -1.5}, got


def test_static_export_payload_matches_api(tmp_path):
    """静态站 JSON 与 /api/picks 同构：两条读路径不能各算一套。"""
    from export_json import export as export_site

    db = _make_db(
        tmp_path,
        picks=[
            ("2026-08-19", "均线", "600519", 100.0),
            ("2026-08-20", "均线", "600519", 110.0),
        ],
        prices=[
            ("600519", "2026-08-18", 98.0, 0.0),
            ("600519", "2026-08-19", 100.0, 2.04),
            ("600519", "2026-08-20", 110.0, 10.0),
        ],
    )
    out = tmp_path / "site"
    export_site(db, str(out))

    old = json.loads((out / "data" / "picks-2026-08-19.json").read_text("utf-8"))
    row = old["groups"]["均线"][0]
    assert row["day_pct"] == 2.04, row
    assert row["ret_pct"] == 10.0, row            # 最新收盘 110 / 买入 100

    latest = json.loads((out / "data" / "picks-2026-08-20.json").read_text("utf-8"))
    latest_row = latest["groups"]["均线"][0]
    assert latest_row["day_pct"] == 10.0, latest_row
    assert latest_row["ret_pct"] is None, (
        "最新榜单的收益% 应为 None（JSON 里是 null），不能是 0.0"
    )


def test_frontend_declares_and_renders_both_columns():
    """前端两列都要有：picks.js 列定义 + table.js 渲染 + 排序下拉项。"""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    picks_js = (root / "static" / "js" / "picks.js").read_text("utf-8")
    assert "key: 'day_pct'" in picks_js and "label: '涨跌%'" in picks_js, picks_js
    assert "key: 'ret_pct'" in picks_js and "label: '收益%'" in picks_js, picks_js

    table_js = (root / "static" / "js" / "table.js").read_text("utf-8")
    assert "pctCell(r.day_pct)" in table_js, table_js
    assert "pctCell(r.ret_pct)" in table_js, table_js

    toolbar = (root / "templates" / "components" / "filter_toolbar.html").read_text("utf-8")
    assert "day_pct:-1" in toolbar, toolbar
    assert "ret_pct:-1" in toolbar, toolbar
