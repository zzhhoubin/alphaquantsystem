"""
时间周期风控策略

Skill 4.5 节：
  - 持仓时长超最大持有周期 → 强制平仓（L2）
  - 非允许交易时段 → 拦截开仓（L4）
"""
from __future__ import annotations
from datetime import datetime, time as dtime
from typing import List

from . import BaseRiskPolicy, RiskAction, RiskEvent
from ..risk_limits import RiskLimits
from ..calc_layer import IndicatorSnapshot


class TimeRiskPolicy(BaseRiskPolicy):
    """时间周期风控 —— 持仓超时(L2) + 交易时段(L4)

    本策略包含两个不同优先级的规则，因此 evaluate() 返回的事件可能有不同 priority。
    """

    rule_id = "time_risk"
    priority = 2  # 默认按最高（超时强制平仓为 L2）

    def evaluate(
        self,
        indicator: IndicatorSnapshot,
        limits: RiskLimits,
        monitor_when: str = "each_bar",
    ) -> List[RiskEvent]:
        events: List[RiskEvent] = []
        symbol = indicator.symbol
        sid = indicator.strategy_id
        ts = indicator.timestamp

        # ---- 持仓超时强制平仓（L2） ----
        max_periods = limits.get_limit("max_holding_periods", 20)
        if max_periods > 0 and indicator.holding_periods >= max_periods:
            events.append(RiskEvent(
                priority=2,  # L2 重度风险
                rule_id=f"{self.rule_id}.max_holding_periods",
                action=RiskAction.FULL_CLOSE,
                reason=(
                    f"持仓 {indicator.holding_periods} 周期 ≥ "
                    f"最大持有 {max_periods} 周期，强制平仓"
                ),
                symbol=symbol,
                strategy_id=sid,
                timestamp=ts,
                indicator_snapshot=indicator,
            ))

        # 日线回测时间戳为 00:00:00，秒级持仓时长会按自然日膨胀；回测仅用周期数
        max_seconds = limits.get_limit("max_holding_seconds", 14400)
        if (
            limits.scene != "backtest"
            and max_seconds > 0
            and indicator.holding_duration >= max_seconds
        ):
            events.append(RiskEvent(
                priority=2,  # L2
                rule_id=f"{self.rule_id}.max_holding_time",
                action=RiskAction.FULL_CLOSE,
                reason=(
                    f"持仓 {indicator.holding_duration:.0f}s ≥ "
                    f"最大持有 {max_seconds}s，强制平仓"
                ),
                symbol=symbol,
                strategy_id=sid,
                timestamp=ts,
                indicator_snapshot=indicator,
            ))

        # ---- 非交易时段拦截（L4） ----
        # 回测模式下跳过交易时段检查（Bar时间戳默认00:00:00不在任何窗口内）
        if limits.scene != "backtest":
            windows = limits.get_limit("trade_time_windows", [])
            if windows:
                now_t = ts.time() if hasattr(ts, 'time') else datetime.now().time()
                in_window = any(
                    dtime.fromisoformat(s) <= now_t <= dtime.fromisoformat(e)
                    for s, e in windows
                )
                if not in_window:
                    events.append(RiskEvent(
                        priority=4,
                        rule_id=f"{self.rule_id}.outside_hours",
                        action=RiskAction.REJECT_ORDER,
                        reason=f"当前时间 {now_t} 不在交易时段",
                        symbol=symbol,
                        strategy_id=sid,
                        timestamp=ts,
                        indicator_snapshot=indicator,
                    ))

        return events

    @staticmethod
    def check_trading_hours(
        timestamp: datetime,
        limits: RiskLimits,
    ) -> bool:
        """检查给定时间是否在交易时段内。回测模式始终返回 True。"""
        if limits.scene == "backtest":
            return True
        windows = limits.get_limit("trade_time_windows", [])
        if not windows:
            return True
        now_t = timestamp.time() if hasattr(timestamp, 'time') else datetime.now().time()
        return any(
            dtime.fromisoformat(s) <= now_t <= dtime.fromisoformat(e)
            for s, e in windows
        )
