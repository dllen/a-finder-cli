import sqlite3
import threading
import traceback
import urllib.parse
import uuid
from datetime import datetime as _dt_class

from flask import Flask, jsonify, request, render_template
from markupsafe import escape as _esc_html

from pick_history import run_picks
# 计划/持仓/决策台已下线（2026-11-05）—— 不再需要 CAPITAL_TIERS / DEFAULT_CAPITAL
# plan_rows 去重规则随「组合页」一起下线（2026-11-05）；规则本身仍保留在
# shared_lib/plan_rows.py，将来恢复功能时直接 import 即可


def esc_attr(s: str) -> str:
    """HTML-attr-safe escape (escapes both & < > and " ' for attr context)."""
    return _esc_html(str(s)).replace('"', '"').replace("'", '&#39;')




JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def _start_job(db_path, top, do_sync):
    with JOBS_LOCK:
        if any(j["status"] in ("pending", "running") for j in JOBS.values()):
            return None
        job_id = uuid.uuid4().hex
        JOBS[job_id] = {"status": "pending", "message": "", "progress": 0, "log": []}

    def on_progress(pct, msg):
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if job is None:
                return
            job["progress"] = pct
            job["log"].append(msg)
            if len(job["log"]) > 200:
                job["log"] = job["log"][-200:]

    def work():
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "running"
        try:
            result = run_picks(db_path, top, do_sync, on_progress)
            msg = f"日期 {result.get('date') or '-'}：均线 {result.get('ma') or 0} 条 / 买入信号 {result.get('buy') or 0} 条 / 信号策略 {result.get('signal') or 0} 条 / 多因子 {result.get('multi') or 0} 条"
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if job is not None:
                    job["status"] = "done"
                    job["message"] = msg
                    job["progress"] = 100
        except Exception as e:  # noqa: BLE001
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if job is not None:
                    job["status"] = "error"
                    job["message"] = traceback.format_exc(limit=3)

    threading.Thread(target=work, daemon=True).start()
    return job_id


def open_conn(db_path):
    # ponytail: use open_db so fresh/existing DBs get schema + pending migrations applied.
    # Was raw sqlite3.connect, which broke when the dashboard migration added updated_at.
    from db_repository import open_db
    return open_db(db_path)


def list_dates(conn):
    rows = conn.execute("SELECT DISTINCT date FROM daily_picks ORDER BY date DESC").fetchall()
    return [r[0] for r in rows]


def picks_for_date(conn, date):
    rows = conn.execute(
        "SELECT rank, kind, code, name, strategy, buy, stop, target, score FROM daily_picks WHERE date = ? ORDER BY kind, rank",
        (date,),
    ).fetchall()
    return [
        {"rank": r[0], "kind": r[1], "code": r[2], "name": r[3], "strategy": r[4],
         "buy": r[5], "stop": r[6], "target": r[7], "score": r[8]}
        for r in rows
    ]


