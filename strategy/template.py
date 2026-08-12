# alphaQuantSystem/strategy/template.py
"""
Strategy base class — StrategyContext pattern
Strategies access the world only through self.ctx; buy/sell go through SignalPipeline
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Optional, TYPE_CHECKING
import uuid

from loguru import logger

from alphaQuantSystem.core import (
    BarData, TickData, SignalData, TradeData, OrderData, Direction,
)
from alphaQuantSystem.core.context import StrategyContext

if TYPE_CHECKING:
    from alphaQuantSystem.engine.signal_pipeline import SignalPipeline


class BaseStrategy(ABC):
    """Strategy base class

    Subclass overrides lifecycle hooks:
    - on_init() / on_warmup_bar(bar) / on_start() / on_stop()
    - on_bar(bar) / on_tick(tick)
    - on_trade(trade) / on_order(order) / on_risk_block(...)
    """

    # Strategy parameters
    order_volume: int = 10000
    max_position_ratio: float = 0.25

    def __init__(self, ctx: StrategyContext):
        self.ctx = ctx
        self._pipeline: Optional["SignalPipeline"] = None

    def set_pipeline(self, pipeline: "SignalPipeline") -> None:
        self._pipeline = pipeline

    # ── Lifecycle ──
    def on_init(self) -> None: ...
    def on_warmup_bar(self, bar: BarData) -> None: ...
    def on_start(self) -> None: ...
    def on_stop(self) -> None: ...

    # ── Market data ──
    def on_bar(self, bar: BarData) -> None: ...
    def on_tick(self, tick: TickData) -> None: ...

    # ── Event callbacks ──
    def on_trade(self, trade: TradeData) -> None: ...
    def on_order(self, order: OrderData) -> None: ...
    def on_risk_block(
        self,
        reason: str,
        tag: Optional[str] = None,
        signal: Optional[SignalData] = None,
    ) -> None:
        logger.warning(f"[{self.__class__.__name__}] Signal blocked: {reason}")

    # ── Order placement ──
    def buy(
        self,
        symbol: str,
        volume: float,
        price: float = None,
        reason: str = "",
        tag: Optional[str] = None,
    ) -> None:
        """Emit buy signal -> SignalPipeline.submit()"""
        self._submit_signal(symbol, volume, price, Direction.LONG, reason, tag)

    def sell(
        self,
        symbol: str,
        volume: float,
        price: float = None,
        reason: str = "",
        tag: Optional[str] = None,
    ) -> None:
        """Emit sell signal -> SignalPipeline.submit()"""
        self._submit_signal(symbol, volume, price, Direction.SHORT, reason, tag)

    def _submit_signal(
        self,
        symbol: str,
        volume: float,
        price: Optional[float],
        direction: Direction,
        reason: str,
        tag: Optional[str],
    ) -> None:
        if self.ctx.is_warmup():
            return
        if volume <= 0 or not symbol:
            self.ctx.log(f"Invalid signal: symbol={symbol} volume={volume}", "WARNING")
            return

        signal = SignalData(
            strategy_id=self.__class__.__name__,
            symbol=symbol,
            direction=direction,
            volume=float(volume),
            price=float(price or 0.0),
            signal_id=str(uuid.uuid4()),
            tag=tag,
            reason=reason,
        )

        if self._pipeline is not None:
            self._pipeline.submit(signal)
        else:
            self.ctx.log("SignalPipeline not set, signal dropped", "WARNING")
