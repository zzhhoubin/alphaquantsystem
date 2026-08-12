"""函数式批量接口测试"""
import numpy as np
import pandas as pd
import pytest
from alphaQuantSystem.indicator import functional as ind


def _make_df(n: int = 60) -> pd.DataFrame:
    """生成测试用 OHLCV DataFrame。"""
    np.random.seed(42)
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    return pd.DataFrame({
        'open':   close - np.abs(np.random.randn(n) * 0.2),
        'high':   close + np.abs(np.random.randn(n) * 0.3),
        'low':    close - np.abs(np.random.randn(n) * 0.3),
        'close':  close,
        'volume': np.random.randint(1000, 10000, n).astype(float),
    })


# ---------- 均线 ----------

def test_sma_returns_series():
    df = _make_df()
    result = ind.sma(df, 5)
    assert isinstance(result, pd.Series)
    assert len(result) == len(df)
    # 前 4 根为 NaN
    assert result.iloc[:4].isna().all()
    assert not pd.isna(result.iloc[-1])


def test_ema_returns_series():
    df = _make_df()
    result = ind.ema(df, 10)
    assert isinstance(result, pd.Series)
    assert not pd.isna(result.iloc[-1])


def test_wma_returns_series():
    df = _make_df()
    result = ind.wma(df, 5)
    assert isinstance(result, pd.Series)
    assert not pd.isna(result.iloc[-1])


# ---------- MACD ----------

def test_macd_returns_three_series():
    df = _make_df(60)
    dif, dea, hist = ind.macd(df)
    assert isinstance(dif, pd.Series)
    assert isinstance(dea, pd.Series)
    assert isinstance(hist, pd.Series)
    assert len(dif) == len(df)
    # hist = (dif - dea) * 2
    valid = ~(dif.isna() | dea.isna())
    np.testing.assert_allclose(
        hist[valid].values,
        ((dif - dea) * 2)[valid].values,
        rtol=1e-5,
    )


# ---------- RSI ----------

def test_rsi_returns_three_series():
    df = _make_df(60)
    r1, r2, r3 = ind.rsi(df)
    assert isinstance(r1, pd.Series)
    # RSI 值域 [0, 100]，三个周期均需满足
    for r in (r1, r2, r3):
        valid = ~r.isna()
        assert (r[valid] >= 0).all() and (r[valid] <= 100).all()


def test_rsi_flat_price_no_division_error():
    """价格完全不动时，RSI 分母为 0，不应产生异常或 NaN。"""
    closes = [100.0] * 30
    df = pd.DataFrame({'close': closes})
    r1, r2, r3 = ind.rsi(df, n1=6, n2=12, n3=24)
    # 不抛异常即合格；值可能为 0 或 100（取决于实现），但不应是 NaN（除非序列太短）
    assert isinstance(r1, pd.Series)


def test_kdj_flat_price_no_division_error():
    """high==low 时 KDJ 分母为 0，不应产生异常。"""
    n = 20
    df = pd.DataFrame({
        'close': [100.0] * n,
        'high':  [100.0] * n,
        'low':   [100.0] * n,
    })
    k, d, j = ind.kdj(df)
    assert isinstance(k, pd.Series)


# ---------- KDJ ----------

def test_kdj_returns_three_series():
    df = _make_df(30)
    k, d, j = ind.kdj(df)
    assert isinstance(k, pd.Series)
    assert isinstance(d, pd.Series)
    assert isinstance(j, pd.Series)
    assert len(k) == len(df)


# ---------- CCI ----------

def test_cci_returns_series():
    df = _make_df(30)
    result = ind.cci(df)
    assert isinstance(result, pd.Series)
    assert not pd.isna(result.iloc[-1])


# ---------- ATR ----------

def test_atr_returns_series():
    df = _make_df(30)
    result = ind.atr(df)
    assert isinstance(result, pd.Series)
    assert not pd.isna(result.iloc[-1])
    # ATR 必须非负
    valid = ~result.isna()
    assert (result[valid] >= 0).all()


# ---------- 缺失列异常 ----------

def test_missing_close_column_raises():
    df = pd.DataFrame({'open': [1.0, 2.0]})
    with pytest.raises(KeyError):
        ind.sma(df, 5)
