import argparse
from typing import Dict, List, Optional

from config import CAPITAL_TIERS
from domain_models import Stock
from db_repository import get_metadata_by_code, list_fetch_offsets, open_db
from formatter import format_lines, format_table
from logger import set_log_level
from sync_service import sync_hs300, sync_hs300_metadata, sync_hs300_range
from view_models import build_buy_signals_rows, build_ma_picks_rows, build_overview_lines, build_picks_rows, build_signals_rows


def format_picks(stocks: List[Stock], scores: Dict[str, float], top: int) -> str:
    rows = build_picks_rows(stocks, scores, top)
    headers = ["代码", "名称", "评分", "信号", "策略", "最新价", "60日涨幅"]
    return format_table(headers, rows)


def format_ma_picks(stocks: List[Stock], top: int) -> str:
    rows = build_ma_picks_rows(stocks, top)
    headers = ["代码", "名称", "形态", "信号", "策略", "最新价", "MA10", "MA30", "MA50", "MA100", "MA200", "量比", "止损价"]
    return format_table(headers, rows)


def format_signals(stocks: List[Stock], code: Optional[str]) -> str:
    rows = build_signals_rows(stocks, code)
    headers = ["代码", "名称", "信号", "策略", "最新价"]
    return format_table(headers, rows)


def format_buy_signals(stocks: List[Stock], top: int) -> str:
    rows = build_buy_signals_rows(stocks, top)
    headers = ["代码", "名称", "买入信号数", "主策略", "买入价", "止损价", "目标价"]
    return format_table(headers, rows)


def format_overview(stocks: List[Stock], scores: Dict[str, float]) -> str:
    lines = build_overview_lines(stocks, scores)
    return format_lines(lines)


def format_meta(code: str, name: str, industry: str, region: str) -> str:
    headers = ["代码", "名称", "行业", "地区"]
    rows = [[code, name or "-", industry or "-", region or "-"]]
    return format_table(headers, rows)


def run_textual_ui(stocks: List[Stock], scores: Dict[str, float], top: int, code: Optional[str]) -> None:
    from textual.app import App, ComposeResult
    from textual.containers import Vertical
    from textual.widgets import DataTable, Footer, Header, Static

    overview_lines = build_overview_lines(stocks, scores)
    picks_rows = build_picks_rows(stocks, scores, top)
    picks_headers = ["代码", "名称", "评分", "信号", "策略", "最新价", "60日涨幅"]
    signals_rows = build_signals_rows(stocks, code)
    signals_headers = ["代码", "名称", "信号", "策略", "最新价"]
    ma_rows = build_ma_picks_rows(stocks, top)
    ma_headers = ["代码", "名称", "形态", "信号", "策略", "最新价", "MA10", "MA30", "MA50", "MA100", "MA200", "量比", "止损价"]

    class FinderApp(App):
        CSS = """
        Screen {
            background: #0b1020;
            color: #e6e6e6;
        }
        #main {
            padding: 1 2;
        }
        .section-title {
            text-style: bold;
            color: #7dd3fc;
            margin-top: 1;
        }
        DataTable {
            height: auto;
            max-height: 14;
        }
        """
        BINDINGS = [("q", "quit", "退出")]

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            with Vertical(id="main"):
                yield Static("市场概览", classes="section-title")
                yield Static("\n".join(overview_lines))
                yield Static("今日选股", classes="section-title")
                yield DataTable(id="picks_table")
                yield Static("买卖信号", classes="section-title")
                yield DataTable(id="signals_table")
                yield Static("均线选股", classes="section-title")
                yield DataTable(id="ma_table")
            yield Footer()

        def on_mount(self) -> None:
            picks_table = self.query_one("#picks_table", DataTable)
            picks_table.add_columns(*picks_headers)
            picks_table.add_rows(picks_rows)
            picks_table.zebra_stripes = True

            signals_table = self.query_one("#signals_table", DataTable)
            signals_table.add_columns(*signals_headers)
            signals_table.add_rows(signals_rows)
            signals_table.zebra_stripes = True

            ma_table = self.query_one("#ma_table", DataTable)
            ma_table.add_columns(*ma_headers)
            ma_table.add_rows(ma_rows)
            ma_table.zebra_stripes = True

    FinderApp().run()


