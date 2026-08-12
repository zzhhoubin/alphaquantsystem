# tests/indicator/test_init.py
"""验证 indicator 模块统一导出"""


def test_public_api_importable():
    from alphaQuantSystem.indicator import (
        BaseIndicator,
        SMA_Indicator, EMA_Indicator, WMA_Indicator,
        RSI_Indicator, MACD_Indicator, KDJ_Indicator,
        CCI_Indicator, ATR_Indicator,
        functional,
        primitive,
    )
    assert BaseIndicator is not None
    assert SMA_Indicator is not None
    assert EMA_Indicator is not None
    assert WMA_Indicator is not None
    assert RSI_Indicator is not None
    assert MACD_Indicator is not None
    assert KDJ_Indicator is not None
    assert CCI_Indicator is not None
    assert ATR_Indicator is not None
    assert functional is not None
    assert primitive is not None


def test_functional_submodule_has_expected_functions():
    from alphaQuantSystem.indicator import functional as ind
    for fn_name in ('sma', 'ema', 'wma', 'macd', 'rsi', 'kdj', 'cci', 'atr'):
        assert callable(getattr(ind, fn_name, None)), f"functional.{fn_name} not found"


def test_primitive_submodule_has_expected_functions():
    from alphaQuantSystem.indicator import primitive as prim
    for fn_name in ('MA', 'EMA', 'SMA', 'WMA', 'REF', 'DIFF', 'HHV', 'LLV',
                    'SUM', 'MAX', 'MIN', 'ABS', 'IF', 'CROSS'):
        assert callable(getattr(prim, fn_name, None)), f"primitive.{fn_name} not found"
