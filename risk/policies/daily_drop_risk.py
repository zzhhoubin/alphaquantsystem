"""
当日跌幅风控策略（L3）

检测当日价格相对昨收的跌幅是否超过阈值，触发则全仓平仓。
与 PriceRiskPolicy（基于持仓浮动盈亏的止损）互补：
  - PriceRiskPolicy.stop_loss：基于持仓成本价 → 防止持仓亏损扩大
  - DailyDropRiskPolicy：基于当日涨跌幅 → 防止持仓标的单日暴跌
"""
from __future__ import annotations

from typing import List

from . import BaseRiskPolicy, RiskAction, RiskEvent
from ..calc_layer import IndicatorSnapshot
from ..risk_limits import RiskLimits


class DailyDropRiskPolicy(BaseRiskPolicy):
    """L3 当日跌幅止损 —— 检测当日价格相对昨收跌幅是否超过阈值

    仅日终评估：当日跌幅在盘中无法准确判断，日终数据最可靠。
    """

    rule_id = "daily_drop_stop"
    priority = 3
    allowed_when = {"day_end"}

    def evaluate(
        self,
        indicator: IndicatorSnapshot,
        limits: RiskLimits,
        monitor_when: str = "each_bar",
    ) -> List[RiskEvent]:
        threshold = limits.get_limit("daily_drop_stop_pct", 0.05)

        # price_change_pct = (last_price - pre_close) / pre_close
        # 实盘 tick 有 pre_close 可正确计算；日线回测 pre_close=0 时
        # CalcLayer 返回 0.0，此时不触发（等价原版时间过滤行为）
        if indicator.price_change_pct >= 0:
            return []

        if indicator.price_change_pct <= -threshold:
            return [RiskEvent(
                priority=self.priority,
                rule_id=f"{self.rule_id}.stop_loss",
                action=RiskAction.FULL_CLOSE,
                reason=(
                    f"当日跌幅止损触发: 当日跌幅 {indicator.price_change_pct:.2%} "
                    f"≤ 阈值 {-threshold:.2%}"
                ),
                symbol=indicator.symbol,
                strategy_id=indicator.strategy_id,
                timestamp=indicator.timestamp,
                indicator_snapshot=indicator,
            )]

        return []
