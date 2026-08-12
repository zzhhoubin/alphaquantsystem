"""
执行调度层（Layer 4 / 6）

接收规则引擎事件，区分场景执行：回测模拟执行 / 实盘真实交易执行。

统一动作池（Skill 3.4）：
  REJECT_ORDER  - 拒单
  PARTIAL_CLOSE - 部分平仓
  FULL_CLOSE    - 全部平仓
  PAUSE_OPEN    - 暂停开仓
  LOCK_TRADE    - 永久锁仓
  ALERT         - 风控告警
"""
from __future__ import annotations
from typing import List

from loguru import logger

from alphaQuantSystem.core import (
    Direction, Event, EventEngine, EventType, OrderData, OrderType, SignalData,
)

from .policies import RiskAction, RiskEvent


class ExecLayer:
    """执行调度层：将 RiskEvent 转化为具体交易动作/事件。

    回测模式下发出模拟 ORDER_REQUEST；
    实盘模式下发出真实 ORDER_REQUEST，由 trader 层执行。
    两种场景共用同一套动作映射逻辑。
    """

    def __init__(self, event_engine: EventEngine, scene: str = "live"):
        self.event_engine = event_engine
        self.scene = scene

    def dispatch(self, events: List[RiskEvent]):
        """按优先级执行风控动作列表。

        已在 RuleEngine 中完成排序和截断，此处直接遍历执行。
        """
        for event in events:
            try:
                if event.action == RiskAction.REJECT_ORDER:
                    self._reject(event)
                elif event.action == RiskAction.PARTIAL_CLOSE:
                    self._partial_close(event)
                elif event.action == RiskAction.FULL_CLOSE:
                    self._full_close(event)
                elif event.action == RiskAction.PAUSE_OPEN:
                    self._pause_open(event)
                elif event.action == RiskAction.LOCK_TRADE:
                    self._lock(event)
                elif event.action == RiskAction.ALERT:
                    self._alert(event)
                else:
                    logger.warning(f"[ExecLayer] 未知动作类型: {event.action}")
            except Exception as e:
                logger.error(f"[ExecLayer] 执行风控动作失败: {event.action} | {e}")

    def _reject(self, event: RiskEvent):
        """拒单：发出 RISK_BLOCK 事件"""
        self.event_engine.put(Event(
            EventType.RISK_BLOCK,
            {"event": event, "reason": event.reason},
        ))
        logger.warning(f"[ExecLayer] 拒单: {event.symbol} | {event.reason}")

    def _partial_close(self, event: RiskEvent):
        """部分平仓：按 close_ratio 减持"""
        signal = SignalData(
            strategy_id=event.strategy_id,
            symbol=event.symbol,
            direction=Direction.SHORT,  # 平多仓 → 卖出方向
            volume=0,  # 实际 volume 由上层的持仓量 × close_ratio 决定
            price=0.0,
            event_time=event.timestamp,
            meta={
                "reason": f"风控部分平仓: {event.reason}",
                "close_ratio": event.close_ratio,
                "risk_event": event.rule_id,
            },
        )
        self.event_engine.put(Event(EventType.ORDER_REQUEST, signal))
        logger.warning(
            f"[ExecLayer] 部分平仓: {event.symbol} "
            f"比例={event.close_ratio:.0%} | {event.reason}"
        )

    def _full_close(self, event: RiskEvent):
        """全部平仓：清空指定标的持仓"""
        signal = SignalData(
            strategy_id=event.strategy_id,
            symbol=event.symbol,
            direction=Direction.SHORT,  # 平多仓
            volume=0,  # 全平时 volume=0 表示清仓
            price=0.0,
            event_time=event.timestamp,
            meta={
                "reason": f"风控全平: {event.reason}",
                "close_ratio": 1.0,
                "risk_event": event.rule_id,
            },
        )
        self.event_engine.put(Event(EventType.ORDER_REQUEST, signal))
        logger.warning(f"[ExecLayer] 全平: {event.symbol} | {event.reason}")

    def _pause_open(self, event: RiskEvent):
        """暂停开仓：发出 RISK_BLOCK 标记"""
        self.event_engine.put(Event(
            EventType.RISK_BLOCK,
            {
                "event": event,
                "reason": event.reason,
                "action": "PAUSE_OPEN",
            },
        ))
        logger.warning(f"[ExecLayer] 暂停开仓: {event.symbol} | {event.reason}")

    def _lock(self, event: RiskEvent):
        """永久锁仓：发出 RISK_BLOCK + LOCK 标记"""
        self.event_engine.put(Event(
            EventType.RISK_BLOCK,
            {
                "event": event,
                "reason": event.reason,
                "action": "LOCK_TRADE",
                "require_manual_unlock": True,
            },
        ))
        logger.error(f"[ExecLayer] 锁仓: {event.symbol} | {event.reason} | 需人工解锁")

    def _alert(self, event: RiskEvent):
        """告警：仅记录不拦截"""
        self.event_engine.put(Event(
            EventType.RISK_BLOCK,
            {
                "event": event,
                "reason": event.reason,
                "action": "ALERT",
                "block_trade": False,
            },
        ))
        logger.info(f"[ExecLayer] 告警: {event.symbol} | {event.reason}")
