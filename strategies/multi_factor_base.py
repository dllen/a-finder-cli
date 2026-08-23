from dataclasses import dataclass, field
from typing import Dict, List, Callable, Optional
from datetime import date
from enum import Enum
from domain_models import Stock
from indicators import z_score_normalize

class FactorDirection(Enum):
    HIGHER_IS_BETTER = 1
    LOWER_IS_BETTER = -1

@dataclass
class FactorConfig:
    name: str
    weight: float
    direction: FactorDirection
    get_value: Callable[[Stock], float]

@dataclass
class MultiFactorConfig:
    name: str
    factors: List[FactorConfig] = field(default_factory=list)
    top_n: int = 30
    max_weight: float = 0.05
    sector_limits: Dict[str, float] = field(default_factory=dict)
    sub_sector_limits: Dict[str, float] = field(default_factory=dict)
    rebalance_freq: str = "monthly"

    def validate(self) -> bool:
        total = sum(f.weight for f in self.factors)
        return abs(total - 1.0) < 1e-6

@dataclass
class TargetPosition:
    code: str
    name: str
    weight: float
    score: float
    sector: str = ""
    sub_sector: str = ""

@dataclass
class SelectionResult:
    date: date
    positions: List[TargetPosition]
    excluded: List[Dict] = field(default_factory=list)
    rebalance_reason: str = ""

class MultiFactorBase:
    def __init__(self, config: MultiFactorConfig):
        self.config = config

    def select(self, date: date, candidates: List[Stock]) -> SelectionResult:
        # 1. 过滤候选
        filtered = self._filter_candidates(candidates)
        if not filtered:
            # 过滤后为空时退回到仅满足基础数据要求的候选, 而非全部候选
            filtered = MultiFactorBase._filter_candidates(self, candidates)

        # 2. 计算Z-score
        z_scores = self._calculate_z_scores(filtered)

        # 3. 计算综合得分
        scores = self._calculate_composite_score(z_scores, len(filtered))

        # 4. 构建持仓
        positions = []
        for stock, score in zip(filtered, scores):
            positions.append(TargetPosition(
                code=stock.code,
                name=stock.name,
                weight=0.0,  # 待分配
                score=score,
                sector=stock.sector,
                sub_sector=stock.sub_sector
            ))

        # 5. 排序并应用约束
        positions.sort(key=lambda p: p.score, reverse=True)
        positions = self._apply_sector_constraints(positions)
        positions = self._rebalance_weights(positions)

        return SelectionResult(date=date, positions=positions[:self.config.top_n])

    def _filter_candidates(self, stocks: List[Stock]) -> List[Stock]:
        """子类可重写"""
        return [s for s in stocks if s.prices and len(s.prices) >= 120]

    def _calculate_z_scores(self, stocks: List[Stock]) -> Dict[str, List[float]]:
        result = {}
        for factor in self.config.factors:
            values = [factor.get_value(s) for s in stocks]
            higher = factor.direction == FactorDirection.HIGHER_IS_BETTER
            if not higher:
                # 缺失数据(<=0)在 lower-is-better 里会被当成最优，替换为最差正值
                positives = [v for v in values if v > 0]
                if positives:
                    worst = max(positives)
                    values = [v if v > 0 else worst for v in values]
            result[factor.name] = z_score_normalize(values, higher)
        return result

    def _calculate_composite_score(self, z_scores: Dict[str, List[float]], count: int) -> List[float]:
        weights = {f.name: f.weight for f in self.config.factors}
        scores = [0.0] * count
        for name, values in z_scores.items():
            w = weights.get(name, 0)
            for i, v in enumerate(values):
                scores[i] += v * w
        return scores

    def _apply_sector_constraints(self, positions: List[TargetPosition]) -> List[TargetPosition]:
        """应用行业权重上限(暂未实现, 本计划不包含行业上限逻辑)"""
        # 简化: 暂不实现,后续扩展
        return positions

    def _rebalance_weights(self, positions: List[TargetPosition]) -> List[TargetPosition]:
        """等权分配权重"""
        n = min(len(positions), self.config.top_n)
        weight = 1.0 / n if n > 0 else 0
        for p in positions[:n]:
            p.weight = min(weight, self.config.max_weight)
        return positions[:n]
