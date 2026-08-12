"""
EXPMA（双指数均线）与 KDJ 批量序列。

``EXPMA`` 与 ``alphaQuantSystem_1.0.1`` 下 ``sun_tool/technical_analysis_indicator.EXPMA`` 一致：
输出为 ``EMA(CLOSE, M1)``、``EMA(CLOSE, M2)``。

KDJ 与 ``indicator.functional.kdj`` / ``primitive`` 通达信算法一致。
"""
from __future__ import annotations

import numpy as np

from alphaQuantSystem.indicator import primitive as _p


def expma(CLOSE, M1: int = 5, M2: int = 10):
    """
    指数平均线（两条 EMA）。

    Returns:
        (EXP1, EXP2)，均为与 ``CLOSE`` 等长的 ``ndarray``。
    """
    c = np.asarray(CLOSE, dtype=float)
    return _p.EMA(c, M1), _p.EMA(c, M2)


def kdj(CLOSE, LOW, HIGH, n: int = 9, m1: int = 3, m2: int = 3):
    """
    KDJ 三序列（K、D、J），与 ``functional.kdj`` 无 talib 分支一致。

    Returns:
        (K, D, J)，均为与输入等长的 ``ndarray``；``J = 3*K - 2*D``。
    """
    close = np.asarray(CLOSE, dtype=float)
    low = np.asarray(LOW, dtype=float)
    high = np.asarray(HIGH, dtype=float)
    llv = _p.LLV(low, n)
    hhv = _p.HHV(high, n)
    denom = hhv - llv
    denom = np.where(denom == 0, 1e-10, denom)
    rsv = (close - llv) / denom * 100
    k_arr = _p.SMA(rsv, m1, 1)
    d_arr = _p.SMA(k_arr, m2, 1)
    j_arr = 3 * k_arr - 2 * d_arr
    return k_arr, d_arr, j_arr
