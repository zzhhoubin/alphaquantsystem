"""
六脉神剑（通达信 / MyTT 风格）。

共振得分 ``ABC1+…+ABC6``，取值 0～6；等于 6 表示六条件均为多。

实现依赖 ``indicator.primitive``，与历史 ``sun_tool/technical_analysis_indicator.py`` 同源公式。
"""
from __future__ import annotations

import numpy as np

from alphaQuantSystem.indicator import primitive as _p


def six_pulse_excalibur(CLOSE, LOW, HIGH):
    """
    计算六脉神剑序列。

    Args:
        CLOSE: 收盘价序列（可 ``np.ndarray`` / list）。
        LOW:   最低价序列。
        HIGH:  最高价序列。

    Returns:
        ``numpy.ndarray``：与输入等长，元素为 0.0～6.0 的共振得分（整数关口以浮点表示）。
    """
    CLOSE = np.asarray(CLOSE, dtype=float)
    LOW = np.asarray(LOW, dtype=float)
    HIGH = np.asarray(HIGH, dtype=float)

    DIFF = _p.EMA(CLOSE, 8) - _p.EMA(CLOSE, 13)
    DEA = _p.EMA(DIFF, 5)
    ABC1 = _p.IF(DIFF > DEA, 1, 0)

    rng = _p.HHV(HIGH, 8) - _p.LLV(LOW, 8)
    rsv_like = _p.IF(rng != 0, (CLOSE - _p.LLV(LOW, 8)) / rng * 100, 50.0)
    K = _p.SMA(rsv_like, 3, 1)
    D = _p.SMA(K, 3, 1)
    ABC2 = _p.IF(K > D, 1, 0)

    prev_c = _p.REF(CLOSE, 1)
    RSI1 = (_p.SMA(_p.MAX(CLOSE - prev_c, 0), 5, 1)) / np.maximum(_p.SMA(_p.ABS(CLOSE - prev_c), 5, 1), 1e-12) * 100
    RSI2 = (_p.SMA(_p.MAX(CLOSE - prev_c, 0), 13, 1)) / np.maximum(_p.SMA(_p.ABS(CLOSE - prev_c), 13, 1), 1e-12) * 100
    ABC3 = _p.IF(RSI1 > RSI2, 1, 0)

    rng13 = _p.HHV(HIGH, 13) - _p.LLV(LOW, 13)
    lwr_raw = _p.IF(rng13 != 0, -(_p.HHV(HIGH, 13) - CLOSE) / rng13 * 100, -50.0)
    LWR1 = _p.SMA(lwr_raw, 3, 1)
    LWR2 = _p.SMA(LWR1, 3, 1)
    ABC4 = _p.IF(LWR1 > LWR2, 1, 0)

    BBI = (_p.MA(CLOSE, 3) + _p.MA(CLOSE, 5) + _p.MA(CLOSE, 8) + _p.MA(CLOSE, 13)) / 4
    ABC5 = _p.IF(CLOSE > BBI, 1, 0)

    MTM = CLOSE - _p.REF(CLOSE, 1)
    MMS = 100 * _p.EMA(_p.EMA(MTM, 5), 3) / np.maximum(_p.EMA(_p.EMA(_p.ABS(MTM), 5), 3), 1e-12)
    MMM = 100 * _p.EMA(_p.EMA(MTM, 13), 8) / np.maximum(_p.EMA(_p.EMA(_p.ABS(MTM), 13), 8), 1e-12)
    ABC6 = _p.IF(MMS > MMM, 1, 0)

    return ABC1 + ABC2 + ABC3 + ABC4 + ABC5 + ABC6
