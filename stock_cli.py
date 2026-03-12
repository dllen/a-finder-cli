import os

from cli_layer import build_parser, run_cli
from market_data import build_market, build_market_from_db
from analysis_service import get_scores


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command in ("sync-hs300", "sync-hs300-range", "sync-hs300-meta", "meta", "show-offsets") or args.command is None:
        run_cli(args, [], {})
        return
    min_days = 220 if args.command == "ma-picks" else 60
    db_path = getattr(args, "db", None)
    stocks = []
    if db_path and os.path.exists(db_path):
        stocks = build_market_from_db(db_path, min_days=min_days)
    if not stocks:
        stocks = build_market()
    scores = get_scores(stocks) if stocks else {}
    run_cli(args, stocks, scores)


if __name__ == "__main__":
    main()
