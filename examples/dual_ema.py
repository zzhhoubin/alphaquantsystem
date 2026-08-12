# alphaQuantSystem/strategies/dual_ema.py
"""双均线策略: EMA(5)上穿EMA(10)买入，下穿卖出"""
from __future__ import annotations

from alphaQuantSystem import BaseStrategy
from alphaQuantSystem.core import BarData
from alphaQuantSystem.indicator.ma import EMA_Indicator
from alphaQuantSystem.monitor.trace import trace
from alphaQuantSystem.utils.helpers import round_volume


class DualEmaStrategy(BaseStrategy):
    """Dual EMA crossover strategy

    Parameters:
        fast_period: 快线周期 (default 5)
        slow_period: 慢线周期 (default 10)
        full_position: 金叉买入是否全仓 (default True)
        order_volume: full_position=False 时使用的固定买入股数
    """
    fast_period: int = 5
    slow_period: int = 10
    full_position: bool = True
    order_volume: int = 10000

    def on_init(self) -> None:
        trace("DualEma", "on_init", fast=self.fast_period, slow=self.slow_period)
        self._fast_ema = EMA_Indicator(self.fast_period)
        self._slow_ema = EMA_Indicator(self.slow_period)
        self._prev_fast: float | None = None
        self._prev_slow: float | None = None

    def _buy_volume(self, symbol: str, price: float) -> int:
        """全仓：按可用现金估算可买整手数；撮合层会再按佣金精确裁剪。"""
        if not self.full_position:
            return self.order_volume
        cash = self.ctx.portfolio.available_cash
        if price <= 0 or cash <= 0:
            return 0
        return round_volume(symbol, cash / price)

    def on_warmup_bar(self, bar: BarData) -> None:
        self._fast_ema.push(bar.close)
        self._slow_ema.push(bar.close)
        if self._fast_ema.is_ready and self._slow_ema.is_ready:
            self._prev_fast = self._fast_ema.value
            self._prev_slow = self._slow_ema.value
            trace(
                "DualEma", "warmup_bar ready",
                symbol=bar.symbol, time=bar.event_time, close=bar.close,
                fast=round(self._prev_fast, 4), slow=round(self._prev_slow, 4),
            )

    def on_bar(self, bar: BarData) -> None:
        self._fast_ema.push(bar.close)
        self._slow_ema.push(bar.close)

        if not self._fast_ema.is_ready or not self._slow_ema.is_ready:
            trace("DualEma", "on_bar skip (EMA not ready)", symbol=bar.symbol, time=bar.event_time)
            return

        fast_val = self._fast_ema.value
        slow_val = self._slow_ema.value

        pos = self.ctx.portfolio.positions.get(bar.symbol)
        holding = pos.total_amount if pos else 0

        trace(
            "DualEma", "on_bar",
            symbol=bar.symbol, time=bar.event_time, close=bar.close,
            fast=round(fast_val, 4), slow=round(slow_val, 4), holding=holding,
            prev_fast=round(self._prev_fast, 4) if self._prev_fast is not None else None,
            prev_slow=round(self._prev_slow, 4) if self._prev_slow is not None else None,
        )

        if self._prev_fast is not None and self._prev_slow is not None:
            # Golden cross: fast crosses above slow
            if self._prev_fast <= self._prev_slow and fast_val > slow_val and holding <= 0:
                buy_vol = self._buy_volume(bar.symbol, bar.close)
                trace("DualEma", "golden_cross signal", buy_vol=buy_vol, cash=self.ctx.portfolio.available_cash)
                if buy_vol > 0:
                    self.buy(bar.symbol, buy_vol, price=bar.close,
                             reason="EMA golden cross", tag="golden_cross")
            # Dead cross: fast crosses below slow
            elif self._prev_fast >= self._prev_slow and fast_val < slow_val and holding > 0:
                trace("DualEma", "dead_cross signal", sell_vol=holding)
                self.sell(bar.symbol, holding, price=bar.close,
                          reason="EMA dead cross", tag="dead_cross")

        self._prev_fast = fast_val
        self._prev_slow = slow_val
