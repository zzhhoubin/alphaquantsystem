"""
规则引擎层（Layer 3 / 6）

加载用户/默认风控参数，基于指标快照做阈值判定，输出风控事件与对应动作。

能力（Skill 3.3）：
  - 支持多规则并存
  - 优先级仲裁（L1 > L2 > L3 > L4）
  - 状态幂等锁定（同一事件不重复触发）
  - 回测/实盘共用同一套判定代码
"""
from __future__ import annotations
from datetime import datetime
from typing import Dict, List, Optional

from loguru import logger

from alphaQuantSystem.core import Direction

from .policies import BaseRiskPolicy, RiskAction, RiskEvent
from .policies.price_risk import PriceRiskPolicy
from .policies.daily_drop_risk import DailyDropRiskPolicy
from .policies.drawdown_risk import DrawdownRiskPolicy
from .policies.position_risk import PositionRiskPolicy
from .policies.abnormal_risk import AbnormalRiskPolicy
from .policies.time_risk import TimeRiskPolicy
from .risk_limits import RiskLimits
from .calc_layer import IndicatorSnapshot
from .state_machine import RiskState, RiskStateMachine


class RuleEngine:
    """规则引擎：加载规则插件 → 逐策略评估 → 优先级排序 → 幂等去重 → 输出动作列表

    回测/实盘共用同一套判定代码（Skill 核心原则）。
    所有规则插件注册后按 priority 排序执行。
    """

    def __init__(self, state_machine: RiskStateMachine, risk_limits: RiskLimits):
        self.state_machine = state_machine
        self.limits = risk_limits
        self._policies: List[BaseRiskPolicy] = []
        # 已触发事件缓存: key = f"{rule_id}:{symbol}" → RiskEvent
        self._triggered_cache: Dict[str, RiskEvent] = {}

    def register_policy(self, policy: BaseRiskPolicy):
        """注册风控策略插件"""
        self._policies.append(policy)
        self._policies.sort(key=lambda p: p.priority)
        logger.debug(f"[RuleEngine] 注册策略: {policy}")

    def register_default_policies(self):
        """注册全部 5 个标准风控策略（L1-L4）"""
        self.register_policy(AbnormalRiskPolicy())
        self.register_policy(TimeRiskPolicy())
        self.register_policy(DrawdownRiskPolicy())
        self.register_policy(PriceRiskPolicy())
        self.register_policy(DailyDropRiskPolicy())
        self.register_policy(PositionRiskPolicy())
        logger.info(
            f"[RuleEngine] 已注册 {len(self._policies)} 个风控策略: "
            + ", ".join(p.rule_id for p in self._policies)
        )

    # ---- 持仓监控评估（逐Tick/Bar时调用） ----

    def evaluate(self, indicator: IndicatorSnapshot, monitor_when: str = "each_bar") -> List[RiskEvent]:
        """运行全部策略，返回优先级排序后的可行事件列表。

        Args:
            indicator: 风控指标快照
            monitor_when: 监控时机 — "each_bar" | "day_end"；
                          用于按 policy.allowed_when 过滤及 policy 内部 gating

        执行步骤:
          1. 按 policy.allowed_when 过滤不匹配当前监控时机的策略
          2. 遍历剩余策略，收集触发的 RiskEvent
          3. 按 priority 升序排序（L1 在前）
          4. 幂等去重：同一 rule_id + symbol 在当前状态下不重复
          5. 高优先级覆盖低优先级
        """
        all_events: List[RiskEvent] = []

        for policy in self._policies:
            # 按 monitor_when 过滤：仅当 policy 允许当前时机时才评估
            if policy.allowed_when is not None and monitor_when not in policy.allowed_when:
                continue
            policy_events = policy.evaluate(indicator, self.limits, monitor_when=monitor_when)
            all_events.extend(policy_events)

        if not all_events:
            return []

        # 按优先级排序（L1=1 在前）
        all_events.sort(key=lambda e: e.priority)

        # 去重 + 幂等
        final_events: List[RiskEvent] = []
        seen_keys: set = set()

        for event in all_events:
            dedup_key = f"{event.rule_id}:{event.symbol}:{event.action}"
            if dedup_key in seen_keys:
                continue
            # 幂等：同一规则+标的+动作不重复（勿含状态机，避免升级后重复触发/重复记日志）
            cache_key = f"{event.rule_id}:{event.symbol}:{event.action}"
            if cache_key in self._triggered_cache:
                logger.debug(f"[RuleEngine] 幂等跳过: {event.rule_id} @ {event.symbol}")
                continue
            seen_keys.add(dedup_key)
            self._triggered_cache[cache_key] = event
            final_events.append(event)

        # 高优先级截断：L1/L2 动作覆盖低优先级
        final_events = self._apply_priority_override(final_events)

        return final_events

    # ---- 信号预检（开仓前调用） ----

    def check_signal(
        self,
        symbol: str,
        strategy_id: str,
        volume: float,
        price: float,
        timestamp: datetime,
        direction: Direction = Direction.LONG,
    ) -> List[RiskEvent]:
        """信号预检：开仓走 L4 额度；平仓仅做名单/时段校验。

        与持仓监控 evaluate() 互补：
          - evaluate() 针对已有持仓做 L1-L3 检测
          - check_signal() 针对策略信号做预检
        """
        events: List[RiskEvent] = []
        is_open = direction == Direction.LONG

        # 黑名单检查
        blacklist = self.limits.get_limit("blacklist_symbols", [])
        if symbol[:6] in blacklist:
            events.append(RiskEvent(
                priority=1,
                rule_id="signal.blacklist",
                action=RiskAction.REJECT_ORDER,
                reason=f"标的 {symbol[:6]} 在黑名单中",
                symbol=symbol,
                strategy_id=strategy_id,
                timestamp=timestamp,
            ))

        # 白名单检查
        whitelist = self.limits.get_limit("whitelist_symbols", [])
        if whitelist and symbol[:6] not in whitelist:
            events.append(RiskEvent(
                priority=1,
                rule_id="signal.whitelist",
                action=RiskAction.REJECT_ORDER,
                reason=f"标的 {symbol[:6]} 不在白名单中",
                symbol=symbol,
                strategy_id=strategy_id,
                timestamp=timestamp,
            ))

        # 状态机：仅拦截开仓
        if is_open and not self.state_machine.can_open():
            events.append(RiskEvent(
                priority=2,
                rule_id="signal.state_blocked",
                action=RiskAction.REJECT_ORDER,
                reason=f"风控状态 {self.state_machine.state.value} 禁止开仓",
                symbol=symbol,
                strategy_id=strategy_id,
                timestamp=timestamp,
            ))

        # L4 仓位/手数/频率：仅拦截开仓
        if is_open:
            for policy in self._policies:
                if isinstance(policy, PositionRiskPolicy):
                    pp_events = policy.check_signal(
                        volume=volume,
                        price=price,
                        symbol=symbol,
                        strategy_id=strategy_id,
                        timestamp=timestamp,
                        limits=self.limits,
                    )
                    events.extend(pp_events)

        # 时段检查
        from .policies.time_risk import TimeRiskPolicy
        if not TimeRiskPolicy.check_trading_hours(timestamp, self.limits):
            events.append(RiskEvent(
                priority=4,
                rule_id="signal.outside_hours",
                action=RiskAction.REJECT_ORDER,
                reason="当前不在交易时段",
                symbol=symbol,
                strategy_id=strategy_id,
                timestamp=timestamp,
            ))

        return events

    # ---- 状态同步 ----

    def apply_state_transitions(self, events: List[RiskEvent]):
        """根据风控事件更新状态机

        严格按照 Skill 第五节优先级：
          LOCK_TRADE   → LOCKED
          PAUSE_OPEN   → LIMIT_OPEN（至少）
          FULL_CLOSE   → 不升级状态（仅执行）
          REJECT_ORDER → 不升级状态（仅拦截）
        """
        for event in sorted(events, key=lambda e: e.priority):
            if event.action == RiskAction.LOCK_TRADE:
                self.state_machine.transition_to(
                    RiskState.LOCKED,
                    trigger_rule_id=event.rule_id,
                    reason=event.reason,
                    timestamp=event.timestamp,
                )
            elif event.action == RiskAction.PAUSE_OPEN:
                # 至少升级到 LIMIT_OPEN；如果当前已是 LIMIT_OPEN 则不重复
                if self.state_machine.state == RiskState.NORMAL:
                    self.state_machine.transition_to(
                        RiskState.LIMIT_OPEN,
                        trigger_rule_id=event.rule_id,
                        reason=event.reason,
                        timestamp=event.timestamp,
                    )

    def clear_idempotency_cache(self):
        """清除幂等缓存（人工重置后调用）"""
        self._triggered_cache.clear()

    def clear_symbol_cache(self, symbol: str) -> None:
        """清除指定标的的幂等缓存（平仓后/新开仓前调用）。

        否则上一笔持仓触发的 price_stop.stop_loss 会阻止后续持仓再次止损。
        """
        if not symbol:
            return
        prefix = f":{symbol}:"
        stale = [k for k in self._triggered_cache if prefix in k]
        for k in stale:
            del self._triggered_cache[k]

    def get_position_risk_policy(self) -> Optional[PositionRiskPolicy]:
        """获取仓位风控策略实例（用于记录交易计数）"""
        for p in self._policies:
            if isinstance(p, PositionRiskPolicy):
                return p
        return None

    def get_abnormal_risk_policy(self) -> Optional[AbnormalRiskPolicy]:
        """获取异常交易策略实例（用于记录撤单）"""
        for p in self._policies:
            if isinstance(p, AbnormalRiskPolicy):
                return p
        return None

    # ---- 内部方法 ----

    @staticmethod
    def _apply_priority_override(events: List[RiskEvent]) -> List[RiskEvent]:
        """高优先级覆盖低优先级：L1/L2 动作截断 L3/L4。

        规则（Skill 第五节）：
          L1 触发 → 立即终止交易，忽略 L2-L4
          L2 触发 → 忽略 L3/L4 中的低优先级动作
        """
        if not events:
            return events

        has_l1_lock = any(
            e.priority == 1 and e.action in (RiskAction.LOCK_TRADE, RiskAction.PAUSE_OPEN)
            for e in events
        )
        if has_l1_lock:
            # L1 截断：只保留 L1 事件
            return [e for e in events if e.priority == 1]

        has_l2_lock = any(
            e.priority == 2 and e.action in (RiskAction.LOCK_TRADE, RiskAction.PAUSE_OPEN)
            for e in events
        )
        if has_l2_lock:
            # L2 截断：保留 L1 + L2，丢弃 L3/L4
            return [e for e in events if e.priority <= 2]

        return events
