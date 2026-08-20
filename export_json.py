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
)
from app import (
    _page,
    PAGE_BODY,
    PLAN_BODY,
    PAGE_SCRIPT,
    PLAN_SCRIPT,
    list_dates,
    picks_for_date,
    _dt_class,
)

STATIC_CONFIG = "window.APP_MODE='static'; window.DATA_PREFIX='';"

# 静态页去掉写入类操作按钮（重算/同步/生成 plan/含 failed）。
_WRITE_BTN = re.compile(
    r'<div class="col-auto">\s*<button[^>]*write-control[^>]*>.*?</button>\s*</div>',
    re.S,
)
_WRITE_CHECK = re.compile(
    r'<div class="col-auto">\s*<div class="form-check write-control">.*?</div>\s*</div>',
    re.S,
)


def _strip_write_controls(body: str) -> str:
    return _WRITE_CHECK.sub("", _WRITE_BTN.sub("", body))


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
    finally:
        conn.close()

    # 静态页默认日期 = 最新可用数据日期（fallback 到今天仅用于展示占位）
    # 注意：无需 fallback —— 只要有任意一天的 picks 数据就用它
    today = _dt_class.now().strftime("%Y-%m-%d")
    picks_default = pick_dates[0] if pick_dates else today
    plan_default = plan_dates[0] if plan_dates else today

    (out / "index.html").write_text(
        _page("每日机会", "picks",
              _strip_write_controls(PAGE_BODY).replace("{{today}}", picks_default),
              PAGE_SCRIPT, config=STATIC_CONFIG, assets="static/"),
        encoding="utf-8",
    )
    (out / "plan.html").write_text(
        _page("每日 Plan", "plan",
              _strip_write_controls(PLAN_BODY).replace("{{today}}", plan_default),
              PLAN_SCRIPT, config=STATIC_CONFIG, assets="static/"),
        encoding="utf-8",
    )

    # Static assets
    src_static = Path(__file__).parent / "static"
    for f in ("common.js", "data-source.js", "dashboard.js"):
        shutil.copy(src_static / f, static_dir / f)

    print(f"导出完成：picks={len(pick_dates)} plan={len(plan_dates)} → {out}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="导出静态站点（Cloudflare Pages）")
    ap.add_argument("--db", default="hs300.db")
    ap.add_argument("--out", default="site")
    args = ap.parse_args()
    raise SystemExit(export(args.db, args.out))


if __name__ == "__main__":
    main()
