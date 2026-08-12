"""
价格盈亏风控策略（L3）

Skill 4.1 节：
  - 固定止盈止损：浮动盈亏比例 ≥ 止盈阈值 → 触发止盈平仓；亏损 ≥ 止损阈值 → 触发止损平仓
  - 动态追踪止盈：价格创新高后动态抬升止盈线，回落触发平仓
  - 阶梯止盈止损：多档位阈值匹配，依次执行部分平仓、全平
"""
from __future__ import annotations

from typing import Any, Dict, List

from . import BaseRiskPolicy, RiskAction, RiskEvent
from ..calc_layer import IndicatorSnapshot
from ..risk_limits import RiskLimits


class PriceRiskPolicy(BaseRiskPolicy):
    """L3 盈亏调控 —— 止盈止损/追踪/阶梯"""

    rule_id = "price_stop"
    priority = 3

    def __init__(self):
        # 追踪止盈状态: symbol → {"activated": bool, "highest_price": float, "trailing_line": float}
        self._trailing_state: Dict[str, Dict[str, Any]] = {}

    def evaluate(
        self,
        indicator: IndicatorSnapshot,
        limits: RiskLimits,
        monitor_when: str = "each_bar",
    ) -> List[RiskEvent]:
        events: List[RiskEvent] = []
        pnl_pct = indicator.unrealized_pnl_pct
        symbol = indicator.symbol
        sid = indicator.strategy_id
        ts = indicator.timestamp

        # 无持仓则跳过，同时清理追踪状态
        if pnl_pct == 0.0 and indicator.symbol_position_ratio == 0.0:
            self._trailing_state.pop(symbol, None)
            return events

        # ---- 阶梯止盈止损（优先于固定，因为更精细） ----
        step_events = self._check_stepped(indicator, limits)
        if step_events:
            # 阶梯已覆盖，跳过固定止盈止损（避免重复）
            events.extend(step_events)
            return events

        # ---- 固定止盈 ----
        tp_pct = limits.get_limit("take_profit_pct", 0.10)
        if pnl_pct >= tp_pct:
            events.append(RiskEvent(
                priority=self.priority,
                rule_id=f"{self.rule_id}.take_profit",
                action=RiskAction.FULL_CLOSE,
                reason=f"固定止盈触发: 浮动盈亏 {pnl_pct:.2%} ≥ 止盈阈值 {tp_pct:.2%}",
                symbol=symbol,
                strategy_id=sid,
                timestamp=ts,
                indicator_snapshot=indicator,
            ))
            self._trailing_state.pop(symbol, None)
            return events

        # ---- 固定止损 ----
        sl_pct = limits.get_limit("stop_loss_pct", 0.05)
        if pnl_pct <= -sl_pct:
            events.append(RiskEvent(
                priority=self.priority,
                rule_id=f"{self.rule_id}.stop_loss",
                action=RiskAction.FULL_CLOSE,
                reason=f"固定止损触发: 浮动盈亏 {pnl_pct:.2%} ≤ 止损阈值 {-sl_pct:.2%}",
                symbol=symbol,
                strategy_id=sid,
                timestamp=ts,
                indicator_snapshot=indicator,
            ))
            self._trailing_state.pop(symbol, None)
            return events

        # ---- 动态追踪止盈 ----
        trailing_event = self._check_trailing(indicator, limits)
        if trailing_event:
            events.append(trailing_event)

        return events

    def _check_stepped(
        self,
        indicator: IndicatorSnapshot,
        limits: RiskLimits,
    ) -> List[RiskEvent]:
        """阶梯止盈止损：多档位阈值，每档部分平仓"""
        pnl_pct = indicator.unrealized_pnl_pct
        step_tp = limits.get_limit("step_tp_levels", [])
        step_sl = limits.get_limit("step_sl_levels", [])
        ratios = limits.get_limit("step_close_ratios", [])

        if not ratios or (not step_tp and not step_sl):
            return []

        events = []

        # 阶梯止盈
        for i, level in enumerate(step_tp):
            if pnl_pct >= level:
                ratio = ratios[i] if i < len(ratios) else 1.0
                # 最后一档全平
                if i == len(step_tp) - 1 or ratio >= 1.0:
                    events.append(RiskEvent(
                        priority=self.priority,
                        rule_id=f"{self.rule_id}.step_tp_{i + 1}",
                        action=RiskAction.FULL_CLOSE,
                        reason=f"阶梯止盈第{i + 1}档触发: {pnl_pct:.2%} ≥ {level:.2%}（全平）",
                        symbol=indicator.symbol,
                        strategy_id=indicator.strategy_id,
                        timestamp=indicator.timestamp,
                        close_ratio=1.0,
                        indicator_snapshot=indicator,
                    ))
                    return events
                else:
                    events.append(RiskEvent(
                        priority=self.priority,
                        rule_id=f"{self.rule_id}.step_tp_{i + 1}",
                        action=RiskAction.PARTIAL_CLOSE,
                        reason=f"阶梯止盈第{i + 1}档触发: {pnl_pct:.2%} ≥ {level:.2%}（平{ratio:.0%}）",
                        symbol=indicator.symbol,
                        strategy_id=indicator.strategy_id,
                        timestamp=indicator.timestamp,
                        close_ratio=ratio,
                        indicator_snapshot=indicator,
                    ))

        # 阶梯止损
        if step_sl:
            for i, level in enumerate(step_sl):
                if pnl_pct <= -level:
                    ratio = ratios[i] if i < len(ratios) else 1.0
                    if i == len(step_sl) - 1 or ratio >= 1.0:
                        events.append(RiskEvent(
                            priority=self.priority,
                            rule_id=f"{self.rule_id}.step_sl_{i + 1}",
                            action=RiskAction.FULL_CLOSE,
                            reason=f"阶梯止损第{i + 1}档触发: {pnl_pct:.2%} ≤ {-level:.2%}（全平）",
                            symbol=indicator.symbol,
                            strategy_id=indicator.strategy_id,
                            timestamp=indicator.timestamp,
                            close_ratio=1.0,
                            indicator_snapshot=indicator,
                        ))
                        return events
                    else:
                        events.append(RiskEvent(
                            priority=self.priority,
                            rule_id=f"{self.rule_id}.step_sl_{i + 1}",
                            action=RiskAction.PARTIAL_CLOSE,
                            reason=f"阶梯止损第{i + 1}档触发: {pnl_pct:.2%} ≤ {-level:.2%}（平{ratio:.0%}）",
                            symbol=indicator.symbol,
                            strategy_id=indicator.strategy_id,
                            timestamp=indicator.timestamp,
                            close_ratio=ratio,
                            indicator_snapshot=indicator,
                        ))

        return events

    def _check_trailing(
        self,
        indicator: IndicatorSnapshot,
        limits: RiskLimits,
    ) -> RiskEvent | None:
        """动态追踪止盈：盈利达激活阈值后启用，从最高点回撤超阈值触发平仓"""
        pnl_pct = indicator.unrealized_pnl_pct
        activation = limits.get_limit("trailing_tp_activation", 0.08)
        callback = limits.get_limit("trailing_tp_callback", 0.03)
        symbol = indicator.symbol

        # 未激活：检查是否达到激活条件
        if pnl_pct < activation:
            return None

        # 初始化或更新最高价
        state = self._trailing_state.get(symbol)
        if state is None:
            self._trailing_state[symbol] = {
                "activated": True,
                "highest_pnl_pct": pnl_pct,
            }
            return None

        # 更新最高盈利
        if pnl_pct > state["highest_pnl_pct"]:
            state["highest_pnl_pct"] = pnl_pct
            self._trailing_state[symbol] = state
            return None

        # 回撤检测
        drawdown_from_peak = state["highest_pnl_pct"] - pnl_pct
        if drawdown_from_peak >= callback:
            self._trailing_state.pop(symbol, None)
            return RiskEvent(
                priority=self.priority,
                rule_id=f"{self.rule_id}.trailing_tp",
                action=RiskAction.FULL_CLOSE,
                reason=(
                    f"追踪止盈触发: 最高盈利 {state['highest_pnl_pct']:.2%}, "
                    f"当前 {pnl_pct:.2%}, 回撤 {drawdown_from_peak:.2%} ≥ 阈值 {callback:.2%}"
                ),
                symbol=symbol,
                strategy_id=indicator.strategy_id,
                timestamp=indicator.timestamp,
                indicator_snapshot=indicator,
            )

        return None
