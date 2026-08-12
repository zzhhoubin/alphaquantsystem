"""primitive 底层序列函数单元测试"""
import numpy as np
import pytest
from alphaQuantSystem.indicator.primitive import (
    MA, EMA, SMA, WMA, STD,
    REF, DIFF, HHV, LLV, SUM,
    MAX, MIN, ABS, IF,
    CROSS, CROSS_UP, CROSS_DOWN,
    AVEDEV, COUNT, DMA,
)


def test_MA_basic():
    s = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    result = MA(s, 3)
    assert np.isnan(result[0])
    assert np.isnan(result[1])
    assert result[2] == pytest.approx(2.0)
    assert result[3] == pytest.approx(3.0)
    assert result[4] == pytest.approx(4.0)


def test_EMA_length():
    s = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    result = EMA(s, 3)
    assert len(result) == len(s)


def test_SMA_chinese_style():
    s = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    result = SMA(s, 3, 1)
    assert len(result) == len(s)
    assert result[0] == pytest.approx(1.0)


def test_WMA_basic():
    s = np.array([1.0, 2.0, 3.0])
    result = WMA(s, 3)
    # weights: 1,2,3 → (1*1 + 2*2 + 3*3)/(1+2+3) = 14/6 ≈ 2.333
    assert result[2] == pytest.approx(14.0 / 6.0, rel=1e-5)


def test_STD_basic():
    s = np.array([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0])
    result = STD(s, 4)
    assert not np.isnan(result[-1])


def test_REF_shift():
    s = np.array([1.0, 2.0, 3.0, 4.0])
    result = REF(s, 1)
    assert np.isnan(result[0])
    assert result[1] == pytest.approx(1.0)
    assert result[3] == pytest.approx(3.0)


def test_DIFF_basic():
    s = np.array([1.0, 3.0, 6.0, 10.0])
    result = DIFF(s, 1)
    assert np.isnan(result[0])
    assert result[1] == pytest.approx(2.0)
    assert result[3] == pytest.approx(4.0)


def test_HHV_LLV():
    s = np.array([3.0, 1.0, 4.0, 1.0, 5.0])
    assert HHV(s, 3)[4] == pytest.approx(5.0)
    assert LLV(s, 3)[4] == pytest.approx(1.0)


def test_SUM_rolling():
    s = np.array([1.0, 2.0, 3.0, 4.0])
    result = SUM(s, 2)
    assert result[1] == pytest.approx(3.0)
    assert result[3] == pytest.approx(7.0)


def test_MAX_MIN():
    a = np.array([1.0, 5.0, 3.0])
    b = np.array([2.0, 4.0, 3.0])
    assert list(MAX(a, b)) == [2.0, 5.0, 3.0]
    assert list(MIN(a, b)) == [1.0, 4.0, 3.0]


def test_ABS_IF():
    s = np.array([-1.0, 2.0, -3.0])
    assert list(ABS(s)) == [1.0, 2.0, 3.0]
    cond = np.array([True, False, True])
    result = IF(cond, np.array([10.0, 10.0, 10.0]), np.array([20.0, 20.0, 20.0]))
    assert list(result) == [10.0, 20.0, 10.0]


def test_CROSS_golden():
    s1 = np.array([1.0, 2.0, 4.0, 5.0])
    s2 = np.array([3.0, 3.0, 3.0, 3.0])
    cross = CROSS(s1, s2)
    assert cross[2] == True
    assert cross[0] == False
    assert cross[3] == False


def test_CROSS_DOWN():
    s1 = np.array([5.0, 4.0, 2.0, 1.0])
    s2 = np.array([3.0, 3.0, 3.0, 3.0])
    cross = CROSS_DOWN(s1, s2)
    assert cross[2] == True
    assert cross[0] == False


def test_COUNT_basic():
    s = np.array([True, False, True, True, False])
    result = COUNT(s, 3)
    # rolling(3): [nan, nan, T+F+T=2, F+T+T=2, T+T+F=2]
    assert result[2] == pytest.approx(2.0)
    assert result[3] == pytest.approx(2.0)
    assert result[4] == pytest.approx(2.0)


def test_DMA_basic():
    s = np.array([100.0] * 20)
    result = DMA(s, 0.5)
    assert result[-1] == pytest.approx(100.0, rel=1e-3)
