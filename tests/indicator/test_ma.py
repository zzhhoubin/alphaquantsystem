"""均线增量对象测试"""
import pytest
from alphaQuantSystem.indicator.ma import SMA_Indicator, EMA_Indicator, WMA_Indicator


# ---------- SMA_Indicator ----------

def test_sma_not_ready_before_period():
    ind = SMA_Indicator(3)
    ind.push(close=1.0)
    ind.push(close=2.0)
    assert not ind.is_ready
    assert ind.value is None


def test_sma_value_correct():
    ind = SMA_Indicator(3)
    for v in [1.0, 2.0, 3.0]:
        ind.push(close=v)
    assert ind.is_ready
    assert ind.value == pytest.approx(2.0)


def test_sma_sliding_window():
    ind = SMA_Indicator(3)
    for v in [1.0, 2.0, 3.0, 4.0]:
        ind.push(close=v)
    # 窗口滑动：(2+3+4)/3
    assert ind.value == pytest.approx(3.0)


def test_sma_reset():
    ind = SMA_Indicator(3)
    for v in [1.0, 2.0, 3.0]:
        ind.push(close=v)
    ind.reset()
    assert not ind.is_ready
    assert ind.value is None


def test_sma_invalid_period():
    with pytest.raises(ValueError):
        SMA_Indicator(0)


# ---------- EMA_Indicator ----------

def test_ema_not_ready_before_period():
    ind = EMA_Indicator(5)
    for _ in range(4):
        ind.push(close=10.0)
    assert not ind.is_ready


def test_ema_ready_at_period():
    ind = EMA_Indicator(5)
    for i in range(1, 6):
        ind.push(close=float(i))
    assert ind.is_ready
    # alpha=2/6=1/3, inputs [1,2,3,4,5]
    # After 5 pushes: ema = 275/81 ≈ 3.3951
    assert ind.value == pytest.approx(275 / 81, rel=1e-4)


def test_ema_value_approaches_price():
    # 价格恒定时，EMA 最终收敛到该价格
    ind = EMA_Indicator(3)
    for _ in range(20):
        ind.push(close=100.0)
    assert ind.value == pytest.approx(100.0, rel=1e-4)


def test_ema_reset():
    ind = EMA_Indicator(3)
    for v in [1.0, 2.0, 3.0]:
        ind.push(close=v)
    ind.reset()
    assert not ind.is_ready
    assert ind.value is None


def test_ema_invalid_period():
    with pytest.raises(ValueError):
        EMA_Indicator(0)


# ---------- WMA_Indicator ----------

def test_wma_not_ready_before_period():
    ind = WMA_Indicator(3)
    ind.push(close=1.0)
    assert not ind.is_ready


def test_wma_value_correct():
    ind = WMA_Indicator(3)
    for v in [1.0, 2.0, 3.0]:
        ind.push(close=v)
    # (1*1 + 2*2 + 3*3) / (1+2+3) = 14/6
    assert ind.value == pytest.approx(14.0 / 6.0, rel=1e-5)


def test_wma_sliding_window():
    ind = WMA_Indicator(3)
    for v in [1.0, 2.0, 3.0, 4.0]:
        ind.push(close=v)
    # 窗口滑动，最近3个值 [2, 3, 4]：(1*2 + 2*3 + 3*4) / (1+2+3) = 20/6
    assert ind.value == pytest.approx(20.0 / 6.0, rel=1e-5)


def test_wma_reset():
    ind = WMA_Indicator(3)
    for v in [1.0, 2.0, 3.0]:
        ind.push(close=v)
    ind.reset()
    assert not ind.is_ready
    assert ind.value is None


def test_wma_invalid_period():
    with pytest.raises(ValueError):
        WMA_Indicator(0)
