"""振荡势能增量对象测试"""
import pytest
from alphaQuantSystem.indicator.momentum import (
    RSI_Indicator, MACD_Indicator, KDJ_Indicator,
    CCI_Indicator, ATR_Indicator,
)

# 用一段真实感价格序列辅助测试
_CLOSES = [
    10.0, 10.5, 10.2, 10.8, 11.0, 10.7, 10.9, 11.2,
    11.5, 11.3, 11.6, 11.8, 12.0, 11.9, 12.1, 12.3,
    12.0, 12.4, 12.2, 12.5, 12.7, 12.6, 12.8, 13.0,
    12.9, 13.2, 13.1, 13.4, 13.6, 13.5,
]
_HIGHS = [c + 0.3 for c in _CLOSES]
_LOWS  = [c - 0.3 for c in _CLOSES]


# ---------- RSI_Indicator ----------

def test_rsi_not_ready_before_enough_bars():
    ind = RSI_Indicator(n1=6, n2=12, n3=24)
    for c in _CLOSES[:23]:
        ind.push(close=c)
    assert not ind.is_ready


def test_rsi_ready_after_n3_bars():
    ind = RSI_Indicator(n1=6, n2=12, n3=24)
    for c in _CLOSES[:24]:
        ind.push(close=c)
    assert ind.is_ready
    r1, r2, r3 = ind.value
    # RSI 值域 [0, 100]
    assert 0 <= r1 <= 100
    assert 0 <= r2 <= 100
    assert 0 <= r3 <= 100


def test_rsi_reset():
    ind = RSI_Indicator(n1=6, n2=12, n3=24)
    for c in _CLOSES:
        ind.push(close=c)
    ind.reset()
    assert not ind.is_ready
    assert ind.value is None


# ---------- MACD_Indicator ----------

def test_macd_not_ready_before_enough_bars():
    ind = MACD_Indicator(short=12, long=26, mid=9)
    for c in _CLOSES[:26]:
        ind.push(close=c)
    assert not ind.is_ready


def test_macd_ready_after_long_plus_mid_bars():
    ind = MACD_Indicator(short=12, long=26, mid=9)
    # 至少需要 long + mid = 35 根，此处用 60 根
    closes = _CLOSES * 2  # 60 根
    for c in closes:
        ind.push(close=c)
    assert ind.is_ready
    dif, dea, hist = ind.value
    assert isinstance(dif, float)
    assert isinstance(dea, float)
    assert hist == pytest.approx((dif - dea) * 2, rel=1e-6)


def test_macd_reset():
    ind = MACD_Indicator()
    for c in _CLOSES * 2:
        ind.push(close=c)
    ind.reset()
    assert not ind.is_ready
    assert ind.value is None


# ---------- KDJ_Indicator ----------

def test_kdj_not_ready_before_n_bars():
    ind = KDJ_Indicator(n=9)
    for i in range(8):
        ind.push(close=_CLOSES[i], high=_HIGHS[i], low=_LOWS[i])
    assert not ind.is_ready


def test_kdj_ready_after_n_bars():
    ind = KDJ_Indicator(n=9)
    for i in range(9):
        ind.push(close=_CLOSES[i], high=_HIGHS[i], low=_LOWS[i])
    assert ind.is_ready
    k, d, j = ind.value
    assert isinstance(k, float)
    assert isinstance(d, float)
    assert isinstance(j, float)


def test_kdj_reset():
    ind = KDJ_Indicator(n=9)
    for i in range(9):
        ind.push(close=_CLOSES[i], high=_HIGHS[i], low=_LOWS[i])
    ind.reset()
    assert not ind.is_ready
    assert ind.value is None


# ---------- CCI_Indicator ----------

def test_cci_not_ready_before_n_bars():
    ind = CCI_Indicator(n=14)
    for i in range(13):
        ind.push(close=_CLOSES[i], high=_HIGHS[i], low=_LOWS[i])
    assert not ind.is_ready


def test_cci_ready_after_n_bars():
    ind = CCI_Indicator(n=14)
    for i in range(14):
        ind.push(close=_CLOSES[i], high=_HIGHS[i], low=_LOWS[i])
    assert ind.is_ready
    val = ind.value
    assert isinstance(val, float)


def test_cci_reset():
    ind = CCI_Indicator(n=14)
    for i in range(14):
        ind.push(close=_CLOSES[i], high=_HIGHS[i], low=_LOWS[i])
    ind.reset()
    assert not ind.is_ready
    assert ind.value is None


# ---------- ATR_Indicator ----------

def test_atr_not_ready_before_n_bars():
    ind = ATR_Indicator(n=14)
    for i in range(13):
        ind.push(close=_CLOSES[i], high=_HIGHS[i], low=_LOWS[i])
    assert not ind.is_ready


def test_atr_ready_after_n_bars():
    ind = ATR_Indicator(n=14)
    for i in range(14):
        ind.push(close=_CLOSES[i], high=_HIGHS[i], low=_LOWS[i])
    assert ind.is_ready
    val = ind.value
    assert isinstance(val, float)
    assert val >= 0


def test_atr_reset():
    ind = ATR_Indicator(n=14)
    for i in range(14):
        ind.push(close=_CLOSES[i], high=_HIGHS[i], low=_LOWS[i])
    ind.reset()
    assert not ind.is_ready
    assert ind.value is None
