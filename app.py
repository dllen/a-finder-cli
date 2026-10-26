import sqlite3
import threading
import traceback
import urllib.parse
import uuid
from datetime import datetime as _dt_class

from flask import Flask, jsonify, request, render_template
from markupsafe import escape as _esc_html

from pick_history import run_picks
from config import CAPITAL_TIERS, DEFAULT_CAPITAL


def esc_attr(s: str) -> str:
    """HTML-attr-safe escape (escapes both & < > and " ' for attr context)."""
    return _esc_html(str(s)).replace('"', '"').replace("'", '&#39;')


_TIER_FILTER_OPTIONS = "".join(
    f'<li><label class="dropdown-item"><input type="checkbox" class="form-check-input me-1 tier-filter-cb" value="{c}"> {c//10000}W</label></li>'
    for c in CAPITAL_TIERS
)


JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def _start_plan_job(db_path, plan_date, capital):
    from config import MAX_SINGLE, MAX_TOTAL, RR_TARGET, SLIPPAGE
    from plan_builder import build_plan

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
            on_progress(10, f"读取 {plan_date} 的 picks / open positions")
            params = {
                "max_single": MAX_SINGLE,
                "max_total": MAX_TOTAL,
                "rr_target": RR_TARGET,
                "regime": "sideways",
                "capital": capital,
            }
            on_progress(40, f"参数：{params} slippage={SLIPPAGE}")
            result = build_plan(plan_date, db_path, params, slippage=SLIPPAGE)
            on_progress(95, f"picks={result.num_picks} open={result.num_open_positions}")
            msg = (
                f"plan_date={plan_date} picks={result.num_picks} "
                f"open={result.num_open_positions} sanity={result.sanity_passed}"
            )
            if result.sanity_reasons:
                msg += f" reasons={result.sanity_reasons}"
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

    @app.get("/plan")
    def plan_page():
        from datetime import date as _date
        return render_template("plan.html",
            today=_date.today().isoformat(),
            capital_tiers=CAPITAL_TIERS,
            tier_filter_options=_TIER_FILTER_OPTIONS,
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
            for row in picks_for_date(conn, date):
                c = lc.get(row["code"])
                row["ret_pct"] = round((c / row["buy"] - 1) * 100, 2) if (c and row["buy"]) else None
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

    @app.post("/api/plan/build")
    def plan_build():
        body = request.get_json(silent=True) or {}
        plan_date = (body.get("plan_date") or "").strip()
        if not plan_date:
            return jsonify({"error": "plan_date 必填"}), 400
        capital = body.get("capital")
        try:
            capital = int(capital)
        except (TypeError, ValueError):
            capital = DEFAULT_CAPITAL
        if capital not in CAPITAL_TIERS:
            capital = DEFAULT_CAPITAL
        job_id = _start_plan_job(db_path, plan_date, capital)
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

    @app.get("/api/plan/dates")
    def plan_dates():
        conn = open_conn(db_path)
        cur = conn.execute(
            "SELECT DISTINCT plan_date FROM trade_plan ORDER BY plan_date DESC"
        )
        ds = [r[0] for r in cur.fetchall()]
        conn.close()
        return jsonify({"dates": ds})

    @app.get("/api/plan/today")
    def plan_today():
        conn = open_conn(db_path)
        rows = _get_plan(conn, _date.today().isoformat())
        conn.close()
        return jsonify({"plan_date": _date.today().isoformat(), "rows": rows})

    @app.get("/api/plan/<plan_date>")
    def plan_by_date(plan_date):
        include_failed = request.args.get("include_failed", "0") == "1"
        conn = open_conn(db_path)
        rows = _get_plan(conn, plan_date, include_failed=include_failed)
        conn.close()
        return jsonify({"plan_date": plan_date, "rows": rows})

    @app.get("/api/holdings")
    def holdings():
        from db_repository import get_holdings_detail
        conn = open_conn(db_path)
        try:
            return jsonify(get_holdings_detail(conn))
        finally:
            conn.close()

    @app.get("/api/dashboard")
    def dashboard():
        from datetime import date as _date
        from db_repository import (
            get_last_refresh,
            get_today_plan_summary,
            get_open_positions_with_unrealized,
            get_recent_pnl,
            get_holdings_detail,
        )
        conn = open_conn(db_path)
        try:
            last = get_last_refresh(conn)
            today = get_today_plan_summary(conn, _date.today().isoformat())
            opens = get_open_positions_with_unrealized(conn)
            pnl = get_recent_pnl(conn, days=5)
            hd = get_holdings_detail(conn)
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
            "today_plan": today,
            "open_positions": opens,
            "pnl_5d": pnl,
            "holdings_summary": hd["summary"],
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

    # ------------------------------------------------------------------
    # 10w 决策台
    # ------------------------------------------------------------------

    def _decide_latest_plan_date(conn):
        """最新有「买入信号」榜单的交易日（决策台数据源，报告第 3 章结论）。"""
        row = conn.execute(
            "SELECT MAX(date) FROM daily_picks WHERE kind='买入信号'"
        ).fetchone()
        return row[0] if row and row[0] else ""

    def _decide_plan_rows(conn, date):
        """当日「买入信号」榜单 → 10w 建仓计划（top 8，跳过高价买不起一手的）。

        不用 trade_plan 的 10W 档位——那是多策略（动量突破/回调买入/震荡回踩/
        高股息）叠加的 23-26 只，合计超本金 2.5 倍，不能直接建仓。
        """
        from decide_service import build_plan_from_picks

        picks = [
            dict(zip(["rank", "code", "name", "buy", "stop", "target", "score", "strategy"], r))
            for r in conn.execute(
                "SELECT rank, code, name, buy, stop, target, score, strategy "
                "FROM daily_picks WHERE kind='买入信号' AND date=? ORDER BY rank",
                (date,),
            )
        ]
        return build_plan_from_picks(picks)

    def _decide_latest_prices(conn):
        """每只票的最新收盘价，用于止损/止盈判定。"""
        out = {}
        for code, close in conn.execute(
            "SELECT d.code, d.close FROM daily_prices d "
            "JOIN (SELECT code, MAX(trade_date) AS md FROM daily_prices GROUP BY code) x "
            "ON d.code = x.code AND d.trade_date = x.md"
        ):
            if close is not None:
                out[code] = float(close)
        return out

    @app.get("/api/decide/config")
    def decide_config():
        from config import PORTFOLIO_10W
        return jsonify(PORTFOLIO_10W)

    @app.get("/api/decide/plan")
    def decide_plan():
        conn = open_conn(db_path)
        try:
            date = request.args.get("date", "") or _decide_latest_plan_date(conn)
            built = _decide_plan_rows(conn, date) if date else {
                "rows": [], "skipped": [], "target_amount": 0.0,
                "planned_amount": 0.0, "stock_capital": 0.0,
                "remaining_stock_cash": 0.0,
            }
            dates = [
                r[0] for r in conn.execute(
                    "SELECT DISTINCT date FROM daily_picks WHERE kind='买入信号' "
                    "ORDER BY date DESC"
                ).fetchall()
            ]
        finally:
            conn.close()
        return jsonify({
            "date": date,
            "dates": dates,
            "rows": built["rows"],
            "skipped": built["skipped"],
            "target_amount": built["target_amount"],
            "planned_amount": built["planned_amount"],
            "stock_capital": built["stock_capital"],
            "remaining_stock_cash": built["remaining_stock_cash"],
        })

    @app.get("/api/decide/positions")
    def decide_positions():
        from real_position_repository import get_real_cb_positions, get_real_positions
        conn = open_conn(db_path)
        try:
            rows = get_real_positions(conn)
            cb_rows = get_real_cb_positions(conn)
        finally:
            conn.close()
        return jsonify({"rows": rows, "cb_rows": cb_rows})

    @app.post("/api/decide/positions")
    def decide_positions_add():
        from real_position_repository import upsert_real_cb_position, upsert_real_position
        payload = request.get_json(silent=True) or {}
        is_cb = bool(payload.get("is_cb"))
        conn = open_conn(db_path)
        try:
            if is_cb:
                upsert_real_cb_position(conn, payload)
            else:
                upsert_real_position(conn, payload)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        finally:
            conn.close()
        return jsonify({"ok": True})

    @app.delete("/api/decide/positions/<code>")
    def decide_positions_delete(code):
        from real_position_repository import delete_real_cb_position, delete_real_position
        conn = open_conn(db_path)
        try:
            if request.args.get("cb") == "1":
                delete_real_cb_position(conn, code)
            else:
                delete_real_position(conn, code)
        finally:
            conn.close()
        return jsonify({"ok": True})

    @app.get("/api/decide/comparison")
    def decide_comparison():
        from decide_service import compare_plan_vs_actual
        from real_position_repository import get_real_cb_positions, get_real_positions
        conn = open_conn(db_path)
        try:
            date = request.args.get("date", "") or _decide_latest_plan_date(conn)
            built = _decide_plan_rows(conn, date) if date else {"rows": [], "skipped": []}
            actual_rows = get_real_positions(conn)
            cb_rows = get_real_cb_positions(conn)
            prices = _decide_latest_prices(conn)
        finally:
            conn.close()
        result = compare_plan_vs_actual(built["rows"], actual_rows, cb_rows, prices)
        result["date"] = date
        result["skipped"] = built["skipped"]
        result["target_amount"] = built.get("target_amount", 0.0)
        result["planned_amount"] = built.get("planned_amount", 0.0)
        result["stock_capital"] = built.get("stock_capital", 0.0)
        result["remaining_stock_cash"] = built.get("remaining_stock_cash", 0.0)
        return jsonify(result)

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

    # ---- Portfolio tiers ----
    from config import CAPITAL_TIERS
    from db_repository import compute_portfolio_pnl, get_open_positions_with_unrealized

    @app.get("/portfolio")
    def portfolio_page():
        from datetime import date as _date
        today = _date.today().isoformat()
        conn = open_conn(db_path)
        try:
            strategies = [r[0] for r in conn.execute(
                "SELECT DISTINCT json_extract(rationale_json, '$.strategy') AS s "
                "FROM trade_plan WHERE action='buy' AND s IS NOT NULL "
                "ORDER BY s"
            ).fetchall()]
        finally:
            conn.close()
        options_html = "".join(
            f'        <option value="{esc_attr(s)}">{esc_attr(s)}</option>\n'
            for s in strategies
        )
        return render_template("portfolio.html",
            today=today,
            strategy_options=options_html,
            app_mode="api", data_prefix="",
            year=_date.today().year)

    @app.get("/cb")
    def cb_page():
        from datetime import date as _date
        return render_template("cb.html",
            today=_date.today().isoformat(),
            app_mode="api", data_prefix="",
            year=_date.today().year)

    @app.get("/decide")
    def decide_page():
        from datetime import date as _date
        from config import PORTFOLIO_10W
        return render_template("decide.html",
            today=_date.today().isoformat(),
            config=PORTFOLIO_10W,
            app_mode="api", data_prefix="",
            year=_date.today().year)

    def _portfolio_label_to_capital(label: str):
        """'10W' → 100000；None if label doesn't match a known tier."""
        for c in CAPITAL_TIERS:
            if f"{c // 10000}W" == label:
                return c
        return None

    @app.get("/api/portfolio/dates")
    def api_portfolio_dates():
        """档位组合（10W…50W）实际有 trade_plan 行的日期，倒序。

        注意：`default` 档不是页面展示的档位组合，必须排除——否则日期筛选器
        会列出只有 default 行的日期，选中后「当日计划」恒为空。
        """
        conn = open_conn(db_path)
        try:
            dates = [r[0] for r in conn.execute(
                "SELECT DISTINCT plan_date FROM trade_plan "
                "WHERE portfolio IS NOT NULL AND portfolio != 'default' "
                "ORDER BY plan_date DESC"
            ).fetchall()]
        finally:
            conn.close()
        return jsonify({"dates": dates})

    @app.get("/api/portfolio/summary")
    def api_portfolio_summary():
        date = request.args.get("date", "")
        as_of = date or None
        conn = open_conn(db_path)
        try:
            tiers = []
            for capital in CAPITAL_TIERS:
                label = f"{capital // 10000}W"
                pnl = compute_portfolio_pnl(
                    conn, label, initial_capital=capital, as_of=as_of
                )
                if as_of:
                    last = conn.execute(
                        "SELECT MAX(plan_date) FROM trade_plan "
                        "WHERE portfolio=? AND plan_date<=?",
                        (label, as_of),
                    ).fetchone()[0]
                else:
                    last = conn.execute(
                        "SELECT MAX(plan_date) FROM trade_plan WHERE portfolio=?",
                        (label,),
                    ).fetchone()[0]
                tiers.append({
                    "label": label,
                    "capital": capital,
                    "cash_remaining": pnl["cash_remaining"],
                    "position_value": pnl["position_value"],
                    "total_value": pnl["total_value"],
                    "realized_pnl": pnl["realized_pnl"],
                    "unrealized_pnl": pnl["unrealized_pnl"],
                    "return_rate": pnl["return_rate"],
                    "last_plan_date": last,
                })
        finally:
            conn.close()
        return jsonify({"date": date, "as_of": as_of, "tiers": tiers})

    @app.get("/api/portfolio/<label>")
    def api_portfolio_detail(label):
        capital = _portfolio_label_to_capital(label)
        if capital is None:
            return jsonify({"error": f"unknown portfolio: {label}"}), 404
        date = request.args.get("date", "")
        strategy = request.args.get("strategy", "")
        as_of = date or None
        conn = open_conn(db_path)
        try:
            pnl = compute_portfolio_pnl(
                conn, label, initial_capital=capital, as_of=as_of
            )
            holdings = get_open_positions_with_unrealized(
                conn, portfolio=label, as_of=as_of
            )
            if strategy:
                codes = [r[0] for r in conn.execute(
                    "SELECT DISTINCT code FROM trade_plan "
                    "WHERE portfolio=? AND JSON_EXTRACT(rationale_json, '$.strategy') = ?",
                    (label, strategy),
                ).fetchall()]
                holdings["items"] = [it for it in holdings["items"] if it["code"] in set(codes)]
                holdings["count"] = len(holdings["items"])
            plan_rows = []
            if date:
                sql = (
                    "SELECT tp.*, m.name AS name FROM trade_plan tp "
                    "LEFT JOIN hs300_metadata m ON m.code = tp.code "
                    "WHERE tp.plan_date=? AND tp.portfolio=?"
                )
                params = [date, label]
                if strategy:
                    sql += " AND JSON_EXTRACT(tp.rationale_json, '$.strategy') = ?"
                    params.append(strategy)
                sql += " ORDER BY tp.action DESC, tp.code"
                cur = conn.execute(sql, tuple(params))
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
                # 回补第二轮按 strategy 写了重复的 (code, action) 行（价格/仓位/止损止盈
                # 完全相同，仅 strategy 标签不同），当日计划只保留每个 (code, action) 一行。
                seen = set()
                plan_rows = []
                for r in rows:
                    key = (r.get("code"), r.get("action"))
                    if key not in seen:
                        seen.add(key)
                        plan_rows.append(r)
            last = conn.execute(
                "SELECT MAX(plan_date) FROM trade_plan WHERE portfolio=?",
                (label,),
            ).fetchone()[0]
        finally:
            conn.close()
        return jsonify({
            "label": label,
            "capital": capital,
            "date": date,
            "pnl": pnl,
            "holdings": holdings,
            "plan_rows": plan_rows,
            "last_plan_date": last,
        })

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
