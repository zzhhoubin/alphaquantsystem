"""
异常交易风控策略（L1）

Skill 4.4 节：
  - 滑点偏差超阈值 → 记录异常 + 可配置拦截成交
  - 价格大幅跳空/涨跌停 → 撤单 + 暂停当期交易
  - 废单过多、重复委托、超时未成交 → 短时禁单保护
"""
from __future__ import annotations
from datetime import datetime
from typing import List

from . import BaseRiskPolicy, RiskAction, RiskEvent
from ..risk_limits import RiskLimits
from ..calc_layer import IndicatorSnapshot


class AbnormalRiskPolicy(BaseRiskPolicy):
    """L1 紧急拦截 —— 价格异动、委托异常、系统故障"""

    rule_id = "abnormal_stop"
    priority = 1

    def __init__(self):
        self._cancel_count: int = 0
        self._cancel_window_start: datetime | None = None

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

        # ---- 涨跌停板 ----
        if limits.get_limit("ban_limit_up_down", True):
            if indicator.is_limit_up:
                events.append(RiskEvent(
                    priority=self.priority,
                    rule_id=f"{self.rule_id}.limit_up",
                    action=RiskAction.PAUSE_OPEN,
                    reason=f"{symbol} 已涨停，暂停开仓",
                    symbol=symbol,
                    strategy_id=sid,
                    timestamp=ts,
                    indicator_snapshot=indicator,
                ))
            if indicator.is_limit_down:
                events.append(RiskEvent(
                    priority=self.priority,
                    rule_id=f"{self.rule_id}.limit_down",
                    action=RiskAction.FULL_CLOSE,
                    reason=f"{symbol} 已跌停，立即平仓",
                    symbol=symbol,
                    strategy_id=sid,
                    timestamp=ts,
                    indicator_snapshot=indicator,
                ))

        # ---- 价格跳空 ----
        gap_limit = limits.get_limit("gap_interval_pct", 0.03)
        if indicator.price_gap_pct > gap_limit:
            events.append(RiskEvent(
                priority=self.priority,
                rule_id=f"{self.rule_id}.price_gap",
                action=RiskAction.PAUSE_OPEN,
                reason=(
                    f"价格跳空 {indicator.price_gap_pct:.2%} > "
                    f"阈值 {gap_limit:.2%}，暂停当期开仓"
                ),
                symbol=symbol,
                strategy_id=sid,
                timestamp=ts,
                indicator_snapshot=indicator,
            ))

        # ---- 异常价格波动 ----
        abnormal_pct = limits.get_limit("abnormal_price_move_pct", 0.05)
        if abs(indicator.price_change_pct) > abnormal_pct:
            events.append(RiskEvent(
                priority=self.priority,
                rule_id=f"{self.rule_id}.abnormal_price",
                action=RiskAction.PAUSE_OPEN,
                reason=(
                    f"异常价格波动 {indicator.price_change_pct:.2%}，"
                    f"超过阈值 ±{abnormal_pct:.2%}"
                ),
                symbol=symbol,
                strategy_id=sid,
                timestamp=ts,
                indicator_snapshot=indicator,
            ))

        # ---- 滑点超限 ----
        max_slippage = limits.get_limit("max_slippage_bp", 20)
        if indicator.slippage_bp > max_slippage:
            slip_action = limits.get_limit("slippage_action", "log")
            action_map = {
                "log": RiskAction.ALERT,
                "reject": RiskAction.REJECT_ORDER,
                "pause": RiskAction.PAUSE_OPEN,
            }
            action = action_map.get(slip_action, RiskAction.ALERT)
            events.append(RiskEvent(
                priority=self.priority,
                rule_id=f"{self.rule_id}.slippage",
                action=action,
                reason=(
                    f"滑点偏差 {indicator.slippage_bp:.1f}bp > "
                    f"阈值 {max_slippage}bp"
                ),
                symbol=symbol,
                strategy_id=sid,
                timestamp=ts,
                indicator_snapshot=indicator,
            ))

        return events

    def record_cancel(self, timestamp: datetime, limits: RiskLimits):
        """记录一次撤单，检查是否触发废单过多保护。

        返回: 若触发保护则返回 RiskEvent，否则 None。
        """
        now = timestamp
        max_cancel = limits.get_limit("max_cancel_orders_per_minute", 5)

        # 滑动窗口重置
        if (
            self._cancel_window_start is None
            or (now - self._cancel_window_start).total_seconds() > 60
        ):
            self._cancel_window_start = now
            self._cancel_count = 0

        self._cancel_count += 1

        if self._cancel_count > max_cancel:
            action_name = limits.get_limit("cancel_exceed_action", "pause")
            action = RiskAction.LOCK_TRADE if action_name == "lock" else RiskAction.PAUSE_OPEN
            return RiskEvent(
                priority=self.priority,
                rule_id=f"{self.rule_id}.excess_cancel",
                action=action,
                reason=f"每分钟撤单 {self._cancel_count} 次 > 阈值 {max_cancel}",
                symbol="*",
                strategy_id="*",
                timestamp=now,
            )
        return None
