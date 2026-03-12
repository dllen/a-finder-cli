import datetime as dt
import time
from typing import Callable, Optional, TypeVar

T = TypeVar("T")


def date_to_str(value: dt.date) -> str:
    return value.strftime("%Y%m%d")


def parse_date(value: str) -> dt.date:
    return dt.datetime.strptime(value, "%Y-%m-%d").date()


def retry_call(func: Callable[[], T], retries: int = 3, delay: float = 0.5, backoff: float = 1.5) -> T:
    last_error: Optional[Exception] = None
    current_delay = delay
    for _ in range(retries):
        try:
            return func()
        except Exception as exc:
            last_error = exc
            time.sleep(current_delay)
            current_delay *= backoff
    if last_error:
        raise last_error
    raise RuntimeError("retry_call failed")
