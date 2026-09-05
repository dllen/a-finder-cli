import datetime as dt
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from config import SyncConfig
from db_repository import (
    FundamentalsHistoryRow,
    StockMeta,
    get_completed_codes,
    get_fetch_offsets,
    get_failed_codes,
    get_last_trade_date,
    get_trade_dates,
    get_trade_date_range,
    get_all_codes,
    insert_prices,
    open_db,
    upsert_checkpoint,
    upsert_constituents,
    upsert_fetch_offsets,
    upsert_fundamentals,
    upsert_fundamentals_history,
    upsert_metadata,
)
from akshare_data_provider import fetch_fundamentals_history_akshare
from data_providers import fetch_daily_kline_with_name, fetch_hs300_constituents, fetch_stock_meta
from errors import FetchError, InvalidConfigError
from logger import get_logger
from utils import date_to_str, parse_date


class RateLimiter:
    def __init__(self, rate_per_sec: float) -> None:
        self.min_interval = 1.0 / rate_per_sec if rate_per_sec > 0 else 0.0
        self.lock = threading.Lock()
        self.next_time = 0.0

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        with self.lock:
            now = time.time()
            sleep_for = max(0.0, self.next_time - now)
            self.next_time = max(now, self.next_time) + self.min_interval
        if sleep_for > 0:
            time.sleep(sleep_for)