def run_ma_picks_textual_ui(stocks: List[Stock], top: int) -> None:
    from textual.app import App, ComposeResult
    from textual.containers import Vertical
    from textual.widgets import DataTable, Footer, Header, Static

    ma_rows = build_ma_picks_rows(stocks, top)
    ma_headers = ["代码", "名称", "形态", "信号", "策略", "最新价", "MA10", "MA30", "MA50", "MA100", "MA200", "量比", "止损价"]

    class MAPicksApp(App):
        CSS = """
        Screen {
            background: #0b1020;
            color: #e6e6e6;
        }
        #main {
            padding: 1 2;
        }
        .section-title {
            text-style: bold;
            color: #7dd3fc;
            margin-bottom: 1;
        }
        DataTable {
            height: 1fr;
        }
        """
        BINDINGS = [("q", "quit", "退出")]

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            with Vertical(id="main"):
                yield Static(f"均线选股 Top {top}", classes="section-title")
                yield DataTable(id="ma_table")
            yield Footer()

        def on_mount(self) -> None:
            ma_table = self.query_one("#ma_table", DataTable)
            ma_table.add_columns(*ma_headers)
            ma_table.add_rows(ma_rows)
            ma_table.zebra_stripes = True

    MAPicksApp().run()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="a-finder", description="选股与买卖点提示命令行")
    subparsers = parser.add_subparsers(dest="command")

    pick_parser = subparsers.add_parser("picks", help="输出今日选股")
    pick_parser.add_argument("--top", type=int, default=10, help="输出前 N 只股票")
    pick_parser.add_argument("--db", type=str, default="hs300.db", help="SQLite 文件路径")

    signal_parser = subparsers.add_parser("signals", help="输出买卖信号")
    signal_parser.add_argument("--code", type=str, help="指定股票代码")
    signal_parser.add_argument("--db", type=str, default="hs300.db", help="SQLite 文件路径")

    buy_parser = subparsers.add_parser("buy-signals", help="输出沪深300买入信号并按推荐优先级排序")
    buy_parser.add_argument("--top", type=int, default=20, help="输出前 N 只股票")
    buy_parser.add_argument("--db", type=str, default="hs300.db", help="SQLite 文件路径")

    ma_parser = subparsers.add_parser("ma-picks", help="输出均线选股结果")
    ma_parser.add_argument("--top", type=int, default=10, help="输出前 N 只股票")
    ma_parser.add_argument("--db", type=str, default="hs300.db", help="SQLite 文件路径")
    ma_parser.add_argument("--ui", action="store_true", help="使用 Textual 界面展示结果")

    sync_parser = subparsers.add_parser("sync-hs300", help="同步沪深300近一年行情到SQLite")
    sync_parser.add_argument("--db", type=str, default="hs300.db", help="SQLite 文件路径")
    sync_parser.add_argument(
        "--mode",
        type=str,
        default="incremental",
        choices=["incremental", "full"],
        help="增量或全量",
    )
    sync_parser.add_argument("--limit", type=int, help="限制同步数量")
    sync_parser.add_argument("--log-level", type=str, help="日志级别")

    range_parser = subparsers.add_parser("sync-hs300-range", help="同步沪深300指定区间行情到SQLite")
    range_parser.add_argument("--db", type=str, default="hs300.db", help="SQLite 文件路径")
    range_parser.add_argument("--start", type=str, required=True, help="开始日期 YYYY-MM-DD")
    range_parser.add_argument("--end", type=str, required=True, help="结束日期 YYYY-MM-DD")
    range_parser.add_argument("--limit", type=int, help="限制同步数量")
    range_parser.add_argument("--concurrency", type=int, default=4, help="并发数")
    range_parser.add_argument("--rate", type=float, default=5.0, help="每秒请求数")
    range_parser.add_argument("--retries", type=int, default=3, help="重试次数")
    range_parser.add_argument("--backoff", type=float, default=0.5, help="指数补偿基数秒")
    range_parser.add_argument("--no-resume", action="store_true", help="关闭断点续抓")
    range_parser.add_argument("--only-failed", action="store_true", help="只重试失败标记的股票")
    range_parser.add_argument("--gap-fill", action="store_true", help="按缺口日期分段补抓")
    range_parser.add_argument("--retry-gaps", action="store_true", help="仅对失败股票做缺口补抓")
    range_parser.add_argument("--log-level", type=str, help="日志级别")

    meta_sync_parser = subparsers.add_parser("sync-hs300-meta", help="同步沪深300元数据到SQLite")
    meta_sync_parser.add_argument("--db", type=str, default="hs300.db", help="SQLite 文件路径")
    meta_sync_parser.add_argument("--concurrency", type=int, default=4, help="并发数")
    meta_sync_parser.add_argument("--rate", type=float, default=5.0, help="每秒请求数")
    meta_sync_parser.add_argument("--retries", type=int, default=3, help="重试次数")
    meta_sync_parser.add_argument("--backoff", type=float, default=0.5, help="指数补偿基数秒")
    meta_sync_parser.add_argument("--log-level", type=str, help="日志级别")

    meta_parser = subparsers.add_parser("meta", help="查询股票元数据")
    meta_parser.add_argument("--code", type=str, required=True, help="指定股票代码")
    meta_parser.add_argument("--db", type=str, default="hs300.db", help="SQLite 文件路径")

    offsets_parser = subparsers.add_parser("show-offsets", help="查看抓取点位")
    offsets_parser.add_argument("--db", type=str, default="hs300.db", help="SQLite 文件路径")
    offsets_parser.add_argument("--code", type=str, help="指定股票代码")
    offsets_parser.add_argument("--limit", type=int, default=50, help="返回条数上限")

    overview_parser = subparsers.add_parser("overview", help="输出市场概览")
    overview_parser.add_argument("--db", type=str, default="hs300.db", help="SQLite 文件路径")

    ui_parser = subparsers.add_parser("ui", help="启动美化终端界面")
    ui_parser.add_argument("--top", type=int, default=10, help="选股展示数量")
    ui_parser.add_argument("--code", type=str, help="信号指定股票代码")
    ui_parser.add_argument("--db", type=str, default="hs300.db", help="SQLite 文件路径")

    plan_parser = subparsers.add_parser("plan", help="生成 / 查看每日交易计划（paper）")
    plan_sub = plan_parser.add_subparsers(dest="action")

    # plan build
    pb = plan_sub.add_parser("build", help="生成单 portfolio 的 plan")
    pb.add_argument("--date", type=str, default=None, help="YYYY-MM-DD（默认今日）")
    pb.add_argument("--db", type=str, default="hs300.db")
    pb.add_argument("--rr-target", type=float, default=None)
    pb.add_argument("--max-single", type=float, default=None)
    pb.add_argument("--slippage", type=float, default=None)
    pb.add_argument("--regime", type=str, default=None,
                    choices=["bull", "bear", "sideways"])
    pb.add_argument("--capital", type=int, default=None, choices=CAPITAL_TIERS, metavar="资金")
    pb.add_argument("--portfolio", type=str, default="default",
                    help="portfolio label（如 '10W'），默认 'default'")
    pb.add_argument("--dry-run", action="store_true")
    pb.add_argument("--backfill", action="store_true",
                    help="补齐所有历史 daily_picks 日期的 plan")
    pb.add_argument("--list", dest="list_mode", action="store_true")
    pb.add_argument("--days", type=int, default=30)
    pb.add_argument("--show", dest="show_mode", action="store_true")

    # plan build-all
    pba = plan_sub.add_parser("build-all", help="为所有资金档位批量生成 plan")
    pba.add_argument("--date", type=str, default=None, help="YYYY-MM-DD（默认今日）")
    pba.add_argument("--db", type=str, default="hs300.db")
    pba.add_argument("--strategy", type=str, default="linyuan",
                     help="策略名（仅日志）")
    pba.add_argument("--tiers", type=str, default=None,
                     help="逗号分隔资金列表（如 '50000,100000'），默认走 CAPITAL_TIERS")
    pba.add_argument("--since", type=str, default=None,
                     help="回算起点 YYYY-MM-DD（仅 --backfill）")
    pba.add_argument("--backfill", action="store_true",
                     help="回填所有历史 daily_picks 日期")
    pba.add_argument("--dry-run", action="store_true")
    pba.add_argument("--regime", type=str, default="sideways",
                     choices=["bull", "bear", "sideways"])
    pba.add_argument("--rr-target", type=float, default=None)
    pba.add_argument("--max-single", type=float, default=None)
    pba.add_argument("--slippage", type=float, default=None)

    evolve_parser = subparsers.add_parser("evolve", help="选股策略自我进化闭环（周频 cron 用）")
    evolve_parser.add_argument("--db", type=str, default="hs300.db", help="SQLite 文件路径")
    evolve_parser.add_argument("--top", type=int, default=20, help="榜单席位数（与每日 pick 保持一致）")
    evolve_parser.add_argument("--backfill-days", type=int, default=250, help="首次重放标注的交易日数")
    evolve_parser.add_argument("--dry-run", action="store_true", help="只打印报告，不写库")

    ly = subparsers.add_parser(
        "linyuan-picks",
        help="林园策略选股：连续5年毛利率>40% ∧ 扣非ROE>15% ∧ 行业白名单",
    )
    ly.add_argument("--top", type=int, default=20)
    ly.add_argument("--db", type=str, default="hs300.db")
    ly.add_argument("--dry-run", action="store_true")

    sfh = subparsers.add_parser("sync-fundamentals-history", help="同步历年财务到 fundamentals_history")
    sfh.add_argument("--db", type=str, default="hs300.db")
    sfh.add_argument("--concurrency", type=int, default=4)
    sfh.add_argument("--rate", type=float, default=3.0)
    sfh.add_argument("--retries", type=int, default=2)
    sfh.add_argument("--backoff", type=float, default=1.0)

    si = subparsers.add_parser("sync-industry", help="回填 hs300_metadata.industry")
    si.add_argument("--db", type=str, default="hs300.db")
    si.add_argument("--concurrency", type=int, default=4)
    si.add_argument("--rate", type=float, default=3.0)
    si.add_argument("--retries", type=int, default=2)
    si.add_argument("--backoff", type=float, default=1.0)

    return parser


