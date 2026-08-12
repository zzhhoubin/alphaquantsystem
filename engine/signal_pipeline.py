# alphaQuantSystem/engine/signal_pipeline.py
"""Signal pipeline — buy/sell -> submit -> drain -> RiskService -> ExecutionHandler"""
from __future__ import annotations
from collections import deque
from typing import Callable, Optional, TYPE_CHECKING

from loguru import logger

from alphaQuantSystem.core import SignalData, BarData, Direction, TradeData
from alphaQuantSystem.monitor.trace import trace
from alphaQuantSystem.services.risk import RiskContext

if TYPE_CHECKING:
    from alphaQuantSystem.services.risk import RiskService
    from alphaQuantSystem.engine.execution import ExecutionHandler
    from alphaQuantSystem.strategy.template import BaseStrategy


class SignalPipeline:
    """Single signal path: buy/sell -> submit() -> [pending] -> drain() -> RiskService -> ExecutionHandler"""

    def __init__(
        self,
        execution: "ExecutionHandler",
        risk: Optional["RiskService"] = None,
    ):
        self._execution = execution
        self._risk = risk
        self._pending: deque[SignalData] = deque()
        self._strategy: Optional["BaseStrategy"] = None
        self._trade_callback: Optional[Callable[["TradeData", Optional[BarData]], None]] = None

    def set_strategy(self, strategy: "BaseStrategy") -> None:
        self._strategy = strategy

    def set_trade_callback(
        self,
        callback: Optional[Callable[["TradeData", Optional[BarData]], None]],
    ) -> None:
        self._trade_callback = callback

    def submit(self, signal: SignalData) -> None:
        """Entry point for BaseStrategy.buy/sell: enqueue only"""
        side = "SELL" if signal.direction == Direction.SHORT else "BUY"
        trace(
            "Pipeline", "submit",
            side=side, symbol=signal.symbol, vol=signal.volume,
            price=signal.price, tag=signal.tag,
        )
        self._pending.append(signal)

    def _build_risk_context(
        self,
        signal: SignalData,
        bar: Optional[BarData],
    ) -> RiskContext:
        """从策略上下文关联的 PositionService / AccountService 填充风控上下文。"""
        if self._strategy is None:
            return RiskContext()

        strategy_ctx = self._strategy.ctx
        pos_svc = strategy_ctx._position_svc
        acc_svc = strategy_ctx._account_svc
        if pos_svc is None or acc_svc is None:
            return RiskContext()

        positions = pos_svc.snapshot()
        pos = positions.get(signal.symbol)
        position_volume = pos.volume if pos else 0.0
        position_market_value = pos.market_value if pos else 0.0
        if bar is not None:
            current_price = bar.close
        elif pos is not None and pos.current_price > 0:
            current_price = pos.current_price
        else:
            current_price = signal.price

        total_mv = pos_svc.total_market_value()
        return RiskContext(
            available_cash=acc_svc.available,
            total_value=acc_svc.total_value(total_mv),
            position_volume=position_volume,
            position_market_value=position_market_value,
            current_price=current_price,
            daily_pnl=acc_svc.daily_pnl,
            cumulative_pnl=acc_svc.cumulative_pnl,
            total_positions_count=len(positions),
        )

    def drain(self, *, bar: Optional["BarData"] = None) -> None:
        """Consume queue: 卖单先于买单处理，然后 RiskService.evaluate -> ExecutionHandler.execute。

        同一批 pending 中 SHORT（卖）先于 LONG（买）撮合；
        若策略同日既触发死叉又触发金叉，先平后开。
        """
        if not self._pending:
            return

        # 排序：卖单（SHORT）优先于买单（LONG），同方向保持原始顺序
        pending_list = list(self._pending)
        self._pending.clear()
        pending_list.sort(key=lambda s: 0 if s.direction == Direction.SHORT else 1)
        bar_time = bar.event_time if bar is not None else None
        trace("Pipeline", "drain start", count=len(pending_list), bar_time=bar_time)

        for signal in pending_list:
            side = "SELL" if signal.direction == Direction.SHORT else "BUY"
            trace("Pipeline", "evaluate", side=side, symbol=signal.symbol, vol=signal.volume, tag=signal.tag)
            if self._risk is not None:
                ctx = self._build_risk_context(signal, bar)
                result = self._risk.evaluate(signal, ctx, bar=bar)
                if not result.passed:
                    trace("Pipeline", "blocked", symbol=signal.symbol, reason=result.reason)
                    logger.info(f"[SignalPipeline] Signal blocked: {signal.symbol} reason={result.reason}")
                    if self._strategy is not None:
                        self._strategy.on_risk_block(reason=result.reason, tag=signal.tag, signal=signal)
                    continue

            trade = self._execution.execute(signal, bar=bar)
            if trade is not None:
                trade_day = (
                    trade.event_time.strftime("%Y-%m-%d")
                    if hasattr(trade.event_time, "strftime")
                    else str(trade.event_time)[:10]
                )
                side = "买入" if trade.direction == Direction.LONG else "卖出"
                tag_part = f" tag={trade.tag}" if trade.tag else ""
                logger.info(
                    f"[成交] {trade_day} {side} {trade.symbol} "
                    f"{trade.volume:.0f}股@{trade.price:.3f}{tag_part}"
                )
                if self._trade_callback is not None:
                    self._trade_callback(trade, bar)
                if self._strategy is not None:
                    self._strategy.on_trade(trade)