def _append_log(path: str, line: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _fetch_with_retry(
    code: str,
    start_str: str,
    end_str: str,
    retries: int,
    backoff: float,
    limiter: RateLimiter,
) -> Tuple[List, str]:
    last_error: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            limiter.wait()
            return fetch_daily_kline_with_name(code, start_str, end_str)
        except Exception as exc:
            last_error = exc
            if attempt >= retries:
                break
            sleep_for = backoff * (2 ** attempt)
            time.sleep(sleep_for)
    if last_error:
        raise last_error
    return [], ""


def _fetch_meta_with_retry(code: str, retries: int, backoff: float, limiter: RateLimiter) -> Optional[StockMeta]:
    last_error: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            limiter.wait()
            return fetch_stock_meta(code)
        except Exception as exc:
            last_error = exc
            if attempt >= retries:
                break
            sleep_for = backoff * (2 ** attempt)
            time.sleep(sleep_for)
    if last_error:
        raise last_error
    return None


def _covered_range(min_date: Optional[str], max_date: Optional[str], start: dt.date, end: dt.date) -> bool:
    if not min_date or not max_date:
        return False
    try:
        min_dt = parse_date(min_date)
        max_dt = parse_date(max_date)
    except Exception:
        return False
    return min_dt <= start and max_dt >= end


def _build_gap_segments(existing_dates: List[str], start: dt.date, end: dt.date) -> List[Tuple[str, str]]:
    if not existing_dates:
        return [(date_to_str(start), date_to_str(end))]
    segments: List[Tuple[str, str]] = []
    prev = start - dt.timedelta(days=1)
    for date_str in existing_dates:
        try:
            current = parse_date(date_str)
        except Exception:
            continue
        if current < start or current > end:
            continue
        if current > prev + dt.timedelta(days=1):
            seg_start = prev + dt.timedelta(days=1)
            seg_end = current - dt.timedelta(days=1)
            segments.append((date_to_str(seg_start), date_to_str(seg_end)))
        prev = current
    if prev < end:
        seg_start = prev + dt.timedelta(days=1)
        segments.append((date_to_str(seg_start), date_to_str(end)))
    return segments


def sync_hs300(db_path: str, mode: str, limit: Optional[int], progress: Optional[Callable[[int, str], None]] = None) -> Dict[str, int]:
    logger = get_logger()
    if mode not in {"incremental", "full"}:
        raise InvalidConfigError("mode 仅支持 incremental 或 full")
    if limit is not None and limit <= 0:
        raise InvalidConfigError("limit 必须为正整数")
    config = SyncConfig(mode=mode, limit=limit)
    today = dt.date.today()
    start = today - dt.timedelta(days=365)
    start_str = date_to_str(start)
    end_str = date_to_str(today)
    mapping = fetch_hs300_constituents()
    if not mapping:
        raise FetchError("无法获取沪深300成分股列表")
    if config.limit is not None:
        mapping = {code: mapping[code] for code in list(sorted(mapping.keys()))[: config.limit]}
    logger.info("同步开始: mode=%s symbols=%s", config.mode, len(mapping))
    conn = open_db(db_path)
    with conn:
        if config.mode == "full":
            conn.execute("DELETE FROM hs300_constituents")
            conn.execute("DELETE FROM daily_prices")
        upsert_constituents(conn, mapping)
        total_rows = 0
        names: Dict[str, str] = {}
        code_offsets = {} if config.mode == "full" else get_fetch_offsets(conn, list(mapping.keys()))
        updated_offsets: Dict[str, str] = {}
        total = len(mapping)
        done = 0
        for code in sorted(mapping.keys()):
            last_date = None if config.mode == "full" else code_offsets.get(code)
            if not last_date and config.mode != "full":
                last_date = get_last_trade_date(conn, code)
            if last_date:
                next_day = parse_date(last_date) + dt.timedelta(days=1)
                if next_day > today:
                    done += 1
                    if progress:
                        progress(int(done * 100 / total), f"跳过 {code}（已最新）")
                    continue
                beg = date_to_str(next_day)
            else:
                beg = start_str
            try:
                rows, name = fetch_daily_kline_with_name(code, beg, end_str)
            except Exception as exc:  # noqa: BLE001
                done += 1
                logger.warning("拉取失败: code=%s error=%s", code, exc)
                if progress:
                    progress(int(done * 100 / total), f"⚠ {code} 拉取失败: {exc}")
                continue
            if name:
                names[code] = name
            total_rows += insert_prices(conn, rows)
            if rows:
                updated_offsets[code] = max(row.trade_date for row in rows)
            time.sleep(config.sleep_seconds)
            done += 1
            if progress:
                progress(int(done * 100 / total), f"同步 {code} 完成（{done}/{total}）")
        upsert_fetch_offsets(conn, updated_offsets)
        if names:
            upsert_metadata(conn, [StockMeta(code=c, name=n, industry="", region="") for c, n in names.items()])
    logger.info("同步完成: symbols=%s rows=%s", len(mapping), total_rows)
    return {"symbols": len(mapping), "rows": total_rows}


def sync_hs300_range(
    db_path: str,
    start_date: str,
    end_date: str,
    limit: Optional[int],
    concurrency: int,
    rate_limit: float,
    retries: int,
    backoff: float,
    resume: bool,
    only_failed: bool,
    gap_fill: bool,
    progress: Optional[Callable[[int, str], None]] = None,
) -> Dict[str, int]:
    logger = get_logger()
    if limit is not None and limit <= 0:
        raise InvalidConfigError("limit 必须为正整数")
    if concurrency <= 0:
        raise InvalidConfigError("concurrency 必须为正整数")
    if rate_limit < 0:
        raise InvalidConfigError("rate 必须为非负数")
    if retries < 0:
        raise InvalidConfigError("retries 必须为非负数")
    if backoff <= 0:
        raise InvalidConfigError("backoff 必须为正数")
    start = parse_date(start_date)
    end = parse_date(end_date)
    if start > end:
        raise InvalidConfigError("start 必须早于或等于 end")
    end_str = date_to_str(end)
    mapping = fetch_hs300_constituents()
    if not mapping:
        raise FetchError("无法获取沪深300成分股列表")
    if limit is not None:
        mapping = {code: mapping[code] for code in list(sorted(mapping.keys()))[:limit]}
    logger.info("区间同步开始: %s~%s symbols=%s", start_date, end_date, len(mapping))
    job_id = f"hs300:{start_date}:{end_date}"
    success_log = os.path.join(os.path.dirname(db_path) or ".", "logs", "fetch_success.log")
    fail_log = os.path.join(os.path.dirname(db_path) or ".", "logs", "fetch_failed.log")
    limiter = RateLimiter(rate_limit)
    conn = open_db(db_path)
    with conn:
        upsert_constituents(conn, mapping)
        completed = get_completed_codes(conn, job_id) if resume else {}
        failed = get_failed_codes(conn, job_id) if only_failed else {}
        code_offsets = get_fetch_offsets(conn, list(mapping.keys()))
        candidates: List[Tuple[str, List[Tuple[str, str]]]] = []
        for code in sorted(mapping.keys()):
            if only_failed and code not in failed:
                continue
            if resume and code in completed:
                continue
            if gap_fill:
                existing = get_trade_dates(conn, code, start_date, end_date)
                segments = _build_gap_segments(existing, start, end)
                if not segments:
                    upsert_checkpoint(conn, job_id, code, start_date, end_date, "success", None)
                    continue
                candidates.append((code, segments))
                continue
            beg = start
            if resume:
                last_date = code_offsets.get(code)
                if not last_date:
                    last_date = get_last_trade_date(conn, code)
                if last_date:
                    next_day = parse_date(last_date) + dt.timedelta(days=1)
                    if next_day > end:
                        upsert_checkpoint(conn, job_id, code, start_date, end_date, "success", None)
                        continue
                    if next_day > beg:
                        beg = next_day
            min_date, max_date = get_trade_date_range(conn, code)
            if resume and _covered_range(min_date, max_date, beg, end):
                upsert_checkpoint(conn, job_id, code, start_date, end_date, "success", None)
                continue
            candidates.append((code, [(date_to_str(beg), end_str)]))
        total_rows = 0
        if candidates:
            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                futures = {
                    executor.submit(
                        _fetch_with_retry,
                        code,
                        segment[0],
                        segment[1],
                        retries,
                        backoff,
                        limiter,
                    ): (code, segment)
                    for code, segments in candidates
                    for segment in segments
                }
                per_code_rows: Dict[str, int] = {}
                per_code_failed: Dict[str, str] = {}
                per_code_last_date: Dict[str, str] = {}
                per_code_name: Dict[str, str] = {}
                total_futures = len(futures)
                done = 0
                for future in as_completed(futures):
                    code, segment = futures[future]
                    done += 1
                    try:
                        rows, name = future.result()
                        if name:
                            per_code_name[code] = name
                        inserted = insert_prices(conn, rows)
                        total_rows += inserted
                        per_code_rows[code] = per_code_rows.get(code, 0) + inserted
                        if rows:
                            latest = max(row.trade_date for row in rows)
                            existing_latest = per_code_last_date.get(code)
                            if not existing_latest or latest > existing_latest:
                                per_code_last_date[code] = latest
                    except Exception as exc:
                        per_code_failed[code] = str(exc)
                        _append_log(
                            fail_log,
                            f"{dt.datetime.now().isoformat()} code={code} segment={segment[0]}~{segment[1]} range={start_date}~{end_date} error={exc}",
                        )
                    if progress:
                        if code in per_code_failed:
                            progress(int(done * 100 / total_futures), f"⚠ {code} 拉取失败: {per_code_failed[code]}")
                        else:
                            progress(int(done * 100 / total_futures), f"同步 {code} 完成（{done}/{total_futures}）")
                for code, rows in per_code_rows.items():
                    if code in per_code_failed:
                        continue
                    upsert_checkpoint(conn, job_id, code, start_date, end_date, "success", None)
                    _append_log(
                        success_log,
                        f"{dt.datetime.now().isoformat()} code={code} rows={rows} range={start_date}~{end_date}",
                    )
                for code, err in per_code_failed.items():
                    upsert_checkpoint(conn, job_id, code, start_date, end_date, "failed", err)
                upsert_fetch_offsets(conn, per_code_last_date)
                if per_code_name:
                    upsert_metadata(conn, [StockMeta(code=c, name=n, industry="", region="") for c, n in per_code_name.items()])
    logger.info("区间同步完成: symbols=%s rows=%s", len(mapping), total_rows)
    return {"symbols": len(mapping), "rows": total_rows}


def sync_hs300_metadata(
    db_path: str,
    concurrency: int,
    rate_limit: float,
    retries: int,
    backoff: float,
) -> Dict[str, int]:
    logger = get_logger()
    if concurrency <= 0:
        raise InvalidConfigError("concurrency 必须为正整数")
    if rate_limit < 0:
        raise InvalidConfigError("rate 必须为非负数")
    if retries < 0:
        raise InvalidConfigError("retries 必须为非负数")
    if backoff <= 0:
        raise InvalidConfigError("backoff 必须为正数")
    mapping = fetch_hs300_constituents()
    conn = open_db(db_path)
    if not mapping:
        with conn:
            codes = get_all_codes(conn)
        if not codes:
            raise FetchError("无法获取沪深300成分股列表，且本地数据库为空")
        mapping = {code: "" for code in codes}
        logger.warning("沪深300成分股获取失败，改用本地数据库代码列表")
    logger.info("元数据同步开始: symbols=%s", len(mapping))
    limiter = RateLimiter(rate_limit)
    with conn:
        if mapping:
            upsert_constituents(conn, mapping)
        metas: List[StockMeta] = []
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {
                executor.submit(_fetch_meta_with_retry, code, retries, backoff, limiter): code
                for code in sorted(mapping.keys())
            }
            for future in as_completed(futures):
                code = futures[future]
                try:
                    meta = future.result()
                    if meta:
                        metas.append(meta)
                except Exception as exc:
                    logger.warning("元数据同步失败: code=%s error=%s", code, exc)
        inserted = upsert_metadata(conn, metas)
    logger.info("元数据同步完成: symbols=%s rows=%s", len(mapping), inserted)
    return {"symbols": len(mapping), "rows": inserted}


def sync_fundamentals(
    db_path: str,
    concurrency: int = 4,
    rate_limit: float = 3.0,
    retries: int = 2,
    backoff: float = 1.0,
    progress: Optional[Callable[[int, str], None]] = None,
) -> Dict[str, int]:
    """同步基本面快照到 fundamentals 表。sector 目前只标注申万医药生物（其他策略未用）。"""
    from akshare_data_provider import fetch_fundamentals_akshare, fetch_sw_sector_codes
    from data_providers import fetch_dividends_5y

    logger = get_logger()
    conn = open_db(db_path)
    with conn:
        codes = get_all_codes(conn)
        closes = {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT code, close FROM daily_prices "
                "WHERE (code, trade_date) IN (SELECT code, MAX(trade_date) FROM daily_prices GROUP BY code)"
            ).fetchall()
        }
    if not codes:
        raise FetchError("无本地股票代码，无法同步基本面")

    try:
        pharma_codes = fetch_sw_sector_codes("801150.SI")
    except Exception as exc:  # noqa: BLE001
        pharma_codes = set()
        logger.warning("申万医药生物成分获取失败: %s", exc)

    try:
        dividends_map = fetch_dividends_5y()
    except Exception as exc:  # noqa: BLE001
        dividends_map = {}
        logger.warning("分红数据获取失败: %s", exc)

    limiter = RateLimiter(rate_limit)

    def _fetch(code: str):
        limiter.wait()
        row = fetch_fundamentals_akshare(
            code, float(closes.get(code) or 0.0), dividends_map.get(code, [])
        )
        if code in pharma_codes:
            row.sector = "医药生物"
        return row

    rows = []
    done = 0
    total = len(codes)
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {executor.submit(_fetch, code): code for code in sorted(codes)}
        for future in as_completed(futures):
            code = futures[future]
            try:
                rows.append(future.result())
            except Exception as exc:  # noqa: BLE001
                logger.warning("基本面同步失败: code=%s error=%s", code, exc)
            done += 1
            if progress:
                progress(int(done * 100 / total), f"基本面 {code}（{done}/{total}）")
    with conn:
        inserted = upsert_fundamentals(conn, rows)
    logger.info("基本面同步完成: symbols=%s rows=%s pharma=%s", total, inserted, len(pharma_codes))
    return {"symbols": total, "rows": inserted}


