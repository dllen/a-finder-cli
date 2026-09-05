from dataclasses import dataclass
from typing import List, Tuple

from db_repository import FundamentalsHistoryRow


DEFAULT_INDUSTRIES: Tuple[str, ...] = (
    "医药生物", "中药", "食品饮料",
    "机械设备", "电力设备", "汽车整车",
)


@dataclass
class LinYuanConfig:
    industries: Tuple[str, ...] = DEFAULT_INDUSTRIES
    gross_margin_min: float = 0.40
    roe_excl_min: float = 0.15
    continuity_years: int = 5


def passes_linyuan_filter(
    sector: str,
    history: List[FundamentalsHistoryRow],
    config: LinYuanConfig,
) -> bool:
    """行业白名单 + 连续 N 年 (gross_margin > min AND roe_excl > min)。"""
    if sector not in config.industries:
        return False
    if len(history) < config.continuity_years:
        return False
    years_sorted = sorted(history, key=lambda r: r.year, reverse=True)
    # 必须是最近 N 年连续（按 year 排，相邻差 1）
    selected = years_sorted[: config.continuity_years]
    if len(selected) < config.continuity_years:
        return False
    for i in range(len(selected) - 1):
        if selected[i].year - selected[i + 1].year != 1:
            return False
    return all(
        (r.gross_margin or 0) > config.gross_margin_min
        and (r.roe_excl or 0) > config.roe_excl_min
        for r in selected
    )
