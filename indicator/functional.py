"""
indicator.functional
====================
函数式批量指标接口（DataFrame-in → pd.Series/tuple）。

优先使用 TA-Lib（若已安装），否则 fallback 到 indicator.primitive 实现。
调用方无需关心底层实现，接口完全一致。

调用关系：
    - 由回测层（backtest/）、绩效分析层（analyze/）以及策略 on_init 批量预处理时调用。
    - 内部调用 indicator.primitive 的序列函数（talib 不可用时）。
"""
from __future__ import annotations
from typing import Tuple

import numpy as np
import pandas as pd

from alphaQuantSystem.indicator import primitive as _p

# talib 可选依赖检测
try:
    import talib as _talib
    _HAS_TALIB = True
except ImportError:
    _HAS_TALIB = False


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _to_array(series) -> np.ndarray:
    """将 pd.Series 或 list/ndarray 转为 float64 ndarray。"""
    return np.asarray(series, dtype=np.float64)


def _wrap(arr: np.ndarray, index) -> pd.Series:
    """将 ndarray 封装为与原 DataFrame 同索引的 pd.Series。"""
    return pd.Series(arr, index=index)


# ---------------------------------------------------------------------------
# 均线
# ---------------------------------------------------------------------------

def sma(df: pd.DataFrame, period: int) -> pd.Series:
    """简单移动平均。

    Args:
        df:     含 ``close`` 列的 DataFrame。
        period: 计算周期。

    Returns:
        与 df 同长度的 pd.Series，前 period-1 根为 NaN。
    """
    close = _to_array(df['close'])
    if _HAS_TALIB:
        result = _talib.SMA(close, timeperiod=period)
    else:
        result = _p.MA(close, period)
    return _wrap(result, df.index)


def ema(df: pd.DataFrame, period: int) -> pd.Series:
    """指数移动平均。

    Args:
        df:     含 ``close`` 列的 DataFrame。
        period: 计算周期。

    Returns:
        与 df 同长度的 pd.Series。
    """
    close = _to_array(df['close'])
    if _HAS_TALIB:
        result = _talib.EMA(close, timeperiod=period)
    else:
        result = _p.EMA(close, period)
    return _wrap(result, df.index)


def wma(df: pd.DataFrame, period: int) -> pd.Series:
    """线性加权移动平均。

    Args:
        df:     含 ``close`` 列的 DataFrame。
        period: 计算周期。

    Returns:
        与 df 同长度的 pd.Series。
    """
    close = _to_array(df['close'])
    if _HAS_TALIB:
        result = _talib.WMA(close, timeperiod=period)
    else:
        result = _p.WMA(close, period)
    return _wrap(result, df.index)


# ---------------------------------------------------------------------------
# 振荡势能
# ---------------------------------------------------------------------------