def _wrap_retry(func, retries: int, backoff: float):
    """Wrap a single-argument function with exponential-backoff retry."""
    def wrapper(code: str):
        last_error: Optional[Exception] = None
        for attempt in range(retries + 1):
            try:
                return func(code)
            except Exception as exc:
                last_error = exc
                if attempt >= retries:
                    break
                time.sleep(backoff * (2 ** attempt))
        if last_error:
            raise last_error
    return wrapper


def sync_fundamentals_history(
    db_path: str,
    *,
    concurrency: int = 4,
    rate_limit: float = 3.0,
    retries: int = 2,
    backoff: float = 1.0,
    progress: Optional[Callable[[int, str], None]] = None,
) -> Dict[str, int]:
    """同步历年财务指标到 fundamentals_history。失败单只股票降级日志。"""
    logger = get_logger()
    conn = open_db(db_path)
    with conn:
        codes = get_all_codes(conn)
    if not codes:
        raise FetchError("无本地股票代码，无法同步财务历史")

    limiter = RateLimiter(rate_limit)
    all_rows = []
    done = 0
    total = len(codes)
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {
            executor.submit(_wrap_retry(fetch_fundamentals_history_akshare, retries, backoff), code): code
            for code in sorted(codes)
        }
        for future in as_completed(futures):
            code = futures[future]
            try:
                rows = future.result() or []
                all_rows.extend(rows)
            except Exception as exc:  # noqa: BLE001
                logger.warning("财务历史同步失败: code=%s error=%s", code, exc)
            done += 1
            if progress:
                progress(int(done * 100 / total), f"财务历史 {code}（{done}/{total}）")

    with conn:
        inserted = upsert_fundamentals_history(conn, all_rows)
    logger.info("财务历史同步完成: symbols=%s rows=%s", total, inserted)
    return {"symbols": total, "rows": inserted}