def run_cli(args: argparse.Namespace, stocks: List[Stock], scores: Dict[str, float]) -> None:
    if args.command == "picks":
        print(format_picks(stocks, scores, args.top))
    elif args.command == "signals":
        print(format_signals(stocks, args.code))
    elif args.command == "buy-signals":
        print(format_buy_signals(stocks, args.top))
    elif args.command == "ma-picks":
        if args.ui:
            run_ma_picks_textual_ui(stocks, args.top)
        else:
            print(format_ma_picks(stocks, args.top))
    elif args.command == "sync-hs300":
        set_log_level(args.log_level)
        result = sync_hs300(args.db, args.mode, args.limit)
        print(f"成分股: {result['symbols']} 只")
        print(f"写入行: {result['rows']} 条")
    elif args.command == "sync-hs300-range":
        set_log_level(args.log_level)
        only_failed = args.only_failed or args.retry_gaps
        gap_fill = args.gap_fill or args.retry_gaps
        result = sync_hs300_range(
            args.db,
            args.start,
            args.end,
            args.limit,
            args.concurrency,
            args.rate,
            args.retries,
            args.backoff,
            not args.no_resume,
            only_failed,
            gap_fill,
        )
        print(f"成分股: {result['symbols']} 只")
        print(f"写入行: {result['rows']} 条")
    elif args.command == "sync-hs300-meta":
        set_log_level(args.log_level)
        result = sync_hs300_metadata(
            args.db,
            args.concurrency,
            args.rate,
            args.retries,
            args.backoff,
        )
        print(f"成分股: {result['symbols']} 只")
        print(f"写入行: {result['rows']} 条")
    elif args.command == "meta":
        conn = open_db(args.db)
        with conn:
            meta = get_metadata_by_code(conn, args.code)
        if not meta:
            print(f"未找到元数据: {args.code}")
            return
        print(format_meta(meta.code, meta.name, meta.industry, meta.region))
    elif args.command == "show-offsets":
        conn = open_db(args.db)
        with conn:
            rows = list_fetch_offsets(conn, args.code, args.limit)
        headers = ["代码", "最后交易日", "更新时间"]
        print(format_table(headers, [[row[0], row[1], row[2]] for row in rows]))
    elif args.command == "overview":
        print(format_overview(stocks, scores))
    elif args.command == "ui":
        run_textual_ui(stocks, scores, args.top, args.code)
    elif args.command == "plan":
        if args.action == "build-all":
            _run_plan_build_all(args)
        else:
            _run_plan_build(args)
    elif args.command == "evolve":
        _run_evolve(args)
    elif args.command == "linyuan-picks":
        _run_linyuan(args)
    elif args.command == "sync-fundamentals-history":
        _run_sync_fundamentals_history(args)
    elif args.command == "sync-industry":
        _run_sync_industry(args)
    else:
        build_parser().print_help()


