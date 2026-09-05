from dataclasses import dataclass
from datetime import date as _date
from typing import List, Optional, Tuple

from db_repository import (
    FundamentalsHistoryRow,
    open_db,
    get_fundamentals_history_by_code,
    get_metadata_by_code,
)
from strategies.multi_factor_base import SelectionResult, TargetPosition


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


class LinYuanRunner:
    """林园策略执行器：DB 读取 + 过滤 + 等权 top_n。"""

    def __init__(
        self,
        config: Optional[LinYuanConfig] = None,
        top_n: int = 20,
    ):
        self.config = config or LinYuanConfig()
        self.top_n = top_n

    def run(
        self,
        db_path: str,
        today: Optional[_date] = None,
    ) -> SelectionResult:
        today = today or _date.today()
        conn = open_db(db_path)
        try:
            codes = [
                r[0] for r in conn.execute("SELECT code FROM hs300_constituents").fetchall()
            ]
            if not codes:
                codes = [
                    r[0] for r in conn.execute(
                        "SELECT code FROM hs300_metadata"
                    ).fetchall()
                ]
            positions: List[TargetPosition] = []
            for code in codes:
                meta = get_metadata_by_code(conn, code)
                sector = (meta.industry if meta else "") or ""
                history = get_fundamentals_history_by_code(conn, code)
                if not passes_linyuan_filter(sector, history, self.config):
                    continue
                positions.append(TargetPosition(
                    code=code,
                    name=(meta.name if meta else code) or code,
                    weight=0.0,
                    score=0.0,
                    sector=sector,
                    sub_sector="",
                ))
        finally:
            conn.close()

        positions.sort(key=lambda p: p.code)
        n = min(len(positions), self.top_n)
        positions = positions[:n]
        if positions:
            w = 1.0 / len(positions)
            for p in positions:
                p.weight = w
        return SelectionResult(
            date=today,
            positions=positions,
            excluded=[],
            rebalance_reason=f"林园: 候选{len(codes)}只 通过{len(positions)}只",
        )
