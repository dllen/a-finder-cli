from strategies import (
    bollinger_rebound,
    box_breakout,
    kdj_cross,
    new_high,
    volume_price,
)

STRATEGIES = {
    "箱体突破": box_breakout.detect,
    "新高突破": new_high.detect,
    "布林超卖反弹": bollinger_rebound.detect,
    "KDJ低位金叉": kdj_cross.detect,
    "量价齐升": volume_price.detect,
}