def _run_plan_build(args) -> None:
    """CLI handler for `plan build`: build / list / show / backfill."""
    from datetime import date as _date, timedelta
    from config import (
        MAX_SINGLE as DEFAULT_MAX_SINGLE,
        MAX_TOTAL as DEFAULT_MAX_TOTAL,
        RR_TARGET as DEFAULT_RR_TARGET,
        SLIPPAGE as DEFAULT_SLIPPAGE,
        DEFAULT_CAPITAL,
    )
    from db_repository import open_db, get_trade_plan_by_date
    from plan_builder import build_plan

    today = _date.today().isoformat()
    plan_date = args.date or today
    portfolio = getattr(args, "portfolio", "default")

    # --- list mode ---
    if args.list_mode:
        conn = open_db(args.db)
        try:
            cur = conn.execute(
                "SELECT DISTINCT plan_date FROM trade_plan "
                "WHERE portfolio=? ORDER BY plan_date DESC LIMIT 30",
                (portfolio,),
            )
            dates = [r[0] for r in cur.fetchall()]
        finally:
            conn.close()
        if not dates:
            print(f"无 plan（portfolio={portfolio}）")
            return
        print(f"portfolio={portfolio} 已生成 plan 共 {len(dates)} 天：")
        for d in dates:
            print(f"  {d}")
        return

    # --- show mode ---
    if args.show_mode:
        conn = open_db(args.db)
        try:
            cur = conn.execute(
                "SELECT tp.*, m.name AS name FROM trade_plan tp "
                "LEFT JOIN hs300_metadata m ON m.code = tp.code "
                "WHERE tp.plan_date=? AND tp.portfolio=? "
                "ORDER BY tp.action DESC, tp.code",
                (plan_date, portfolio),
            )
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            conn.close()
        if not rows:
            print(f"无 plan：{plan_date} portfolio={portfolio}")
            return
        print(f"plan_date={plan_date} portfolio={portfolio} rows={len(rows)}")
        for r in rows:
            print(f"  {r['action']:4s} {r['code']} px={r['plan_price']:.2f} "
                  f"status={r['status']} reason={r['reason']}")
        return

    # --- build mode ---
    params = {
        "max_single": args.max_single if args.max_single is not None else DEFAULT_MAX_SINGLE,
        "max_total": DEFAULT_MAX_TOTAL,
        "rr_target": args.rr_target if args.rr_target is not None else DEFAULT_RR_TARGET,
        "regime": args.regime or "sideways",
        "capital": args.capital if args.capital is not None else DEFAULT_CAPITAL,
    }
    slippage = args.slippage if args.slippage is not None else DEFAULT_SLIPPAGE

    if args.backfill:
        conn = open_db(args.db)
        try:
            dates = [r[0] for r in conn.execute(
                "SELECT DISTINCT date FROM daily_picks ORDER BY date"
            ).fetchall()]
        finally:
            conn.close()
        # Backfill 也走 paper_trade=True，让 open_positions 按日累积，
        # 跨日的 current_open_count cap（5-10 持仓上限）才能生效。
        for d in dates:
            result = build_plan(d, args.db, params, slippage=slippage,
                                paper_trade=True, include_carryover=True,
                                portfolio=portfolio)
            print(f"backfilled {d} portfolio={portfolio}: "
                  f"picks={result.num_picks} rows={len(result.rows)}")
        return

    if args.dry_run:
        print(f"[dry-run] would build plan for {plan_date} "
              f"portfolio={portfolio} params={params}")
        return

    result = build_plan(plan_date, args.db, params, slippage=slippage,
                        portfolio=portfolio)
    print(f"plan_date={plan_date} portfolio={portfolio} "
          f"picks={result.num_picks} open={result.num_open_positions} "
          f"sanity={result.sanity_passed}")
    for r in result.rows:
        print(f"  {r.action:4s} {r.code} px={r.plan_price:.2f} "
              f"size={r.size_pct} status={r.status} reason={r.reason}")


