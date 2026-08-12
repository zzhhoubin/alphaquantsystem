"""
indicator
=========
技术指标模块，提供双模式指标计算：

- **增量式（push）**：适合实盘 on_bar 逐 bar 驱动
  ``ind = MACD_Indicator(); ind.push(close=bar.close); ind.value``

- **函数式（functional）**：适合回测/分析，传入 DataFrame 批量计算
  ``from alphaQuantSystem.indicator import functional as ind; ind.macd(df)``、``ind.six_pulse_excalibur(df)``

- **六脉神剑（序列）**：``from alphaQuantSystem.indicator import six_pulse_excalibur``，传入
  ``close``/``low``/``high`` 数组，与 ``indicator.six_pulse`` 一致。
- **EXPMA / KDJ（序列）**：``from alphaQuantSystem.indicator import expma, kdj_ohlc``，见 ``indicator.expma_kdj``。

调用关系：
    - 策略层（strategy.template.BaseStrategy 子类）通过增量式对象使用。
    - 回测层（backtest/）、绩效层（analyze/）通过 functional 模块使用。
"""
from alphaQuantSystem.indicator.base import BaseIndicator
from alphaQuantSystem.indicator.ma import SMA_Indicator, EMA_Indicator, WMA_Indicator
from alphaQuantSystem.indicator.momentum import (
    RSI_Indicator,
    MACD_Indicator,
    KDJ_Indicator,
    CCI_Indicator,
    ATR_Indicator,
)
from alphaQuantSystem.indicator import functional
from alphaQuantSystem.indicator import primitive
from alphaQuantSystem.indicator.six_pulse import six_pulse_excalibur
from alphaQuantSystem.indicator.expma_kdj import expma, kdj as kdj_ohlc

__all__ = [
    'BaseIndicator',
    'SMA_Indicator', 'EMA_Indicator', 'WMA_Indicator',
    'RSI_Indicator', 'MACD_Indicator', 'KDJ_Indicator',
    'CCI_Indicator', 'ATR_Indicator',
    'functional',
    'primitive',
    'six_pulse_excalibur',
    'expma',
    'kdj_ohlc',
]
