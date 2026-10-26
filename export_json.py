"""Export picks / plan / dashboard as static JSON + HTML for Cloudflare Pages.

Usage:
    python export_json.py --db hs300.db --out site

Reads the same DB the Flask app uses, emits:
    site/index.html              每日机会页面（静态模式）
    site/plan.html               交易计划页面（静态模式）
    site/data/dates.json         可选日期列表
    site/data/picks-<date>.json  每日选股（与 /api/picks 同构）
    site/data/plan-<date>.json   每日交易计划（含 failed，与 /api/plan/<date>?include_failed=1 同构）
    site/data/dashboard.json     顶部 dashboard（与 /api/dashboard 同构）
    site/static/*.js             前端脚本副本

Pages load JSON via relative paths, so the whole `site/` dir is static-hostable.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

from db_repository import (
    open_db,
    get_trade_plan_by_date,
    get_last_refresh,
    get_today_plan_summary,
    get_open_positions_with_unrealized,
    get_recent_pnl,
    get_holdings_detail,
    compute_portfolio_pnl,
)
from app import (
    list_dates,
    picks_for_date,
    _dt_class,
)
from config import CAPITAL_TIERS, PORTFOLIO_10W

STATIC_CONFIG = "window.APP_MODE='static'; window.DATA_PREFIX='';"


def _static_version() -> str:
    """静态资源版本号：用 git HEAD 短 sha 做缓存破坏参数。

    Cloudflare 对 static/*.js 下发 max-age=14400（4 小时），部署新代码后
    浏览器/边缘会继续用旧 JS，导致新页面 import 失败（如可转债 fetchCb* 缺失）。
    给 src/href 加 ?v=<sha> 让每次部署 URL 都变，强制重新加载。
    """
    import subprocess
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(Path(__file__).parent),
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        if sha:
            return sha
    except Exception:
        pass
    return _dt_class.now().strftime("%Y%m%d%H%M%S")


def _cache_bust(html: str, version: str) -> str:
    """给 static/ 资源的 src/href 追加 ?v=<version> 缓存破坏参数。"""
    return re.sub(
        r'(src|href)="(static/[^"]+)"',
        rf'\1="\2?v={version}"',
        html,
    )


def _render_static_page(template_name: str, **kwargs) -> str:
    """Render a Jinja2 template in static mode (no url_for, relative paths)."""
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(str(Path(__file__).parent / "templates")))
    # In static mode, url_for doesn't work — provide a simple replacement
    env.globals['url_for'] = lambda endpoint, **kw: f"static/{kw.get('filename', '')}"
    html = env.get_template(template_name).render(
        app_mode="static",
        data_prefix="",
        show_write_controls=False,
        **kwargs,
    )
    return _cache_bust(html, _static_version())


def _cleanup_stale_date_files(data_dir: Path, pick_dates, plan_dates) -> None:
    """删除 site/data 下已不在当前日期集合中的 picks-*/plan-* 陈旧文件。

    export 只写当前日期、不清理历史残留；重导出后旧日期文件会留在原地。
    这里按文件名里的 YYYY-MM-DD 过滤，删掉不在最新日期集合里的。
    """
    pick_set = set(pick_dates)
    plan_set = set(plan_dates)
    for prefix, keep in (("picks", pick_set), ("plan", plan_set)):
        for f in data_dir.glob(f"{prefix}-*.json"):
            date = f.stem[len(prefix) + 1:]  # picks-YYYY-MM-DD -> YYYY-MM-DD
            if len(date) != 10 or date[4] != "-" or date[7] != "-":
                continue  # 跳过 plan-dates.json 等非日期文件
            if date not in keep:
                f.unlink()


def _dashboard_payload(conn) -> dict:
    last = get_last_refresh(conn)
    today = get_today_plan_summary(conn, _dt_class.now().strftime("%Y-%m-%d"))
    opens = get_open_positions_with_unrealized(conn)
    pnl = get_recent_pnl(conn, days=5)
    if last:
        try:
            parsed = _dt_class.fromisoformat(last["updated_at"])
        except ValueError:
            parsed = _dt_class.strptime(last["updated_at"], "%Y-%m-%d %H:%M:%S")
        ago = (_dt_class.now() - parsed).total_seconds() / 3600
        last["ago_hours"] = round(ago, 1)
        last["freshness"] = "fresh" if ago < 24 else ("warm" if ago < 72 else "stale")
    return {
        "last_refresh": last,
        "today_plan": today,
        "open_positions": opens,
        "pnl_5d": pnl,
        "holdings_summary": get_holdings_detail(conn)["summary"],
    }


def _export_stats(conn, data_dir: Path) -> None:
    """导出策略胜率统计（镜像 /api/stats），供静态站「策略胜率统计」区块使用。"""
    from db_repository import fetch_pick_outcomes
    from evolution.attribution import attribute

    rows = fetch_pick_outcomes(conn, judged_only=True)
    s = attribute(rows)
    strategies = [
        {"strategy": k, "n": v.n, "win_rate": round(v.win_rate * 100, 1),
         "expectancy": round(v.expectancy * 100, 2)}
        for k, v in s.items()
    ]
    strategies.sort(key=lambda x: (-x["n"], x["strategy"]))

    monthly: dict = {}
    for r in rows:
        if not r.get("date"):
            continue
        m = str(r["date"])[:7]
        b = monthly.setdefault(m, {"n": 0, "wins": 0, "ret": 0.0})
        b["n"] += 1
        if r.get("win"):
            b["wins"] += 1
        b["ret"] += float(r.get("outcome_pct") or 0.0)
    monthly_list = [
        {"month": m, "n": b["n"],
         "win_rate": round(b["wins"] / b["n"] * 100, 1) if b["n"] else 0.0,
         "avg_ret": round(b["ret"] / b["n"] * 100, 2) if b["n"] else 0.0}
        for m, b in sorted(monthly.items())
    ]
    json.dump(
        {"strategies": strategies, "monthly": monthly_list},
        (data_dir / "stats.json").open("w"),
        ensure_ascii=False, default=str,
    )


def _export_cb_data(conn, data_dir: Path) -> None:
    """导出可转债数据：榜单（按日期）、持仓、预警快照。"""
    from cb_repository import get_cb_positions, get_cb_picks

    cb_dates = [
        r[0] for r in conn.execute(
            "SELECT DISTINCT trade_date FROM cb_picks ORDER BY trade_date DESC"
        ).fetchall()
    ]
    json.dump({"dates": cb_dates}, (data_dir / "cb-dates.json").open("w"), ensure_ascii=False)
    for d in cb_dates:
        rows = get_cb_picks(conn, d)
        json.dump(
            {"date": d, "rows": rows},
            (data_dir / f"cb-picks-{d}.json").open("w"),
            ensure_ascii=False, default=str,
        )

    positions = get_cb_positions(conn)
    json.dump(
        {"rows": positions},
        (data_dir / "cb-positions.json").open("w"),
        ensure_ascii=False, default=str,
    )

    # 预警快照：导出时点抓一次行情（无持仓则不触网）
    generated_at = _dt_class.now().strftime("%Y-%m-%dT%H:%M:%S")
    alerts: list = []
    if positions:
        try:
            from cb_data_provider import fetch_redeem_status_map, fetch_universe_snapshot
            from strategies.convertible import evaluate_alerts

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
        except Exception:
            alerts = []
    json.dump(
        {"generated_at": generated_at, "alerts": alerts},
        (data_dir / "cb-alerts.json").open("w"),
        ensure_ascii=False, default=str,
    )


def _export_decide_data(conn, data_dir: Path) -> None:
    """导出 10w 决策台快照（静态站只读）：建仓计划 + 对照 + 实际持仓。

    数据源是 daily_picks 的「买入信号」榜单（报告第 3 章），不是 trade_plan
    的 10W 档位——后者是多策略叠加的 23-26 只，合计超本金 2.5 倍。
    """
    from decide_service import build_plan_from_picks
    from real_position_repository import get_real_cb_positions, get_real_positions

    dates = [
        r[0] for r in conn.execute(
            "SELECT DISTINCT date FROM daily_picks WHERE kind='买入信号' "
            "ORDER BY date DESC"
        ).fetchall()
    ]
    json.dump({"dates": dates}, (data_dir / "decide-dates.json").open("w"), ensure_ascii=False)

    # 降级用：Worker 不可用时前端仍可只读展示本地 SQLite 的持仓快照
    actual_rows = get_real_positions(conn)
    cb_rows = get_real_cb_positions(conn)
    json.dump(
        {"rows": actual_rows, "cb_rows": cb_rows},
        (data_dir / "decide-positions.json").open("w"),
        ensure_ascii=False, default=str,
    )

    for d in dates:
        picks = [
            dict(zip(["rank", "code", "name", "buy", "stop", "target", "score", "strategy"], r))
            for r in conn.execute(
                "SELECT rank, code, name, buy, stop, target, score, strategy "
                "FROM daily_picks WHERE kind='买入信号' AND date=? ORDER BY rank",
                (d,),
            )
        ]
        built = build_plan_from_picks(picks)
        # 只导 plan；对比结果由前端 decide_compare.js 实时算（持仓在 D1，随时可变）
        json.dump(
            {"date": d, **built},
            (data_dir / f"decide-{d}.json").open("w"),
            ensure_ascii=False, default=str,
        )

    # 前端对比用的配置（PORTFOLIO_10W），保证前后端规则同源
    (data_dir / "decide-config.json").write_text(
        json.dumps(PORTFOLIO_10W, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def export_portfolio(db_path: str, out_dir: Path, *, plan_date: str | None = None) -> None:
    """写出 portfolio 静态数据 + portfolio.html。"""
    pf_dir = out_dir / "data" / "portfolio"
    pf_dir.mkdir(parents=True, exist_ok=True)

    conn = open_db(db_path)
    try:
        latest = plan_date or conn.execute(
            "SELECT MAX(plan_date) FROM trade_plan"
        ).fetchone()[0] or ""

        tiers = []
        for capital in CAPITAL_TIERS:
            label = f"{capital // 10000}W"
            pnl = compute_portfolio_pnl(conn, label, initial_capital=capital)
            holdings = get_open_positions_with_unrealized(conn, portfolio=label)
            cur = conn.execute(
                "SELECT tp.*, m.name AS name FROM trade_plan tp "
                "LEFT JOIN hs300_metadata m ON m.code = tp.code "
                "WHERE tp.plan_date=? AND tp.portfolio=? "
                "ORDER BY tp.action DESC, tp.code",
                (latest, label),
            )
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
            tiers.append({
                "label": label,
                "capital": capital,
                "cash_remaining": pnl["cash_remaining"],
                "position_value": pnl["position_value"],
                "total_value": pnl["total_value"],
                "realized_pnl": pnl["realized_pnl"],
                "unrealized_pnl": pnl["unrealized_pnl"],
                "return_rate": pnl["return_rate"],
                "last_plan_date": latest,
            })
            detail = {
                "label": label,
                "capital": capital,
                "date": latest,
                "pnl": pnl,
                "holdings": holdings,
                "plan_rows": plan_rows,
                "last_plan_date": latest,
            }
            json.dump(
                detail,
                (pf_dir / f"{label}.json").open("w"),
                ensure_ascii=False, default=str,
            )

        json.dump(
            {"date": latest, "tiers": tiers},
            (pf_dir / "summary.json").open("w"),
            ensure_ascii=False, default=str,
        )
    finally:
        conn.close()

    # 静态页
    (out_dir / "portfolio.html").write_text(
        _render_static_page("portfolio.html",
            today=latest,
            strategy_options="",
            year=_dt_class.now().year),
        encoding="utf-8",
    )


def export(db_path: str, out_dir: str) -> int:
    out = Path(out_dir)
    data_dir = out / "data"
    static_dir = out / "static"
    data_dir.mkdir(parents=True, exist_ok=True)
    static_dir.mkdir(parents=True, exist_ok=True)

    conn = open_db(db_path)
    try:
        pick_dates = list_dates(conn)
        plan_dates = [
            r[0] for r in conn.execute(
                "SELECT DISTINCT plan_date FROM trade_plan ORDER BY plan_date DESC"
            ).fetchall()
        ]

        # JSON payloads (mirror the Flask API responses)
        json.dump({"dates": pick_dates}, (data_dir / "dates.json").open("w"), ensure_ascii=False)
        json.dump({"dates": plan_dates}, (data_dir / "plan-dates.json").open("w"), ensure_ascii=False)
        for d in pick_dates:
            groups = {}
            for row in picks_for_date(conn, d):
                groups.setdefault(row["kind"], []).append(row)
            json.dump(
                {"date": d, "groups": groups},
                (data_dir / f"picks-{d}.json").open("w"),
                ensure_ascii=False,
            )
        for d in plan_dates:
            rows = get_trade_plan_by_date(conn, d, include_failed=True)
            json.dump(
                {"plan_date": d, "rows": rows},
                (data_dir / f"plan-{d}.json").open("w"),
                ensure_ascii=False,
                default=str,
            )
        json.dump(_dashboard_payload(conn), (data_dir / "dashboard.json").open("w"),
                  ensure_ascii=False, default=str)
        json.dump(get_holdings_detail(conn), (data_dir / "holdings.json").open("w"),
                  ensure_ascii=False, default=str)
        _export_cb_data(conn, data_dir)
        _export_decide_data(conn, data_dir)
        _export_stats(conn, data_dir)
    finally:
        conn.close()

    # 清理已不在当前日期集合里的陈旧 picks-*/plan-* 文件
    _cleanup_stale_date_files(data_dir, pick_dates, plan_dates)

    # 静态页默认日期 = 最新可用数据日期（fallback 到今天仅用于展示占位）
    today = _dt_class.now().strftime("%Y-%m-%d")
    picks_default = pick_dates[0] if pick_dates else today
    plan_default = plan_dates[0] if plan_dates else today

    # Generate tier filter options for plan page
    tier_filter_options = "".join(
        f'<li><label class="dropdown-item"><input type="checkbox" class="form-check-input me-1 tier-filter-cb" value="{c}"> {c // 10000}W</label></li>'
        for c in CAPITAL_TIERS
    )

    (out / "index.html").write_text(
        _render_static_page("picks.html", today=picks_default, year=_dt_class.now().year),
        encoding="utf-8",
    )
    (out / "plan.html").write_text(
        _render_static_page("plan.html",
            today=plan_default,
            capital_tiers=CAPITAL_TIERS,
            tier_filter_options=tier_filter_options,
            year=_dt_class.now().year),
        encoding="utf-8",
    )
    (out / "cb.html").write_text(
        _render_static_page("cb.html",
            today=_dt_class.now().strftime("%Y-%m-%d"),
            year=_dt_class.now().year),
        encoding="utf-8",
    )
    (out / "decide.html").write_text(
        _render_static_page("decide.html",
            today=_dt_class.now().strftime("%Y-%m-%d"),
            config=PORTFOLIO_10W,
            year=_dt_class.now().year),
        encoding="utf-8",
    )

    # 404 回退到首页（Pages SPA 惯例），避免陈旧副本
    shutil.copy(out / "index.html", out / "404.html")

    # Static assets: copy JS modules, CSS, vendor libs, favicon
    src_static = Path(__file__).parent / "static"
    version = _static_version()
    # JS modules：重写模块内部 import 的相对路径，追加 ?v=<sha>，
    # 否则浏览器缓存的旧依赖模块（如 api.js）会让新入口 import 失败。
    dst_js = static_dir / "js"
    dst_js.mkdir(exist_ok=True)
    for f in (src_static / "js").glob("*.js"):
        src = f.read_text(encoding="utf-8")
        src = re.sub(
            r"from\s+'(\./[^']+\.js)'",
            rf"from '\1?v={version}'",
            src,
        )
        (dst_js / f.name).write_text(src, encoding="utf-8")
    # CSS
    dst_css = static_dir / "css"
    dst_css.mkdir(exist_ok=True)
    for f in (src_static / "css").glob("*.css"):
        shutil.copy(f, dst_css / f.name)
    # Vendor libs
    dst_vendor = static_dir / "vendor"
    dst_vendor.mkdir(exist_ok=True)
    for f in (src_static / "vendor").glob("*"):
        shutil.copy(f, dst_vendor / f.name)
    # Favicon
    shutil.copy(src_static / "favicon.svg", static_dir / "favicon.svg")

    print(f"导出完成：picks={len(pick_dates)} plan={len(plan_dates)} → {out}")
    export_portfolio(db_path, out)
    return 0


def main():
    ap = argparse.ArgumentParser(description="导出静态站点（Cloudflare Pages）")
    ap.add_argument("--db", default="hs300.db")
    ap.add_argument("--out", default="site")
    ap.add_argument("--portfolio-only", action="store_true",
                    help="只导出 portfolio 数据（data/portfolio/*.json + portfolio.html）")
    args = ap.parse_args()
    if args.portfolio_only:
        export_portfolio(args.db, Path(args.out))
        return
    raise SystemExit(export(args.db, args.out))


if __name__ == "__main__":
    main()
