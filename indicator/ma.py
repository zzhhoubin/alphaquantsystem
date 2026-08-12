"""
indicator.ma
============
均线类增量指标对象（实盘 push 式）。

调用关系：
    - 继承 indicator.base.BaseIndicator。
    - 内部使用 collections.deque 积累数据，满 period 后直接计算，不调用 primitive（避免每次重建 array）。
    - 由策略层（strategy.template.BaseStrategy 子类）的 on_init/on_bar 调用。
"""
from __future__ import annotations
from collections import deque
from typing import Optional

from alphaQuantSystem.indicator.base import BaseIndicator


class SMA_Indicator(BaseIndicator):
    """简单移动平均（Simple Moving Average）增量对象。

    Args:
        period: 计算周期（根数）。
    """

    def __init__(self, period: int) -> None:
        if period < 1:
            raise ValueError(f"period 必须 >= 1，got {period}")
        self._period = period
        self._buf: deque = deque(maxlen=period)

    def push(self, close: float) -> None:
        """喂入一根 bar 的收盘价。"""
        self._buf.append(float(close))

    @property
    def is_ready(self) -> bool:
        return len(self._buf) >= self._period

    @property
    def value(self) -> Optional[float]:
        if not self.is_ready:
            return None
        return sum(self._buf) / self._period

    def reset(self) -> None:
        self._buf.clear()


class EMA_Indicator(BaseIndicator):
    """指数移动平均（Exponential Moving Average）增量对象。

    alpha = 2 / (period + 1)，使用在线递推方式计算，避免每次重算全序列。

    Args:
        period: 计算周期。
    """

    def __init__(self, period: int) -> None:
        if period < 1:
            raise ValueError(f"period 必须 >= 1，got {period}")
        self._period = period
        self._alpha = 2.0 / (period + 1)
        self._count: int = 0
        self._ema: Optional[float] = None

    def push(self, close: float) -> None:
        """喂入一根 bar 的收盘价，在线递推 EMA。"""
        close = float(close)
        self._count += 1
        if self._ema is None:
            self._ema = close
        else:
            self._ema = self._alpha * close + (1 - self._alpha) * self._ema

    @property
    def is_ready(self) -> bool:
        return self._count >= self._period

    @property
    def value(self) -> Optional[float]:
        if not self.is_ready:
            return None
        return self._ema

    def reset(self) -> None:
        self._count = 0
        self._ema = None


class WMA_Indicator(BaseIndicator):
    """线性加权移动平均（Weighted Moving Average）增量对象。

    权重：最近一根权重最大（= period），最早一根权重最小（= 1）。
    Yn = (1*X_oldest + ... + N*X_newest) / (1+2+...+N)

    Args:
        period: 计算周期。
    """

    def __init__(self, period: int) -> None:
        if period < 1:
            raise ValueError(f"period 必须 >= 1，got {period}")
        self._period = period
        self._buf: deque = deque(maxlen=period)
        self._denom: float = period * (period + 1) / 2

    def push(self, close: float) -> None:
        """喂入一根 bar 的收盘价。"""
        self._buf.append(float(close))

    @property
    def is_ready(self) -> bool:
        return len(self._buf) >= self._period

    @property
    def value(self) -> Optional[float]:
        if not self.is_ready:
            return None
        # 最旧的元素权重为 1，最新的为 period
        total = sum(w * v for w, v in enumerate(self._buf, start=1))
        return total / self._denom

    def reset(self) -> None:
        self._buf.clear()