def _run_plan_build_all(args) -> None:
    """CLI handler for `plan build-all`: batch build all capital tiers."""
    from datetime import date as _date
    from config import CAPITAL_TIERS, DEFAULT_CAPITAL, MAX_SINGLE as DEFAULT_MAX_SINGLE, MAX_TOTAL as DEFAULT_MAX_TOTAL, RR_TARGET as DEFAULT_RR_TARGET
    from db_repository import open_db
    from plan_builder import build_all_portfolios

    today = _date.today().isoformat()
    plan_date = args.date or today

    tiers = CAPITAL_TIERS
    if args.tiers:
        tiers = [int(x.strip()) for x in args.tiers.split(",")]

    def _progress(pct: int, msg: str) -> None:
        print(f"[{pct:3d}%] {msg}")

    params = {
        "regime": args.regime or "sideways",
        "max_single": args.max_single if args.max_single is not None else DEFAULT_MAX_SINGLE,
        "max_total": DEFAULT_MAX_TOTAL,
        "rr_target": args.rr_target if args.rr_target is not None else DEFAULT_RR_TARGET,
    }

    if args.backfill:
        conn = open_db(args.db)
        try:
            dates = [r[0] for r in conn.execute(
                "SELECT DISTINCT date FROM daily_picks ORDER BY date"
            ).fetchall()]
        finally:
            conn.close()
        if args.since:
            dates = [d for d in dates if d >= args.since]
        for d in dates:
            results = build_all_portfolios(
                args.db, d,
                params=params,
                tiers=tiers, progress=_progress,
            )
            ok = sum(1 for r in results if r is not None)
            print(f"backfilled {d}: ok={ok}/{len(results)}")
        return

    if args.dry_run:
        print(f"[dry-run] would build plans for {plan_date} tiers={tiers}")
        return

    results = build_all_portfolios(
        args.db, plan_date,
        params=params,
        tiers=tiers, progress=_progress,
    )
    ok = sum(1 for r in results if r is not None)
    print(f"\nDONE plan_date={plan_date} tiers={len(tiers)} ok={ok}/{len(results)}")


