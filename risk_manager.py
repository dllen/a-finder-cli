from dataclasses import dataclass
from market_regime import RegimeType


@dataclass
class PositionConfig:
    position_size: float       # 仓位比例 0-1
    stop_loss_pct: float       # 固定止损 %
    trailing_stop_pct: float   # 移动止损 %
    time_exit_days: int        # 时间止损（交易日）
    profit_target_pct: float   # 止盈目标 %


class RiskManager:
    """风险管理器"""

    # 各市场状态默认参数
    REGIME_CONFIGS = {
        RegimeType.BULL: {
            "position_size": 0.15,
            "stop_loss_pct": -0.08,
            "trailing_stop_pct": 0.05,
            "time_exit_days": 30,
            "profit_target_pct": 0.20,
        },
        RegimeType.BEAR: {
            "position_size": 0.08,
            "stop_loss_pct": -0.05,
            "trailing_stop_pct": 0.03,
            "time_exit_days": 10,
            "profit_target_pct": 0.10,
        },
        RegimeType.SIDEWAYS: {
            "position_size": 0.10,
            "stop_loss_pct": -0.05,
            "trailing_stop_pct": 0.03,
            "time_exit_days": 10,
            "profit_target_pct": 0.08,
        },
    }

    def get_config(self, regime: RegimeType, signal_strength: float = 1.0) -> PositionConfig:
        """
        获取指定市场状态的仓位配置

        Args:
            regime: 市场状态
            signal_strength: 信号强度 0-1，1=最强

        Returns:
            PositionConfig 对象
        """
        cfg = self.REGIME_CONFIGS.get(regime, self.REGIME_CONFIGS[RegimeType.SIDEWAYS])

        # 信号强度调整仓位
        position_size = cfg["position_size"] * (0.5 + signal_strength * 0.5)
        position_size = min(0.20, position_size)  # 最大20%

        return PositionConfig(
            position_size=position_size,
            stop_loss_pct=cfg["stop_loss_pct"],
            trailing_stop_pct=cfg["trailing_stop_pct"],
            time_exit_days=cfg["time_exit_days"],
            profit_target_pct=cfg["profit_target_pct"],
        )

    def should_stop_loss(self, entry_price: float, current_price: float,
                         highest_price: float, config: PositionConfig) -> tuple[bool, str]:
        """
        检查是否应该止损

        Returns:
            (should_stop, reason)
        """
        return_pct = (current_price / entry_price - 1)

        # 固定止损
        if return_pct <= config.stop_loss_pct:
            return True, f"固定止损 {return_pct:.2%}"

        # 移动止损
        trailing_stop_price = calculate_trailing_stop(
            entry_price, highest_price, config.trailing_stop_pct
        )
        if current_price <= trailing_stop_price and return_pct > 0:
            return True, f"移动止损 {return_pct:.2%}"

        return False, ""

    def should_take_profit(self, entry_price: float, current_price: float,
                           config: PositionConfig) -> tuple[bool, str]:
        """
        检查是否应该止盈
        """
        return_pct = (current_price / entry_price - 1)

        # 固定止盈
        if return_pct >= config.profit_target_pct:
            return True, f"目标止盈 {return_pct:.2%}"

        return False, ""


def calculate_trailing_stop(entry_price: float, highest_price: float,
                            trailing_pct: float = 0.05) -> float:
    """
    计算移动止损价格

    逻辑：
    - 盈利5% → 保本
    - 盈利10% → 锁定盈利5%
    - 盈利15% → 锁定盈利10%
    - 最高锁定盈利15%

    Args:
        entry_price: 入场价格
        highest_price: 持仓期间最高价
        trailing_pct: 移动止损百分比

    Returns:
        止损价格
    """
    profit_pct = highest_price / entry_price - 1

    if profit_pct <= 0.05:
        # 盈利不足5%，保本
        return entry_price

    # 止损价格跟随最高价，按trailing_pct设置
    stop_price = highest_price * (1 - trailing_pct)

    # 最低不能低于入场价
    return max(entry_price, stop_price)
