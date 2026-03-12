from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SyncConfig:
    mode: str
    limit: Optional[int]
    sleep_seconds: float = 0.05