def _run_evolve(args) -> None:
    """CLI handler for `evolve` subcommand: 每周策略自我进化闭环。"""
    from evolution.service import format_report, run_evolve

    def _progress(pct: int, msg: str) -> None:
        print(f"[{pct:3d}%] {msg}")

    report = run_evolve(args.db, top=args.top, backfill_days=args.backfill_days,
                        dry_run=args.dry_run, progress=_progress)
    print(format_report(report))


def _run_linyuan(args) -> None:
    from strategies.linyuan_multi_factor import LinYuanRunner

    runner = LinYuanRunner(top_n=args.top)
    result = runner.run(args.db)
    if args.dry_run:
        print(f"[dry-run] 林园候选 {len(result.positions)} 只")
        for p in result.positions:
            print(f"  {p.code} {p.name} sector={p.sector} weight={p.weight:.4f}")
        return
    headers = ["代码", "名称", "行业", "权重"]
    rows = [[p.code, p.name, p.sector, f"{p.weight:.4f}"] for p in result.positions]
    print(format_table(headers, rows))


def _run_sync_fundamentals_history(args) -> None:
    from sync_service import sync_fundamentals_history

    def _progress(pct: int, msg: str) -> None:
        print(f"[{pct:3d}%] {msg}")

    print(sync_fundamentals_history(
        args.db,
        concurrency=args.concurrency,
        rate_limit=args.rate,
        retries=args.retries,
        backoff=args.backoff,
        progress=_progress,
    ))


def _run_sync_industry(args) -> None:
    from sync_service import sync_industry

    def _progress(pct: int, msg: str) -> None:
        print(f"[{pct:3d}%] {msg}")

    print(sync_industry(
        args.db,
        concurrency=args.concurrency,
        rate_limit=args.rate,
        retries=args.retries,
        backoff=args.backoff,
        progress=_progress,
    ))