def macd(
    df: pd.DataFrame,
    short: int = 12,
    long: int = 26,
    mid: int = 9,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """平滑异同平均线 MACD。

    Args:
        df:    含 ``close`` 列的 DataFrame。
        short: 快线 EMA 周期，默认 12。
        long:  慢线 EMA 周期，默认 26。
        mid:   信号线 EMA 周期，默认 9。

    Returns:
        (DIF, DEA, MACD柱)，均为与 df 同长度的 pd.Series。
        MACD柱 = (DIF - DEA) * 2。
    """
    close = _to_array(df['close'])
    if _HAS_TALIB:
        # talib hist = DIF-DEA；通达信约定 MACD柱 = (DIF-DEA)*2，需乘 2 对齐
        dif, dea, talib_hist = _talib.MACD(close, fastperiod=short, slowperiod=long, signalperiod=mid)
        hist = talib_hist * 2
    else:
        dif_arr = _p.EMA(close, short) - _p.EMA(close, long)
        dea_arr = _p.EMA(dif_arr, mid)
        hist_arr = (dif_arr - dea_arr) * 2
        dif, dea, hist = dif_arr, dea_arr, hist_arr
    return _wrap(dif, df.index), _wrap(dea, df.index), _wrap(hist, df.index)


def rsi(
    df: pd.DataFrame,
    n1: int = 6,
    n2: int = 12,
    n3: int = 24,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """相对强弱指标 RSI（三周期）。

    Args:
        df: 含 ``close`` 列的 DataFrame。
        n1: 短周期，默认 6。
        n2: 中周期，默认 12。
        n3: 长周期，默认 24。

    Returns:
        (RSI1, RSI2, RSI3)，均为与 df 同长度的 pd.Series，值域 [0, 100]。
    """
    close = _to_array(df['close'])
    if _HAS_TALIB:
        r1 = _talib.RSI(close, timeperiod=n1)
        r2 = _talib.RSI(close, timeperiod=n2)
        r3 = _talib.RSI(close, timeperiod=n3)
    else:
        lc = _p.REF(close, 1)
        d1 = _p.SMA(_p.ABS(close - lc), n1, 1)
        d2 = _p.SMA(_p.ABS(close - lc), n2, 1)
        d3 = _p.SMA(_p.ABS(close - lc), n3, 1)
        d1 = np.where(d1 == 0, 1e-10, d1)
        d2 = np.where(d2 == 0, 1e-10, d2)
        d3 = np.where(d3 == 0, 1e-10, d3)
        r1 = _p.SMA(_p.MAX(close - lc, 0), n1, 1) / d1 * 100
        r2 = _p.SMA(_p.MAX(close - lc, 0), n2, 1) / d2 * 100
        r3 = _p.SMA(_p.MAX(close - lc, 0), n3, 1) / d3 * 100
    return _wrap(r1, df.index), _wrap(r2, df.index), _wrap(r3, df.index)


def kdj(
    df: pd.DataFrame,
    n: int = 9,
    m1: int = 3,
    m2: int = 3,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """随机指标 KDJ。

    Args:
        df: 含 ``close`` / ``high`` / ``low`` 列的 DataFrame。
        n:  RSV 周期，默认 9。
        m1: K 值平滑，默认 3。
        m2: D 值平滑，默认 3。

    Returns:
        (K, D, J)，均为与 df 同长度的 pd.Series。
    """
    close = _to_array(df['close'])
    high  = _to_array(df['high'])
    low   = _to_array(df['low'])
    if _HAS_TALIB:
        # talib 的 STOCH 返回 slowk, slowd；J 需手动计算
        k_arr, d_arr = _talib.STOCH(
            high, low, close,
            fastk_period=n,
            slowk_period=m1, slowk_matype=1,
            slowd_period=m2, slowd_matype=1,
        )
        j_arr = 3 * k_arr - 2 * d_arr
    else:
        llv = _p.LLV(low, n)
        hhv = _p.HHV(high, n)
        denom = hhv - llv
        denom = np.where(denom == 0, 1e-10, denom)
        rsv = (close - llv) / denom * 100
        k_arr = _p.SMA(rsv, m1, 1)
        d_arr = _p.SMA(k_arr, m2, 1)
        j_arr = 3 * k_arr - 2 * d_arr
    return _wrap(k_arr, df.index), _wrap(d_arr, df.index), _wrap(j_arr, df.index)


def cci(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """商品路径指标 CCI。

    Args:
        df: 含 ``close`` / ``high`` / ``low`` 列的 DataFrame。
        n:  计算周期，默认 14。

    Returns:
        与 df 同长度的 pd.Series。
    """
    close = _to_array(df['close'])
    high  = _to_array(df['high'])
    low   = _to_array(df['low'])
    if _HAS_TALIB:
        result = _talib.CCI(high, low, close, timeperiod=n)
    else:
        typ = (high + low + close) / 3
        ma = _p.MA(typ, n)
        avedev = _p.AVEDEV(typ, n)
        avedev_safe = np.where(avedev == 0, 1e-10, avedev)
        result = (typ - ma) * 1000 / (15 * avedev_safe)
    return _wrap(result, df.index)


def six_pulse_excalibur(df: pd.DataFrame) -> pd.Series:
    """六脉神剑共振得分（0～6，6 为满仓共振）。

    Args:
        df: 含 ``close`` / ``low`` / ``high`` 列的 DataFrame。

    Returns:
        与 ``df`` 同索引的 ``pd.Series``。

    底层实现见 ``indicator.six_pulse.six_pulse_excalibur``（与 ``primitive`` 通达信公式一致）。
    """
    from alphaQuantSystem.indicator.six_pulse import six_pulse_excalibur as _six_core

    close = _to_array(df['close'])
    low = _to_array(df['low'])
    high = _to_array(df['high'])
    result = _six_core(close, low, high)
    return _wrap(result, df.index)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """真实波幅 ATR。

    Args:
        df: 含 ``close`` / ``high`` / ``low`` 列的 DataFrame。
        n:  计算周期，默认 14。

    Returns:
        与 df 同长度的 pd.Series，值非负。
    """
    close = _to_array(df['close'])
    high  = _to_array(df['high'])
    low   = _to_array(df['low'])
    if _HAS_TALIB:
        result = _talib.ATR(high, low, close, timeperiod=n)
    else:
        prev_close = _p.REF(close, 1)
        mtr = _p.MAX(_p.MAX(high - low, _p.ABS(prev_close - high)),
                     _p.ABS(prev_close - low))
        mtr = np.where(np.isnan(mtr), high - low, mtr)
        result = _p.MA(mtr, n)
    return _wrap(result, df.index)
