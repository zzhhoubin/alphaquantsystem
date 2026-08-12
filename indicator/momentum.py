"""
indicator.momentum
==================
振荡势能类增量指标对象（实盘 push 式）。

调用关系：
    - 继承 indicator.base.BaseIndicator。
    - 内部使用 deque 积累 close/high/low 序列，就绪后调用 indicator.primitive 完成批量计算，
      取最后一个值作为当前值返回。
    - 由策略层（strategy.template.BaseStrategy 子类）的 on_init/on_bar 调用。
"""
from __future__ import annotations
from collections import deque
from typing import Optional, Tuple

import numpy as np

from alphaQuantSystem.indicator.base import BaseIndicator
from alphaQuantSystem.indicator import primitive as _p


class RSI_Indicator(BaseIndicator):
    """相对强弱指标（RSI）增量对象，同时计算三个周期。

    Args:
        n1: 短周期，默认 6。
        n2: 中周期，默认 12。
        n3: 长周期，默认 24。
    """

    def __init__(self, n1: int = 6, n2: int = 12, n3: int = 24) -> None:
        self._n1, self._n2, self._n3 = n1, n2, n3
        self._maxlen = max(n3 * 4, 120)
        self._buf: deque = deque(maxlen=self._maxlen)
        self._count: int = 0

    def push(self, close: float) -> None:
        self._buf.append(float(close))
        self._count += 1

    @property
    def is_ready(self) -> bool:
        return self._count >= self._n3

    @property
    def value(self) -> Optional[Tuple[float, float, float]]:
        if not self.is_ready:
            return None
        arr = np.array(self._buf)
        lc = _p.REF(arr, 1)
        d1 = _p.SMA(_p.ABS(arr - lc), self._n1, 1)
        d2 = _p.SMA(_p.ABS(arr - lc), self._n2, 1)
        d3 = _p.SMA(_p.ABS(arr - lc), self._n3, 1)
        # 价格完全不动时分母为 0，用 1e-10 避免除零
        d1 = np.where(d1 == 0, 1e-10, d1)
        d2 = np.where(d2 == 0, 1e-10, d2)
        d3 = np.where(d3 == 0, 1e-10, d3)
        rsi1 = _p.SMA(_p.MAX(arr - lc, 0), self._n1, 1) / d1 * 100
        rsi2 = _p.SMA(_p.MAX(arr - lc, 0), self._n2, 1) / d2 * 100
        rsi3 = _p.SMA(_p.MAX(arr - lc, 0), self._n3, 1) / d3 * 100
        return float(rsi1[-1]), float(rsi2[-1]), float(rsi3[-1])

    def reset(self) -> None:
        self._buf.clear()
        self._count = 0


class MACD_Indicator(BaseIndicator):
    """平滑异同平均线（MACD）增量对象。

    输出 DIF（快慢线差）、DEA（信号线）、MACD 柱（histogram = (DIF-DEA)*2）。

    Args:
        short: 短期 EMA 周期，默认 12。
        long:  长期 EMA 周期，默认 26。
        mid:   DEA 平滑周期，默认 9。
    """

    def __init__(self, short: int = 12, long: int = 26, mid: int = 9) -> None:
        self._short, self._long, self._mid = short, long, mid
        self._maxlen = (long + mid) * 4
        self._buf: deque = deque(maxlen=self._maxlen)
        self._count: int = 0

    def push(self, close: float) -> None:
        self._buf.append(float(close))
        self._count += 1

    @property
    def is_ready(self) -> bool:
        return self._count >= self._long + self._mid

    @property
    def value(self) -> Optional[Tuple[float, float, float]]:
        if not self.is_ready:
            return None
        arr = np.array(self._buf)
        dif_arr = _p.EMA(arr, self._short) - _p.EMA(arr, self._long)
        dea_arr = _p.EMA(dif_arr, self._mid)
        macd_arr = (dif_arr - dea_arr) * 2
        return float(dif_arr[-1]), float(dea_arr[-1]), float(macd_arr[-1])

    def reset(self) -> None:
        self._buf.clear()
        self._count = 0