def latest_close_map(conn):
    rows = conn.execute(
        "SELECT code, close FROM daily_prices "
        "WHERE (code, trade_date) IN (SELECT code, MAX(trade_date) FROM daily_prices GROUP BY code)"
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def latest_price_date(conn) -> str:
    """库里最后一个行情交易日；没有任何行情时返回空串。"""
    row = conn.execute("SELECT MAX(trade_date) FROM daily_prices").fetchone()
    return row[0] if row and row[0] else ""


def daily_change_map(conn, date):
    """该交易日各股的当日涨跌幅（%），键是 code。

    优先用落库的 pct_change（交易所口径，除权日也是对的）。但**整天全 0** 必须
    当成「字段缺失」而不是「全体平盘」：2026-11-05 实测 300 只 pct_change 全是
    0，而收盘价有涨有跌 —— 备源（新浪）只给 OHLC/成交量，缺字段被静默写成了 0。
    这时改用「收盘 / 前一交易日收盘」自算：宁可极端除权日有一点口径误差，也不能
    把一整天的涨跌在页面上显示成 0.00%（那正是用户会当成数据坏了的形态）。
    """
    rows = conn.execute(
        "SELECT code, close, pct_change FROM daily_prices WHERE trade_date = ?",
        (date,),
    ).fetchall()
    if not rows:
        return {}

    prev_rows = conn.execute(
        "SELECT d.code, d.close, p.close FROM daily_prices d "
        "LEFT JOIN daily_prices p ON p.code = d.code AND p.trade_date = ("
        "  SELECT MAX(trade_date) FROM daily_prices "
        "  WHERE code = d.code AND trade_date < d.trade_date) "
        "WHERE d.trade_date = ?",
        (date,),
    ).fetchall()
    fallback = {
        code: (round((close / prev_close - 1) * 100, 4) if (close and prev_close) else None)
        for code, close, prev_close in prev_rows
    }

    stored = [r[2] for r in rows if r[2] is not None]
    if stored and all(v == 0 for v in stored):
        # 整天全 0 = 字段缺失，不是全体平盘
        return fallback
    # 个别行缺 pct_change 时也回退到自算，别让单行显示「无」
    return {r[0]: (r[2] if r[2] is not None else fallback.get(r[0])) for r in rows}


def create_app(db_path="hs300.db", top=10):
    app = Flask(__name__)

    def _cb_latest_pick_date(conn) -> str:
        """最新有榜单的交易日；无数据返回空串。"""
        row = conn.execute("SELECT MAX(trade_date) FROM cb_picks").fetchone()
        return row[0] if row and row[0] else ""

    @app.get("/")
    def index():
        from datetime import date as _date
        return render_template("picks.html",
            today=_date.today().isoformat(),
            app_mode="api", data_prefix="",
            year=_date.today().year)

    @app.get("/api/dates")
    def dates():
        conn = open_conn(db_path)
        ds = list_dates(conn)
        conn.close()
        return jsonify({"dates": ds})

    @app.get("/api/picks")
    def picks():
        date = request.args.get("date", "")
        conn = open_conn(db_path)
        if not date:
            ds = list_dates(conn)
            date = ds[0] if ds else ""
        groups = {}
        if date:
            lc = latest_close_map(conn)
            latest_date = latest_price_date(conn)
            day_chg = daily_change_map(conn, date)
            for row in picks_for_date(conn, date):
                # 自买入以来收益：榜单日就是最新行情日时，买入价 == 当天收盘，
                # 还没有「之后」的收盘 ⇒ 收益恒等于 0。显示 0.00% 会被误读成数据
                # 坏了，所以置 None（前端显示 —），等下一个交易日再算。
                c = lc.get(row["code"])
                row["ret_pct"] = (
                    round((c / row["buy"] - 1) * 100, 2)
                    if (c and row["buy"] and date < latest_date) else None
                )
                # 当日涨跌：始终有数，默认榜单（最新交易日）也能看出各票当天表现
                dp = day_chg.get(row["code"])
                row["day_pct"] = round(dp, 2) if dp is not None else None
                groups.setdefault(row["kind"], []).append(row)
        conn.close()
        return jsonify({"date": date, "groups": groups})

    @app.post("/api/refresh")
    def refresh():
        body = request.get_json(silent=True) or {}
        do_sync = bool(body.get("sync", False))
        job_id = _start_job(db_path, top, do_sync)
        if job_id is None:
            return jsonify({"error": "已有任务进行中"}), 409
        return jsonify({"job_id": job_id}), 202

    @app.get("/api/jobs/<job_id>")
    def job(job_id):
        with JOBS_LOCK:
            data = JOBS.get(job_id)
        if data is None:
            return jsonify({"error": "任务不存在"}), 404
        return jsonify(data)

    # ---- Plan (paper trading) ----
    from datetime import date as _date
    from db_repository import get_trade_plan_by_date as _get_plan

    @app.get("/api/dashboard")
    def dashboard():
        """顶部卡片：运行状态 + 可转债摘要。

        ⚠️ 2026-11-05 起「交易计划」「持仓组合」已下线（用户要求），
        所以这里**不再**返回 today_plan / open_positions / pnl_5d /
        holdings_summary。原先那四项都读 trade_plan / open_positions /
        trade_events，那三张表已清空（迁移 2026_11_05_remove_plan_features），
        继续查只会返回空壳，徒增误导。

        保留 last_refresh（数据新鲜度，选股页仍需要）与 cb（可转债仍在线）。
        """
        from db_repository import get_last_refresh
        conn = open_conn(db_path)
        try:
            last = get_last_refresh(conn)
        finally:
            conn.close()
        if last:
            try:
                parsed = _dt_class.fromisoformat(last["updated_at"])
            except ValueError:
                parsed = _dt_class.strptime(last["updated_at"], "%Y-%m-%d %H:%M:%S")
            ago = (_dt_class.now() - parsed).total_seconds() / 3600
            last["ago_hours"] = round(ago, 1)
            last["freshness"] = "fresh" if ago < 24 else ("warm" if ago < 72 else "stale")

        # 可转债摘要（只读 DB，不触网）
        from cb_repository import get_cb_positions
        conn2 = open_conn(db_path)
        try:
            cb_date = _cb_latest_pick_date(conn2)
            cb_picks_count = conn2.execute(
                "SELECT COUNT(*) FROM cb_picks WHERE trade_date=?", (cb_date,)
            ).fetchone()[0] if cb_date else 0
            cb_positions_count = len(get_cb_positions(conn2))
        finally:
            conn2.close()

        return jsonify({
            "last_refresh": last,
            "cb": {
                "picks_count": cb_picks_count,
                "positions_count": cb_positions_count,
                "latest_date": cb_date,
            },
        })

    # ------------------------------------------------------------------
    # 可转债（Convertible Bond）
    # ------------------------------------------------------------------

    @app.get("/api/cb/dates")
    def cb_dates():
        conn = open_conn(db_path)
        try:
            rows = conn.execute(
                "SELECT DISTINCT trade_date FROM cb_picks ORDER BY trade_date DESC"
            ).fetchall()
        finally:
            conn.close()
        return jsonify({"dates": [r[0] for r in rows]})

    @app.get("/api/cb/picks")
    def cb_picks():
        from cb_repository import get_cb_picks
        conn = open_conn(db_path)
        try:
            date = request.args.get("date", "") or _cb_latest_pick_date(conn)
            rows = get_cb_picks(conn, date) if date else []
        finally:
            conn.close()
        return jsonify({"date": date, "rows": rows})

    @app.get("/api/cb/positions")
    def cb_positions():
        from cb_repository import get_cb_positions
        conn = open_conn(db_path)
        try:
            rows = get_cb_positions(conn)
        finally:
            conn.close()
        return jsonify({"rows": rows})

    @app.get("/api/cb/alerts")
    def cb_alerts():
        """持仓退出预警。实时抓取行情（仅针对持仓的少数几只）。

        无持仓时直接返回空数组，不触发任何网络请求。
        """
        from cb_repository import get_cb_positions
        from strategies.convertible import evaluate_alerts

        conn = open_conn(db_path)
        try:
            positions = get_cb_positions(conn)
        finally:
            conn.close()

        generated_at = _dt_class.now().strftime("%Y-%m-%dT%H:%M:%S")
        if not positions:
            return jsonify({"generated_at": generated_at, "alerts": []})

        from cb_data_provider import fetch_redeem_status_map, fetch_universe_snapshot
        universe = {r["code"]: r for r in fetch_universe_snapshot()}
        redeem_map = fetch_redeem_status_map()

        quotes = {}
        for code in {p["code"] for p in positions}:
            u = universe.get(code) or {}
            rd = redeem_map.get(code) or {}
            cp = u.get("convert_price")
            sp = u.get("stock_price")
            trig = rd.get("redeem_price")
            redeem_dist = None
            if trig and sp and sp > 0:
                redeem_dist = round((trig / sp - 1.0) * 100.0, 4)
            quotes[code] = {
                "price": u.get("price"),
                "redeem_status": rd.get("redeem_status") or "",
                "redeem_dist": redeem_dist,
                "premium_rate": u.get("premium_rate"),
                "stock_price": sp,
                "convert_price": cp,
            }

        alerts = evaluate_alerts(positions, quotes)
        return jsonify({"generated_at": generated_at, "alerts": alerts})

    @app.get("/api/stats")
    def stats():
        from db_repository import fetch_pick_outcomes
        from evolution.attribution import attribute
        conn = open_conn(db_path)
        try:
            rows = fetch_pick_outcomes(conn, judged_only=True)
        finally:
            conn.close()
        s = attribute(rows)
        strategies = [
            {"strategy": k, "n": v.n, "win_rate": round(v.win_rate * 100, 1),
             "expectancy": round(v.expectancy * 100, 2)}
            for k, v in sorted(s.items(), key=lambda kv: -kv[1].n)
        ]
        monthly = {}
        for r in rows:
            if r.get("win") is None:
                continue
            m = r["date"][:7]
            b = monthly.setdefault(m, {"n": 0, "wins": 0, "ret": 0.0})
            b["n"] += 1
            b["wins"] += int(r["win"])
            b["ret"] += float(r["outcome_pct"] or 0.0)
        monthly_list = [
            {"month": m, "n": b["n"], "win_rate": round(b["wins"] / b["n"] * 100, 1),
             "avg_ret": round(b["ret"] / b["n"] * 100, 2)}
            for m, b in sorted(monthly.items())
        ]
        return jsonify({"strategies": strategies, "monthly": monthly_list})

    @app.get("/cb")
    def cb_page():
        from datetime import date as _date
        return render_template("cb.html",
            today=_date.today().isoformat(),
            app_mode="api", data_prefix="",
            year=_date.today().year)

    return app


def main():
    import argparse
    parser = argparse.ArgumentParser(description="每日机会 Web 服务（Flask）")
    parser.add_argument("--db", type=str, default="hs300.db", help="SQLite 文件路径")
    parser.add_argument("--port", type=int, default=8000, help="监听端口")
    parser.add_argument("--top", type=int, default=10, help="榜单数量")
    args = parser.parse_args()
    app = create_app(db_path=args.db, top=args.top)
    print(f"http://127.0.0.1:{args.port}")
    app.run(host="127.0.0.1", port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
