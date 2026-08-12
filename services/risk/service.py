# alphaQuantSystem/services/risk/service.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from alphaQuantSystem.core import SignalData, BarData


@dataclass
class RiskResult:
    passed: bool
    reason: str = ""


class RiskRule:
    """Single risk rule — pluggable"""
    def __init__(self, name: str, check: Callable[[SignalData, "RiskContext"], RiskResult],
                 priority: int = 0):
        self.name = name
        self._check = check
        self.priority = priority

    def evaluate(self, signal: SignalData, ctx: "RiskContext") -> RiskResult:
        return self._check(signal, ctx)

    def __repr__(self):
        return f"RiskRule({self.name!r}, priority={self.priority})"


@dataclass
class RiskContext:
    """Risk evaluation context — built by SignalPipeline before drain"""
    available_cash: float = 0.0
    total_value: float = 0.0
    position_volume: float = 0.0
    position_market_value: float = 0.0
    current_price: float = 0.0
    daily_pnl: float = 0.0
    cumulative_pnl: float = 0.0
    total_positions_count: int = 0


class RiskService:
    """Per-strategy risk service"""

    def __init__(self, strategy_id: str):
        self.strategy_id = strategy_id
        self._rules: List[RiskRule] = []
        self._global_defaults: List[RiskRule] = []

    def add_rule(self, rule: RiskRule) -> None:
        self._rules.append(rule)
        self._rules.sort(key=lambda r: r.priority)

    def set_global_defaults(self, rules: List[RiskRule]) -> None:
        self._global_defaults = rules

    def add_builtin_rules(self, limits: Dict[str, Any]) -> None:
        """Load built-in rules from config dict"""
        if "max_order_notional" in limits:
            limit = float(limits["max_order_notional"])
            def _notional(signal: SignalData, ctx: RiskContext) -> RiskResult:
                exposure = signal.volume * (signal.price or ctx.current_price)
                if exposure > limit:
                    return RiskResult(False, f"Notional {exposure:,.0f} > {limit:,.0f}")
                return RiskResult(True)
            self.add_rule(RiskRule("max_order_notional", _notional, priority=10))

        if "max_positions" in limits:
            limit = int(limits["max_positions"])
            def _pos_count(signal: SignalData, ctx: RiskContext) -> RiskResult:
                if ctx.total_positions_count >= limit and ctx.position_volume <= 0:
                    return RiskResult(False, f"Positions {ctx.total_positions_count} >= {limit}")
                return RiskResult(True)
            self.add_rule(RiskRule("max_positions", _pos_count, priority=20))

        if "max_drawdown" in limits:
            limit = float(limits["max_drawdown"])
            def _drawdown(signal: SignalData, ctx: RiskContext) -> RiskResult:
                if ctx.total_value > 0:
                    dd = abs(ctx.cumulative_pnl) / (ctx.total_value - ctx.cumulative_pnl)
                    if dd > limit:
                        return RiskResult(False, f"Drawdown {dd:.2%} > {limit:.2%}")
                return RiskResult(True)
            self.add_rule(RiskRule("max_drawdown", _drawdown, priority=5))

    def evaluate(
        self,
        signal: SignalData,
        ctx: RiskContext,
        *,
        bar: Optional["BarData"] = None,
    ) -> RiskResult:
        for rule in self._rules:
            result = rule.evaluate(signal, ctx)
            if not result.passed:
                return result
        for rule in self._global_defaults:
            result = rule.evaluate(signal, ctx)
            if not result.passed:
                return result
        return RiskResult(True)
