import datetime as dt
import random
import time
from typing import Callable, Optional, TypeVar

T = TypeVar("T")

# 只有网络/连接类异常才值得重试；HTTP 4xx（权限、字段错误等）重试无意义
import urllib.error

_RETRYABLE = (
    urllib.error.URLError,
    urllib.error.ContentTooShortError,
    ConnectionError,
    TimeoutError,
)


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, _RETRYABLE):
        return True
    # HTTPError 仅网络层错误（5xx、连接重置）才重试，4xx 不重试
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code is None or exc.code >= 500
    return False


def date_to_str(value: dt.date) -> str:
    return value.strftime("%Y%m%d")


def parse_date(value: str) -> dt.date:
    return dt.datetime.strptime(value, "%Y-%m-%d").date()


def retry_call(func: Callable[[], T], retries: int = 3, delay: float = 0.5, backoff: float = 1.5) -> T:
    last_error: Optional[Exception] = None
    current_delay = delay
    for attempt in range(retries):
        try:
            return func()
        except Exception as exc:
            last_error = exc
            # 非网络类异常不重试，直接抛出，避免对字段错误/权限问题浪费重试
            if not _is_retryable(exc):
                raise
            if attempt >= retries - 1:
                break
            # 指数退避 + 抖动，避免多线程同时重试造成二次限流
            time.sleep(current_delay * (1 + random.uniform(0, 0.5)))
            current_delay *= backoff
    if last_error:
        raise last_error
    raise RuntimeError("retry_call failed")
