"""
统一风控状态机

状态枚举与流转规则，严格按 Skill 第八节定义：
  NORMAL → LIMIT_OPEN → PAUSE_TRADE → LOCKED（单向升级，人工解锁回退）

职责：
  - 维护策略全局风控状态
  - 控制开仓/平仓权限
  - 状态变更落日志、带时间戳、带触发规则ID
  - 幂等锁定：同一风险事件不重复触发状态变更
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from loguru import logger


class RiskState(str, Enum):
    """风控状态枚举"""
    NORMAL = "NORMAL"           # 正常交易：可开仓、可平仓
    LIMIT_OPEN = "LIMIT_OPEN"   # 禁止新开仓：可平仓减仓
    PAUSE_TRADE = "PAUSE_TRADE" # 暂停全部交易
    LOCKED = "LOCKED"           # 重度风控锁定，需人工解锁


# 状态单向升级链，数值越大越严重
_STATE_SEVERITY = {
    RiskState.NORMAL: 0,
    RiskState.LIMIT_OPEN: 1,
    RiskState.PAUSE_TRADE: 2,
    RiskState.LOCKED: 3,
}


@dataclass
class StateTransition:
    """状态变更记录"""
    from_state: RiskState
    to_state: RiskState
    trigger_rule_id: str
    reason: str
    timestamp: datetime


class RiskStateMachine:
    """策略全局风控状态机

    单向升级：NORMAL → LIMIT_OPEN → PAUSE_TRADE → LOCKED
    回退需人工调用 manual_reset()。
    """

    def __init__(self, strategy_id: str = ""):
        self.strategy_id = strategy_id
        self._state: RiskState = RiskState.NORMAL
        self._history: List[StateTransition] = []
        self._triggered_events: Dict[str, RiskState] = {}

    # ---- 状态查询 ----

    @property
    def state(self) -> RiskState:
        return self._state

    @property
    def history(self) -> List[StateTransition]:
        return list(self._history)

    def can_open(self) -> bool:
        """是否允许新开仓"""
        return self._state == RiskState.NORMAL

    def can_close(self) -> bool:
        """是否允许平仓"""
        return self._state in (RiskState.NORMAL, RiskState.LIMIT_OPEN)

    def can_trade(self) -> bool:
        """是否允许任何交易"""
        return self._state in (RiskState.NORMAL, RiskState.LIMIT_OPEN)

    # ---- 状态升级 ----

    def transition_to(
        self,
        target: RiskState,
        trigger_rule_id: str,
        reason: str,
        timestamp: Optional[datetime] = None,
    ) -> bool:
        """尝试升级到目标状态。

        单向升级规则：
          - 只允许向更严重的方向变更
          - 同级别不重复变更（幂等）
          - 状态变更成功返回 True，否则 False

        幂等保证：同一 trigger_rule_id 在当前状态下已触发过，不重复变更。
        """
        if timestamp is None:
            timestamp = datetime.now()

        current_sev = _STATE_SEVERITY[self._state]
        target_sev = _STATE_SEVERITY[target]

        if target_sev <= current_sev:
            logger.debug(
                f"[StateMachine] 状态升级被拒绝: {self._state.value} → {target.value} "
                f"(目标严重度 {target_sev} ≤ 当前 {current_sev})"
            )
            return False

        # 幂等检查：同一规则在当前状态阶段是否已触发
        dedup_key = f"{trigger_rule_id}:{self._state.value}"
        if dedup_key in self._triggered_events:
            logger.debug(
                f"[StateMachine] 规则 {trigger_rule_id} 已在 {self._state.value} 阶段触发过，跳过"
            )
            return False

        old_state = self._state
        self._state = target
        self._triggered_events[dedup_key] = target

        transition = StateTransition(
            from_state=old_state,
            to_state=target,
            trigger_rule_id=trigger_rule_id,
            reason=reason,
            timestamp=timestamp,
        )
        self._history.append(transition)

        logger.warning(
            f"[StateMachine] 风控状态升级: {old_state.value} → {target.value} | "
            f"规则={trigger_rule_id} | 原因={reason} | "
            f"策略={self.strategy_id} | 时间={timestamp.isoformat()}"
        )
        return True

    # ---- 人工解锁 ----

    def manual_reset(self, operator: str = "admin") -> bool:
        """人工解锁，重置到 NORMAL 状态。

        仅在 LOCKED 或 PAUSE_TRADE 状态下允许人工重置。
        重置后清除幂等记录，允许规则重新触发。
        """
        if self._state == RiskState.NORMAL:
            logger.info("[StateMachine] 当前已是 NORMAL 状态，无需重置")
            return False

        old_state = self._state
        self._state = RiskState.NORMAL
        self._triggered_events.clear()

        transition = StateTransition(
            from_state=old_state,
            to_state=RiskState.NORMAL,
            trigger_rule_id="MANUAL_RESET",
            reason=f"人工解锁，操作者: {operator}",
            timestamp=datetime.now(),
        )
        self._history.append(transition)

        logger.warning(
            f"[StateMachine] 人工重置: {old_state.value} → NORMAL | 操作者={operator}"
        )
        return True

    def unlock_open(self, operator: str = "admin") -> bool:
        """仅解除开仓限制，从 LIMIT_OPEN → NORMAL"""
        if self._state != RiskState.LIMIT_OPEN:
            return False
        self._state = RiskState.NORMAL
        transition = StateTransition(
            from_state=RiskState.LIMIT_OPEN,
            to_state=RiskState.NORMAL,
            trigger_rule_id="MANUAL_UNLOCK_OPEN",
            reason=f"人工解除开仓限制，操作者: {operator}",
            timestamp=datetime.now(),
        )
        self._history.append(transition)
        logger.warning(f"[StateMachine] 人工解除开仓限制: LIMIT_OPEN → NORMAL | 操作者={operator}")
        return True

    # ---- 状态快照 ----

    def snapshot(self) -> dict:
        """返回当前状态快照，用于日志落库、复盘追溯"""
        return {
            "strategy_id": self.strategy_id,
            "state": self._state.value,
            "can_open": self.can_open(),
            "can_close": self.can_close(),
            "history_count": len(self._history),
            "last_transition": (
                {
                    "from": self._history[-1].from_state.value,
                    "to": self._history[-1].to_state.value,
                    "rule_id": self._history[-1].trigger_rule_id,
                    "reason": self._history[-1].reason,
                    "timestamp": self._history[-1].timestamp.isoformat(),
                }
                if self._history
                else None
            ),
        }
