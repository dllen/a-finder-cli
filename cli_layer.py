import argparse
from typing import Dict, List, Optional

from domain_models import Stock
from db_repository import get_metadata_by_code, list_fetch_offsets, open_db
from formatter import format_lines, format_table
from logger import set_log_level
from sync_service import sync_hs300, sync_hs300_metadata, sync_hs300_range
from view_models import build_ma_picks_rows, build_overview_lines, build_picks_rows, build_signals_rows


def format_picks(stocks: List[Stock], scores: Dict[str, float], top: int) -> str:
    rows = build_picks_rows(stocks, scores, top)
    headers = ["代码", "名称", "评分", "信号", "策略", "最新价", "60日涨幅"]
    return format_table(headers, rows)


def format_ma_picks(stocks: List[Stock], top: int) -> str:
    rows = build_ma_picks_rows(stocks, top)
    headers = ["代码", "名称", "形态", "信号", "策略", "最新价", "MA50", "MA200", "量比", "止损价"]
    return format_table(headers, rows)


def format_signals(stocks: List[Stock], code: Optional[str]) -> str:
    rows = build_signals_rows(stocks, code)
    headers = ["代码", "名称", "信号", "策略", "最新价"]
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
    ma_headers = ["代码", "名称", "形态", "信号", "策略", "最新价", "MA50", "MA200", "量比", "止损价"]

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
    ma_headers = ["代码", "名称", "形态", "信号", "策略", "最新价", "MA50", "MA200", "量比", "止损价"]

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

    return parser


def run_cli(args: argparse.Namespace, stocks: List[Stock], scores: Dict[str, float]) -> None:
    if args.command == "picks":
        print(format_picks(stocks, scores, args.top))
    elif args.command == "signals":
        print(format_signals(stocks, args.code))
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
    else:
        build_parser().print_help()
