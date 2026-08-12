"""
仓位风控策略（L4）

Skill 4.3 节：
  - 总仓位占比超限 → 拦截新开仓
  - 单标的仓位超限 → 拦截该标的开仓
  - 单笔开仓手数、单日开仓次数超限 → 直接拒单
"""
from __future__ import annotations
from datetime import datetime
from typing import List

from . import BaseRiskPolicy, RiskAction, RiskEvent
from ..risk_limits import RiskLimits
from ..calc_layer import IndicatorSnapshot


class PositionRiskPolicy(BaseRiskPolicy):
    """L4 额度管控 —— 仓位/手数/频率"""

    rule_id = "position_limit"
    priority = 4

    def __init__(self):
        self._daily_trade_count: int = 0
        self._last_trade_time: datetime | None = None
        self._daily_date = None

    def evaluate(
        self,
        indicator: IndicatorSnapshot,
        limits: RiskLimits,
        monitor_when: str = "each_bar",
    ) -> List[RiskEvent]:
        """持仓监控阶段不重复做 L4 仓位占比判定。

        全仓策略持仓期间占比恒≈100%，若在 evaluate 里判 REJECT_ORDER 只会误报；
        仓位/名义金额/频率限制统一在 check_signal（开仓预检）处理。
        """
        return []

    def check_signal(
        self,
        volume: float,
        price: float,
        symbol: str,
        strategy_id: str,
        timestamp: datetime,
        limits: RiskLimits,
    ) -> List[RiskEvent]:
        """校验单笔交易信号的仓位/手数/频率限制（在 RuleEngine._check_signal 调用）"""
        events: List[RiskEvent] = []

        # 单笔手数上限
        max_qty = limits.get_limit("per_symbol_max_qty", 1000000)
        if volume > max_qty:
            events.append(RiskEvent(
                priority=self.priority,
                rule_id=f"{self.rule_id}.max_qty",
                action=RiskAction.REJECT_ORDER,
                reason=f"单笔委托量 {volume} > 上限 {max_qty}",
                symbol=symbol,
                strategy_id=strategy_id,
                timestamp=timestamp,
            ))

        # 单笔手数下限
        min_qty = limits.get_limit("per_symbol_min_qty", 100)
        if 0 < volume < min_qty:
            events.append(RiskEvent(
                priority=self.priority,
                rule_id=f"{self.rule_id}.min_qty",
                action=RiskAction.REJECT_ORDER,
                reason=f"单笔委托量 {volume} < 下限 {min_qty}",
                symbol=symbol,
                strategy_id=strategy_id,
                timestamp=timestamp,
            ))

        # 名义金额上限
        if price > 0:
            notional = volume * price
            max_notional = limits.get_limit("max_order_notional", 1000000)
            if notional > max_notional:
                events.append(RiskEvent(
                    priority=self.priority,
                    rule_id=f"{self.rule_id}.max_notional",
                    action=RiskAction.REJECT_ORDER,
                    reason=f"名义金额 {notional:.0f} > 上限 {max_notional}",
                    symbol=symbol,
                    strategy_id=strategy_id,
                    timestamp=timestamp,
                ))

            min_notional = limits.get_limit("min_order_notional", 5000)
            if notional < min_notional:
                events.append(RiskEvent(
                    priority=self.priority,
                    rule_id=f"{self.rule_id}.min_notional",
                    action=RiskAction.REJECT_ORDER,
                    reason=f"名义金额 {notional:.0f} < 下限 {min_notional}",
                    symbol=symbol,
                    strategy_id=strategy_id,
                    timestamp=timestamp,
                ))

        # 单日交易次数
        today = timestamp.date() if hasattr(timestamp, 'date') else None
        if today is not None:
            if self._daily_date != today:
                self._daily_date = today
                self._daily_trade_count = 0
            max_trades = limits.get_limit("max_trades_per_day", 200)
            if self._daily_trade_count >= max_trades:
                events.append(RiskEvent(
                    priority=self.priority,
                    rule_id=f"{self.rule_id}.max_trades",
                    action=RiskAction.REJECT_ORDER,
                    reason=f"单日交易次数 {self._daily_trade_count} ≥ 上限 {max_trades}",
                    symbol=symbol,
                    strategy_id=strategy_id,
                    timestamp=timestamp,
                ))

        # 冷却时间
        cooldown = limits.get_limit("cooldown_seconds", 3)
        if self._last_trade_time:
            elapsed = (timestamp - self._last_trade_time).total_seconds()
            if elapsed < cooldown:
                events.append(RiskEvent(
                    priority=self.priority,
                    rule_id=f"{self.rule_id}.cooldown",
                    action=RiskAction.REJECT_ORDER,
                    reason=f"冷却中: {elapsed:.1f}s < {cooldown}s",
                    symbol=symbol,
                    strategy_id=strategy_id,
                    timestamp=timestamp,
                ))

        return events

    def record_trade(self, timestamp: datetime):
        """记录一次通过风控的交易"""
        self._last_trade_time = timestamp
        today = timestamp.date() if hasattr(timestamp, 'date') else None
        if today is not None:
            if self._daily_date != today:
                self._daily_date = today
                self._daily_trade_count = 0
            self._daily_trade_count += 1
