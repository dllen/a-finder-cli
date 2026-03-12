import logging
from typing import Optional


def get_logger(name: str = "a-finder") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger


def set_log_level(level: Optional[str]) -> None:
    if not level:
        return
    logging.getLogger("a-finder").setLevel(level.upper())
