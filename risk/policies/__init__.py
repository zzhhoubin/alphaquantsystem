"""
风控规则插件包

每个插件实现一类风控规则，通过统一接口 evaluate() 输出 RiskEvent 列表。
规则引擎负责加载、调度、优先级仲裁。

动作常量：
  REJECT_ORDER   - 拒单（拦截该笔委托）
  PARTIAL_CLOSE  - 部分平仓
  FULL_CLOSE     - 全部平仓
  PAUSE_OPEN     - 暂停新开仓
  LOCK_TRADE     - 永久锁仓（需人工解锁）
  ALERT          - 仅告警，不执行
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..risk_limits import RiskLimits
from ..calc_layer import IndicatorSnapshot


# ---- 风控动作常量 ----
class RiskAction:
    REJECT_ORDER = "REJECT_ORDER"
    PARTIAL_CLOSE = "PARTIAL_CLOSE"
    FULL_CLOSE = "FULL_CLOSE"
    PAUSE_OPEN = "PAUSE_OPEN"
    LOCK_TRADE = "LOCK_TRADE"
    ALERT = "ALERT"


@dataclass
class RiskEvent:
    """风控事件 —— 规则引擎判定输出

    priority: 1-4 (L1 紧急拦截 > L2 重度止损 > L3 盈亏调控 > L4 额度管控)
    """
    priority: int                           # 1-4
    rule_id: str                            # 触发规则ID
    action: str                             # 动作常量
    reason: str                             # 触发原因
    symbol: str
    strategy_id: str
    timestamp: datetime = field(default_factory=datetime.now)
    close_ratio: float = 1.0                # 平仓比例（PARTIAL_CLOSE 时使用）
    indicator_snapshot: Optional[IndicatorSnapshot] = None


class BaseRiskPolicy(ABC):
    """风控规则基类 —— 所有规则插件继承此基类

    子类必须提供:
      - rule_id: 规则唯一标识
      - priority: 优先级 1-4
      - evaluate(): 评估逻辑

    可选覆盖:
      - allowed_when: 该 policy 被允许的监控时机集合；None 表示不限（始终参与 evaluate）
    """

    rule_id: str = ""
    priority: int = 4  # 默认 L4 最低
    # 该规则被允许的监控时机；None 表示不限（始终参与 evaluate）
    # 非 None 时，仅在 monitor_when 属于该集合时才调用 evaluate()
    allowed_when: Optional[set] = None

    @abstractmethod
    def evaluate(
        self,
        indicator: IndicatorSnapshot,
        limits: RiskLimits,
        monitor_when: str = "each_bar",
    ) -> List[RiskEvent]:
        """评估风控指标，返回触发的事件列表

        Args:
            indicator: 风控指标快照
            limits: 风控参数配置
            monitor_when: 监控时机 — "each_bar" | "day_end"
        """
        ...

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(rule_id={self.rule_id}, priority=L{self.priority})"
