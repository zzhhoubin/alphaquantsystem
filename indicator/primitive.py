"""
indicator.primitive
===================
底层无状态序列运算函数，基于 numpy / pandas 实现。
公式体系参考通达信公式规范，移植自项目历史代码 sun_tool/technical_analysis_indicator.py。

调用关系：
    - 被 indicator.ma / indicator.momentum 的增量对象内部调用（批量计算时）。
    - 被 indicator.functional 的函数式接口调用（talib 不可用时 fallback）。
    - 可直接在 analyze/ 或 Jupyter 中独立使用。
"""
from __future__ import annotations
import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# 基本向量运算
# ---------------------------------------------------------------------------

def ABS(S: np.ndarray) -> np.ndarray:
    """返回序列绝对值。"""
    return np.abs(S)


def MAX(S1: np.ndarray, S2) -> np.ndarray:
    """逐元素最大值。"""
    return np.maximum(S1, S2)


def MIN(S1: np.ndarray, S2) -> np.ndarray:
    """逐元素最小值。"""
    return np.minimum(S1, S2)


def IF(S: np.ndarray, A, B) -> np.ndarray:
    """条件选择：S 为 True 取 A，否则取 B。"""
    return np.where(S, A, B)


# ---------------------------------------------------------------------------
# 序列移位与差分
# ---------------------------------------------------------------------------

def REF(S, N: int = 1) -> np.ndarray:
    """序列整体下移 N（shift），首 N 个元素为 NaN。"""
    return pd.Series(S).shift(N).values


def DIFF(S, N: int = 1) -> np.ndarray:
    """序列差分（前 N 个为 NaN）。"""
    return pd.Series(S).diff(N).values


# ---------------------------------------------------------------------------
# 统计函数
# ---------------------------------------------------------------------------

def STD(S, N: int) -> np.ndarray:
    """N 日滚动标准差（ddof=0，总体标准差）。"""
    return pd.Series(S).rolling(N).std(ddof=0).values


def SUM(S, N: int) -> np.ndarray:
    """N 日滚动累和；N=0 时返回历史累和。"""
    if N > 0:
        return pd.Series(S).rolling(N).sum().values
    return pd.Series(S).cumsum().values


def COUNT(S, N: int) -> np.ndarray:
    """N 日内满足布尔条件 S 的天数。"""
    return SUM(np.asarray(S, dtype=float), N)


def HHV(S, N: int) -> np.ndarray:
    """N 日滚动最高值。"""
    return pd.Series(S).rolling(N).max().values


def LLV(S, N: int) -> np.ndarray:
    """N 日滚动最低值。"""
    return pd.Series(S).rolling(N).min().values


def AVEDEV(S, N: int) -> np.ndarray:
    """平均绝对偏差：序列与其均值的绝对差的均值。"""
    return pd.Series(S).rolling(N).apply(
        lambda x: np.abs(x - x.mean()).mean(), raw=True
    ).values


# ---------------------------------------------------------------------------
# 均线函数
# ---------------------------------------------------------------------------

def MA(S, N: int) -> np.ndarray:
    """N 日简单移动平均（前 N-1 个为 NaN）。"""
    return pd.Series(S).rolling(N).mean().values


def EMA(S, N: int) -> np.ndarray:
    """指数移动平均，alpha=2/(N+1)，adjust=False。

    精度要求：序列长度应 > 4*N，至少需要 120 周期才足够精确。
    """
    return pd.Series(S).ewm(span=N, adjust=False).mean().values


def SMA(S, N: int, M: int = 1) -> np.ndarray:
    """中国式 SMA（通达信），alpha=M/N，adjust=False。

    与标准 EMA 的区别：平滑系数 alpha=M/N，而非 2/(N+1)。
    精度要求：序列长度 > 120 周期。
    """
    return pd.Series(S).ewm(alpha=M / N, adjust=False).mean().values


def WMA(S, N: int) -> np.ndarray:
    """线性加权移动平均：Yn = (1*X1+2*X2+...+N*XN) / (1+2+...+N)。"""
    return pd.Series(S).rolling(N).apply(
        lambda x: x[::-1].cumsum().sum() * 2 / N / (N + 1), raw=True
    ).values


def DMA(S, A) -> np.ndarray:
    """动态移动平均，A 为平滑因子（0 < A < 1）。递归定义：DMA[t] = A*S[t] + (1-A)*DMA[t-1]。"""
    return pd.Series(S).ewm(alpha=A, adjust=False).mean().values


# ---------------------------------------------------------------------------
# 交叉判断
# ---------------------------------------------------------------------------

def CROSS(S1, S2) -> np.ndarray:
    """S1 上穿 S2（金叉）：上一根 S1<=S2，本根 S1>S2。"""
    s1, s2 = np.asarray(S1), np.asarray(S2)
    return np.concatenate(([False], ~(s1 > s2)[:-1] & (s1 > s2)[1:]))


def CROSS_UP(S1, S2) -> np.ndarray:
    """同 CROSS，S1 上穿 S2。"""
    return CROSS(S1, S2)


def CROSS_DOWN(S1, S2) -> np.ndarray:
    """S1 下穿 S2（死叉）：上一根 S1>=S2，本根 S1<S2。"""
    s1, s2 = np.asarray(S1), np.asarray(S2)
    return np.concatenate(([False], ~(s1 < s2)[:-1] & (s1 < s2)[1:]))


def BARSLAST(S) -> np.ndarray:
    """上一次条件成立到当前的周期数。"""
    m = np.concatenate(([0], np.where(S, 1, 0)))
    for i in range(1, len(m)):
        m[i] = 0 if m[i] else m[i - 1] + 1
    return m[1:]
