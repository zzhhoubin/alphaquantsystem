"""
回撤与亏损风控策略（L2）

Skill 4.2 节：
  - 最大浮亏回撤超阈值 → 减仓 + 暂停新开仓
  - 当日累计亏损超阈值 → 禁止开仓、可配置一键清仓
  - 连续N笔/连续N周期亏损 → 冻结交易权限
"""
from __future__ import annotations
from datetime import datetime
from typing import List

from . import BaseRiskPolicy, RiskAction, RiskEvent
from ..risk_limits import RiskLimits
from ..calc_layer import IndicatorSnapshot


class DrawdownRiskPolicy(BaseRiskPolicy):
    """L2 重度风险止损 —— 回撤/亏损/连续亏损

    内部 gating:
      - max_drawdown: 任何时机都评估
      - daily_loss_pct / daily_max_loss / consecutive_*: 仅 monitor_when="day_end" 时评估
    """

    rule_id = "drawdown_stop"
    priority = 2
    allowed_when = None  # 始终参与 evaluate，内部根据 monitor_when 决定子规则

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
        is_day_end = monitor_when == "day_end"

        # ---- 最大回撤超标（本笔持仓周期内，峰值于开仓/清仓时重置）----
        # 仅清仓当前持仓，不升级状态机、不拦截后续开仓。任何时机都评估。
        max_dd = limits.get_limit("max_drawdown", 0.15)
        if (
            indicator.max_drawdown >= max_dd
            and indicator.symbol_position_ratio > 0
        ):
            events.append(RiskEvent(
                priority=self.priority,
                rule_id=f"{self.rule_id}.max_drawdown",
                action=RiskAction.FULL_CLOSE,
                reason=(
                    f"最大回撤超标: {indicator.max_drawdown:.2%} ≥ 阈值 {max_dd:.2%}，"
                    f"触发清仓"
                ),
                symbol=symbol,
                strategy_id=sid,
                timestamp=ts,
                indicator_snapshot=indicator,
            ))

        # 以下日终规则仅当 monitor_when="day_end" 时评估
        if not is_day_end:
            return events

        # ---- 单日绝对亏损超标 ----
        daily_max_loss = limits.get_limit("daily_max_loss", 500000)
        if indicator.realized_pnl_daily < -daily_max_loss:
            action = self._daily_loss_action(limits)
            events.append(RiskEvent(
                priority=self.priority,
                rule_id=f"{self.rule_id}.daily_loss_abs",
                action=action,
                reason=(
                    f"单日亏损超标: {indicator.realized_pnl_daily:.0f} ≤ "
                    f"阈值 {-daily_max_loss:.0f}"
                ),
                symbol=symbol,
                strategy_id=sid,
                timestamp=ts,
                indicator_snapshot=indicator,
            ))

        # ---- 单日亏损比例超标 ----
        daily_max_loss_pct = limits.get_limit("daily_max_loss_pct", 0.05)
        if indicator.daily_drawdown >= daily_max_loss_pct:
            action = self._daily_loss_action(limits)
            events.append(RiskEvent(
                priority=self.priority,
                rule_id=f"{self.rule_id}.daily_loss_pct",
                action=action,
                reason=(
                    f"单日回撤超标: {indicator.daily_drawdown:.2%} ≥ "
                    f"阈值 {daily_max_loss_pct:.2%}"
                ),
                symbol=symbol,
                strategy_id=sid,
                timestamp=ts,
                indicator_snapshot=indicator,
            ))

        # ---- 连续亏损笔数超标 ----
        consec_limit = limits.get_limit("consecutive_loss_limit", 5)
        if indicator.consecutive_losses >= consec_limit > 0:
            action_name = limits.get_limit("consecutive_loss_action", "freeze")
            action = RiskAction.LOCK_TRADE if action_name == "freeze" else RiskAction.PAUSE_OPEN
            events.append(RiskEvent(
                priority=self.priority,
                rule_id=f"{self.rule_id}.consecutive_losses",
                action=action,
                reason=(
                    f"连续亏损 {indicator.consecutive_losses} 笔 ≥ "
                    f"阈值 {consec_limit} 笔"
                ),
                symbol=symbol,
                strategy_id=sid,
                timestamp=ts,
                indicator_snapshot=indicator,
            ))

        # ---- 连续亏损周期超标 ----
        period_limit = limits.get_limit("consecutive_loss_periods_limit", 10)
        if indicator.consecutive_loss_periods >= period_limit > 0:
            action_name = limits.get_limit("consecutive_periods_action", "freeze")
            action = RiskAction.LOCK_TRADE if action_name == "freeze" else RiskAction.PAUSE_OPEN
            events.append(RiskEvent(
                priority=self.priority,
                rule_id=f"{self.rule_id}.consecutive_periods",
                action=action,
                reason=(
                    f"连续亏损 {indicator.consecutive_loss_periods} 周期 ≥ "
                    f"阈值 {period_limit} 周期"
                ),
                symbol=symbol,
                strategy_id=sid,
                timestamp=ts,
                indicator_snapshot=indicator,
            ))

        return events

    @staticmethod
    def _daily_loss_action(limits: RiskLimits) -> str:
        """解析单日亏损超限动作配置"""
        action_name = limits.get_limit("daily_loss_action", "limit_open")
        action_map = {
            "limit_open": RiskAction.PAUSE_OPEN,
            "pause_trade": RiskAction.LOCK_TRADE,
            "lock": RiskAction.LOCK_TRADE,
        }
        return action_map.get(action_name, RiskAction.PAUSE_OPEN)
