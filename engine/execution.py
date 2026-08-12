# alphaQuantSystem/engine/execution.py
"""Execution handlers — Signal -> Order -> Trade adaptation layer"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Optional, TYPE_CHECKING

from alphaQuantSystem.core import (
    SignalData, OrderData, TradeData, BarData, Direction, OrderType, Event, EventType,
)

if TYPE_CHECKING:
    from alphaQuantSystem.backtest.matching_engine import MatchingEngine
    from alphaQuantSystem.backtest.report import BacktestReporter
    from alphaQuantSystem.services.position import PositionService
    from alphaQuantSystem.services.account import AccountService
    from alphaQuantSystem.core.event_engine import EventEngine


class ExecutionHandler(ABC):
    """Abstract execution handler — backtest/live fork point"""

    @abstractmethod
    def execute(self, signal: SignalData, *, bar: Optional[BarData] = None) -> Optional[TradeData]:
        ...


class LiveExecution(ExecutionHandler):
    """Live execution — emit ORDER_REQUEST for QmtTrader / gateway."""

    def __init__(self, event_engine: Optional["EventEngine"] = None, gateway=None):
        self._event_engine = event_engine
        self._gateway = gateway

    def execute(self, signal: SignalData, *, bar: Optional[BarData] = None) -> Optional[TradeData]:
        order = OrderData(
            order_id="",
            strategy_id=signal.strategy_id,
            symbol=signal.symbol,
            direction=signal.direction,
            order_type=OrderType.LIMIT if signal.price > 0 else OrderType.MARKET,
            volume=signal.volume,
            price=signal.price,
            order_remark=signal.reason or signal.strategy_id,
            signal_id=signal.signal_id,
            tag=signal.tag,
        )
        if self._event_engine is not None:
            self._event_engine.put(Event(EventType.ORDER_REQUEST, order))
            return None
        if self._gateway is not None:
            self._gateway.send_order(order)
        return None


class BacktestExecution(ExecutionHandler):
    """Backtest execution — MatchingEngine match + Service settlement + Reporter.record_trade"""

    def __init__(
        self,
        matcher: "MatchingEngine",
        position_svc: "PositionService",
        account_svc: "AccountService",
        reporter: "BacktestReporter",
    ):
        self._matcher = matcher
        self._position_svc = position_svc
        self._account_svc = account_svc
        self._reporter = reporter
        matcher.set_available_cash_resolver(lambda: account_svc.available)
        matcher.set_hold_volume_resolver(
            lambda sym: (
                position_svc.get(sym).closeable_amount
                if position_svc.get(sym) is not None else 0.0
            )
        )

    def execute(self, signal: SignalData, *, bar: Optional[BarData] = None) -> Optional[TradeData]:
        order = OrderData(
            order_id="",
            strategy_id=signal.strategy_id,
            symbol=signal.symbol,
            direction=signal.direction,
            order_type=OrderType.MARKET if signal.price <= 0 else OrderType.LIMIT,
            volume=signal.volume,
            price=signal.price,
            signal_id=signal.signal_id,
            tag=signal.tag,
        )

        if bar is not None:
            trade = self._matcher.match(order, bar)
        else:
            trade = None

        if trade is None:
            return None

        trade.strategy_id = signal.strategy_id
        trade.signal_id = signal.signal_id
        trade.tag = signal.tag

        # 佣金仅在结算时计算并打一次日志（match 内部试算使用 quiet=True）
        commission = self._matcher.commission_model.calculate(trade, quiet=False)
        self._position_svc.apply_trade(trade)
        self._account_svc.apply_trade(trade, commission=commission)
        self._reporter.record_trade(trade)
        return trade