class KDJ_Indicator(BaseIndicator):
    """随机指标 KDJ 增量对象。

    Args:
        n:  RSV 计算周期，默认 9。
        m1: K 值平滑周期，默认 3。
        m2: D 值平滑周期，默认 3。
    """

    def __init__(self, n: int = 9, m1: int = 3, m2: int = 3) -> None:
        self._n, self._m1, self._m2 = n, m1, m2
        self._maxlen = max(n * 4, 60)
        self._close_buf: deque = deque(maxlen=self._maxlen)
        self._high_buf: deque = deque(maxlen=self._maxlen)
        self._low_buf: deque = deque(maxlen=self._maxlen)
        self._count: int = 0

    def push(self, close: float, high: float, low: float) -> None:
        self._close_buf.append(float(close))
        self._high_buf.append(float(high))
        self._low_buf.append(float(low))
        self._count += 1

    @property
    def is_ready(self) -> bool:
        return self._count >= self._n

    @property
    def value(self) -> Optional[Tuple[float, float, float]]:
        if not self.is_ready:
            return None
        close = np.array(self._close_buf)
        high = np.array(self._high_buf)
        low = np.array(self._low_buf)
        llv = _p.LLV(low, self._n)
        hhv = _p.HHV(high, self._n)
        denom = hhv - llv
        # 分母为 0 时（极端行情）用 1 避免除零
        denom = np.where(denom == 0, 1.0, denom)
        rsv = (close - llv) / denom * 100
        k = _p.SMA(rsv, self._m1, 1)
        d = _p.SMA(k, self._m2, 1)
        j = 3 * k - 2 * d
        return float(k[-1]), float(d[-1]), float(j[-1])

    def reset(self) -> None:
        self._close_buf.clear()
        self._high_buf.clear()
        self._low_buf.clear()
        self._count = 0


class CCI_Indicator(BaseIndicator):
    """商品路径指标（CCI）增量对象。

    TYP = (HIGH + LOW + CLOSE) / 3
    CCI = (TYP - MA(TYP, N)) * 1000 / (15 * AVEDEV(TYP, N))

    Args:
        n: 计算周期，默认 14。
    """

    def __init__(self, n: int = 14) -> None:
        self._n = n
        self._close_buf: deque = deque(maxlen=n * 4)
        self._high_buf: deque = deque(maxlen=n * 4)
        self._low_buf: deque = deque(maxlen=n * 4)
        self._count: int = 0

    def push(self, close: float, high: float, low: float) -> None:
        self._close_buf.append(float(close))
        self._high_buf.append(float(high))
        self._low_buf.append(float(low))
        self._count += 1

    @property
    def is_ready(self) -> bool:
        return self._count >= self._n

    @property
    def value(self) -> Optional[float]:
        if not self.is_ready:
            return None
        close = np.array(self._close_buf)
        high = np.array(self._high_buf)
        low = np.array(self._low_buf)
        typ = (high + low + close) / 3
        ma = _p.MA(typ, self._n)
        avedev = _p.AVEDEV(typ, self._n)
        avedev_safe = np.where(avedev == 0, 1e-10, avedev)
        cci = (typ - ma) * 1000 / (15 * avedev_safe)
        return float(cci[-1])

    def reset(self) -> None:
        self._close_buf.clear()
        self._high_buf.clear()
        self._low_buf.clear()
        self._count = 0


class ATR_Indicator(BaseIndicator):
    """真实波幅（ATR）增量对象。

    MTR = MAX(MAX(HIGH-LOW, |REF(CLOSE,1)-HIGH|), |REF(CLOSE,1)-LOW|)
    ATR = MA(MTR, N)

    Args:
        n: 计算周期，默认 14。
    """

    def __init__(self, n: int = 14) -> None:
        self._n = n
        self._close_buf: deque = deque(maxlen=n * 4)
        self._high_buf: deque = deque(maxlen=n * 4)
        self._low_buf: deque = deque(maxlen=n * 4)
        self._count: int = 0

    def push(self, close: float, high: float, low: float) -> None:
        self._close_buf.append(float(close))
        self._high_buf.append(float(high))
        self._low_buf.append(float(low))
        self._count += 1

    @property
    def is_ready(self) -> bool:
        return self._count >= self._n

    @property
    def value(self) -> Optional[float]:
        if not self.is_ready:
            return None
        close = np.array(self._close_buf)
        high = np.array(self._high_buf)
        low = np.array(self._low_buf)
        prev_close = _p.REF(close, 1)
        mtr = _p.MAX(_p.MAX(high - low, _p.ABS(prev_close - high)),
                     _p.ABS(prev_close - low))
        # 首根无前收盘时 mtr[0] 为 NaN，回退到 high-low（标准做法）
        mtr = np.where(np.isnan(mtr), high - low, mtr)
        atr = _p.MA(mtr, self._n)
        return float(atr[-1])

    def reset(self) -> None:
        self._close_buf.clear()
        self._high_buf.clear()
        self._low_buf.clear()
        self._count = 0
