"""撮合引擎 match() 回归：限价可成交、资金/持仓裁剪、佣金。"""
from __future__ import annotations

from datetime import datetime

from alphaQuantSystem.backtest.commission import ETFCommission
from alphaQuantSystem.backtest.matching_engine import MatchingEngine
from alphaQuantSystem.core import BarData, Direction, OrderData, OrderType


def test_match_limit_buy_respects_cash_and_lot_size():
    matcher = MatchingEngine(commission_model=ETFCommission(commission_rate=0.00005, min_commission=0.0))
    cash = {"v": 50_000.0}
    matcher.set_available_cash_resolver(lambda: cash["v"])

    bar = BarData(
        symbol="159509.SZ", open=2.0, high=2.2, low=1.9, close=2.1, volume=1e6,
        event_time=datetime(2024, 6, 3), interval="D",
    )
    order = OrderData(
        order_id="o1", strategy_id="t", symbol="159509.SZ",
        direction=Direction.LONG, order_type=OrderType.LIMIT,
        volume=100000, price=2.1,
    )
    trade = matcher.match(order, bar)
    assert trade is not None
    assert trade.volume == 23800.0  # 50000 / 2.1 ≈ 23809 → 23800 整手
    cash["v"] -= trade.price * trade.volume + matcher.commission_model.calculate(trade)


def test_match_limit_sell_requires_position():
    matcher = MatchingEngine()
    matcher.set_hold_volume_resolver(lambda _sym: 5000.0)

    bar = BarData(
        symbol="159509.SZ", open=2.0, high=2.2, low=1.9, close=2.1, volume=1e6,
        event_time=datetime(2024, 6, 3), interval="D",
    )
    order = OrderData(
        order_id="o2", strategy_id="t", symbol="159509.SZ",
        direction=Direction.SHORT, order_type=OrderType.LIMIT,
        volume=10000, price=2.1,
    )
    trade = matcher.match(order, bar)
    assert trade is not None
    assert trade.volume == 5000.0
