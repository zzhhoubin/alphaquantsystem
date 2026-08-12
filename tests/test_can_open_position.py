"""BaseStrategy.can_open_position 含滑点/佣金估算。"""
from datetime import datetime

import pytest

from alphaQuantSystem.backtest.commission import AShareCommission
from alphaQuantSystem.core import EventEngine, TradeData, Direction, BarData
from alphaQuantSystem.strategy.template import BaseStrategy


class _StubStrategy(BaseStrategy):
    def on_bar(self, bar: BarData):
        pass


@pytest.fixture
def strategy():
    ee = EventEngine(sync_mode=True)
    s = _StubStrategy('test', ee, {
        'initial_capital': 100_000.0,
        'max_position_ratio': 1.0,
        'backtest_slippage_perc': 0.001,
        'backtest_commission': 0.0003,
    })
    s.position_manager.cash = 50_000.0
    return s


def test_rejects_when_cash_insufficient_with_commission(strategy):
    """名义够、含佣金后不够时应拒绝。"""
    strategy.config['external_get_commission'] = AShareCommission().calculate
    price, vol = 10.0, 4999.0
    strategy.position_manager.cash = 50_000.0
    _, _, cash_need = strategy._estimate_long_cost('600000.SH', price, vol)
    assert price * vol < strategy.position_manager.cash < cash_need
    assert strategy.can_open_position('600000.SH', price, vol) is False


def test_market_order_includes_slippage(strategy):
    """市价语义计入滑点，边界现金更紧。"""
    strategy.config['external_get_commission'] = AShareCommission().calculate
    price, vol = 10.0, 4980.0
    _, _, limit_need = strategy._estimate_long_cost('600000.SH', price, vol, market_order=False)
    _, _, market_need = strategy._estimate_long_cost('600000.SH', price, vol, market_order=True)
    assert market_need > limit_need
    strategy.position_manager.cash = limit_need + 1.0
    assert strategy.can_open_position('600000.SH', price, vol, market_order=False) is True
    assert strategy.can_open_position('600000.SH', price, vol, market_order=True) is False


def test_fallback_commission_without_external_fn(strategy):
    """无 external_get_commission 时按费率+最低佣金降级。"""
    price, vol = 1.0, 100.0
    strategy.position_manager.cash = 104.0
    assert strategy.can_open_position('600000.SH', price, vol) is False
    strategy.position_manager.cash = 105.0
    assert strategy.can_open_position('600000.SH', price, vol) is True
