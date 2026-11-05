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

# 2026-11-05：交易计划 / 持仓组合 / 决策台已下线，
# 所以不再需要 get_trade_plan_by_date / get_today_plan_summary /
# get_open_positions_with_unrealized / get_recent_pnl / get_holdings_detail /
# compute_portfolio_pnl / CAPITAL_TIERS / PORTFOLIO_10W / dedupe_plan_rows。
from db_repository import (
    open_db,
    get_last_refresh,
)
from app import (
    list_dates,
    picks_for_date,
    _dt_class,
)

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


#: 已下线功能的产物（2026-11-05 去掉「交易计划」「持仓组合」「决策台」）。
#: 导出时必须**主动删掉**这些旧文件，否则 gh-pages 上会继续留着，
#: 站点等于没下线（Cloudflare 还会继续对外提供这些 JSON）。
OBSOLETE_DATA_GLOBS = (
    "plan-*.json",        # 每日交易计划
    "plan-dates.json",
    "decide-*.json",      # 决策台
    "holdings.json",      # 持仓明细
    "confirm-*.json",
)
OBSOLETE_DATA_DIRS = ("portfolio",)   # 档位组合目录
OBSOLETE_PAGES = ("plan.html", "portfolio.html", "decide.html")


def _cleanup_stale_date_files(data_dir: Path, pick_dates) -> None:
    """删除陈旧 picks-* 文件、以及**已下线功能**留下的所有产物。

    export 只写当前日期、不清理历史残留；重导出后旧日期文件会留在原地。
    这里按文件名里的 YYYY-MM-DD 过滤，删掉不在最新日期集合里的。

    ⚠️ 2026-11-05：交易计划 / 持仓组合 / 决策台已下线。仅「不再生成」是不够的 ——
    上一版发布出去的 plan-*.json、data/portfolio/、decide-*.json、holdings.json、
    plan.html 等**会一直留在 gh-pages 上**，站点实际上没有下线。
    所以这里必须显式删除（见 OBSOLETE_* 常量）。
    """
    pick_set = set(pick_dates)
    for f in data_dir.glob("picks-*.json"):
        date = f.stem[len("picks") + 1:]  # picks-YYYY-MM-DD -> YYYY-MM-DD
        if len(date) != 10 or date[4] != "-" or date[7] != "-":
            continue
        if date not in pick_set:
            f.unlink()

    # 已下线功能的产物：无条件删除
    for pattern in OBSOLETE_DATA_GLOBS:
        for f in data_dir.glob(pattern):
            f.unlink()
    for name in OBSOLETE_DATA_DIRS:
        d = data_dir / name
        if d.is_dir():
            shutil.rmtree(d)


def _dashboard_payload(conn) -> dict:
    """Dashboard 卡片数据：运行状态 + 可转债摘要。

    ⚠️ 2026-11-05 起「交易计划」「持仓组合」「决策台」已下线（用户要求），
    trade_plan / open_positions / trade_events 三张表已清空
    （迁移 2026_11_05_remove_plan_features）⇒ 不再导出
    today_plan / open_positions / pnl_5d / holdings_summary。

    保留 last_refresh（数据新鲜度，选股页仍需要）与 cb（可转债仍在线）。
    cb 这部分**必须**在这里带上：静态站的 dashboard.js 读的是同一个
    dashboard.json，不给它 cb 的话转债卡片在 gh-pages 上恒为「未初始化」。
    """
    last = get_last_refresh(conn)
    if last:
        try:
            parsed = _dt_class.fromisoformat(last["updated_at"])
        except ValueError:
            parsed = _dt_class.strptime(last["updated_at"], "%Y-%m-%d %H:%M:%S")
        ago = (_dt_class.now() - parsed).total_seconds() / 3600
        last["ago_hours"] = round(ago, 1)
        last["freshness"] = "fresh" if ago < 24 else ("warm" if ago < 72 else "stale")
    # 可转债摘要（与 /api/dashboard 同构，保证静态站与 API 模式显示一致）
    from cb_repository import get_cb_positions
    cb_date = conn.execute(
        "SELECT MAX(trade_date) FROM cb_picks").fetchone()[0]
    cb_picks_count = 0
    if cb_date:
        cb_picks_count = conn.execute(
            "SELECT COUNT(*) FROM cb_picks WHERE trade_date=?", (cb_date,)
        ).fetchone()[0]
    cb_positions_count = len(get_cb_positions(conn))

    return {
        "last_refresh": last,
        "cb": {
            "picks_count": cb_picks_count,
            "positions_count": cb_positions_count,
            "latest_date": cb_date,
        },
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


def export(db_path: str, out_dir: str) -> int:
    out = Path(out_dir)
    data_dir = out / "data"
    static_dir = out / "static"
    data_dir.mkdir(parents=True, exist_ok=True)
    static_dir.mkdir(parents=True, exist_ok=True)

    conn = open_db(db_path)
    try:
        pick_dates = list_dates(conn)

        # JSON payloads (mirror the Flask API responses)
        json.dump({"dates": pick_dates}, (data_dir / "dates.json").open("w"), ensure_ascii=False)
        for d in pick_dates:
            groups = {}
            for row in picks_for_date(conn, d):
                groups.setdefault(row["kind"], []).append(row)
            json.dump(
                {"date": d, "groups": groups},
                (data_dir / f"picks-{d}.json").open("w"),
                ensure_ascii=False,
            )
        json.dump(_dashboard_payload(conn), (data_dir / "dashboard.json").open("w"),
                  ensure_ascii=False, default=str)
        _export_cb_data(conn, data_dir)
        _export_stats(conn, data_dir)
    finally:
        conn.close()

    # 清理陈旧 picks-* 文件 + 已下线功能的产物（plan/portfolio/decide/holdings）
    _cleanup_stale_date_files(data_dir, pick_dates)

    # 静态页默认日期 = 最新可用数据日期（fallback 到今天仅用于展示占位）
    today = _dt_class.now().strftime("%Y-%m-%d")
    picks_default = pick_dates[0] if pick_dates else today

    (out / "index.html").write_text(
        _render_static_page("picks.html", today=picks_default, year=_dt_class.now().year),
        encoding="utf-8",
    )
    (out / "cb.html").write_text(
        _render_static_page("cb.html",
            today=_dt_class.now().strftime("%Y-%m-%d"),
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
    # 图片（赞赏收款码，2026-11-05 加）
    # ⚠️ 漏了这块的后果很隐蔽：本地 Flask 站图片正常（直接读 static/），
    # 但静态站会**图裂**—— 只在线上复现，本机测不出来。
    src_images = src_static / "images"
    if src_images.is_dir():
        dst_images = static_dir / "images"
        dst_images.mkdir(exist_ok=True)
        for f in src_images.iterdir():
            if f.is_file():
                shutil.copy(f, dst_images / f.name)
    # Favicon
    shutil.copy(src_static / "favicon.svg", static_dir / "favicon.svg")

    # 已下线功能的静态页也一并删除（否则 gh-pages 上还留着）
    for name in OBSOLETE_PAGES:
        p = out / name
        if p.exists():
            p.unlink()

    print(f"导出完成：picks={len(pick_dates)} → {out}（已下线 plan/portfolio/decide）")
    return 0


def main():
    ap = argparse.ArgumentParser(description="导出静态站点（Cloudflare Pages）")
    ap.add_argument("--db", default="hs300.db")
    ap.add_argument("--out", default="site")
    args = ap.parse_args()
    raise SystemExit(export(args.db, args.out))


if __name__ == "__main__":
    main()
