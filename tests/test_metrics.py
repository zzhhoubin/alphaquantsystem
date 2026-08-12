"""绩效指标口径校验：收益、回撤、胜率、盈亏比等。"""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pytest

from alphaQuantSystem.analyze.metrics import (
    PerformanceContext,
    _annualized_return,
    _equity_path_for_drawdown,
    compute_performance_metrics,
)
from alphaQuantSystem.core import Direction, TradeData
from alphaQuantSystem.core.object import AccountSnapshot


def _snap(d: date, total: float, **kwargs) -> AccountSnapshot:
    return AccountSnapshot(date=d, total_value=total, **kwargs)


def test_total_return_and_pnl():
    snapshots = [
        _snap(date(2024, 1, 2), 1_050_000.0, daily_return=0.05),
    ]
    ctx = PerformanceContext(
        initial_cash=1_000_000.0,
        account_snapshots=snapshots,
        start_date='2024-01-02',
        end_date='2024-01-02',
    )
    m = compute_performance_metrics(ctx)
    assert abs(m['total_return'] - 0.05) < 1e-9
    assert abs(m['total_pnl'] - 50_000.0) < 1e-6
    assert abs(m['final_value'] - 1_050_000.0) < 1e-6


def test_cumulative_return_matches_total_return():
    snapshots = [
        _snap(
            date(2024, 1, 4), 1_020_000.0,
            cumulative_return=0.02, daily_return=0.01,
        ),
    ]
    ctx = PerformanceContext(initial_cash=1_000_000.0, account_snapshots=snapshots)
    m = compute_performance_metrics(ctx)
    assert abs(m['total_return'] - 0.02) < 1e-9


def test_max_drawdown_counts_first_day_drop_from_initial():
    snapshots = [
        _snap(date(2024, 1, 2), 900_000.0),
        _snap(date(2024, 1, 3), 950_000.0),
    ]
    ctx = PerformanceContext(initial_cash=1_000_000.0, account_snapshots=snapshots)
    m = compute_performance_metrics(ctx)
    assert abs(m['max_drawdown'] - 0.10) < 1e-9


def test_equity_path_for_drawdown_prepends_initial():
    path = _equity_path_for_drawdown([900_000.0, 950_000.0], 1_000_000.0)
    assert path == [1_000_000.0, 900_000.0, 950_000.0]
    path_same = _equity_path_for_drawdown([1_000_000.0, 1_010_000.0], 1_000_000.0)
    assert path_same == [1_000_000.0, 1_010_000.0]


def test_annual_return_uses_calendar_when_dates_given():
    # 约 1 年区间，总收益 10% → 年化应接近 10%
    ann = _annualized_return(0.10, trading_days=252, start_date='2024-01-02', end_date='2025-01-02')
    assert abs(ann - 0.10) < 0.02
    # 无日期时按交易日年化
    ann_td = _annualized_return(0.10, trading_days=126)
    assert ann_td > 0.10


def test_equity_series_daily_returns_are_consecutive():
    series = pd.Series([1_000_000.0, 1_010_000.0, 1_020_000.0])
    ctx = PerformanceContext(initial_cash=1_000_000.0, equity_series=series)
    m = compute_performance_metrics(ctx)
    assert abs(m['total_return'] - 0.02) < 1e-9
    assert m['trading_days'] == 3
    assert m['volatility'] > 0


def test_round_trip_win_rate_and_profit_loss_ratio():
    t0 = datetime(2024, 1, 2, 10, 0)
    t1 = datetime(2024, 1, 3, 10, 0)
    t2 = datetime(2024, 1, 4, 10, 0)
    t3 = datetime(2024, 1, 5, 10, 0)
    trades = [
        TradeData('1', 'o1', 's', 'AAA', Direction.LONG, 10.0, 100, t0),
        TradeData('2', 'o2', 's', 'AAA', Direction.SHORT, 12.0, 100, t1),
        TradeData('3', 'o3', 's', 'AAA', Direction.LONG, 10.0, 100, t2),
        TradeData('4', 'o4', 's', 'AAA', Direction.SHORT, 9.0, 100, t3),
    ]
    snapshots = [_snap(date(2024, 1, 5), 1_000_100.0)]
    ctx = PerformanceContext(
        initial_cash=1_000_000.0,
        account_snapshots=snapshots,
        trades=trades,
    )
    m = compute_performance_metrics(ctx)
    assert m['closed_trades'] == 2
    assert m['winning_trades'] == 1
    assert m['losing_trades'] == 1
    assert abs(m['win_rate'] - 0.5) < 1e-9
    assert abs(m['avg_win_gross'] - 200.0) < 1e-6
    assert abs(m['avg_loss_gross'] - 100.0) < 1e-6
    assert abs(m['profit_loss_ratio_gross'] - 2.0) < 1e-6
    assert abs(m['profit_factor_gross'] - 2.0) < 1e-6


def test_unmatched_sell_not_counted_as_closed_trade():
    """无 FIFO 配对的卖出不计入平仓统计。"""
    t0 = datetime(2024, 1, 2, 10, 0)
    trades = [
        TradeData('1', 'o1', 's', 'AAA', Direction.SHORT, 10.0, 100, t0),
    ]
    snapshots = [_snap(date(2024, 1, 2), 1_001_000.0)]
    ctx = PerformanceContext(
        initial_cash=1_000_000.0,
        account_snapshots=snapshots,
        trades=trades,
    )
    m = compute_performance_metrics(ctx)
    assert m['closed_trades'] == 0
    assert m['total_trades'] == 1


class _FlatCommission:
    def calculate(self, trade: TradeData, *, quiet: bool = False) -> float:
        return 5.0


def test_net_win_rate_after_commission():
    t0 = datetime(2024, 1, 2, 10, 0)
    t1 = datetime(2024, 1, 3, 10, 0)
    trades = [
        TradeData('1', 'o1', 's', 'AAA', Direction.LONG, 10.0, 100, t0),
        TradeData('2', 'o2', 's', 'AAA', Direction.SHORT, 12.0, 100, t1),
    ]
    snapshots = [_snap(date(2024, 1, 3), 1_000_000.0)]
    ctx = PerformanceContext(
        initial_cash=1_000_000.0,
        account_snapshots=snapshots,
        trades=trades,
        commission_model=_FlatCommission(),
    )
    m = compute_performance_metrics(ctx)
    assert m['closed_trades'] == 1
    # 毛盈亏 200，双边佣金 10 → 净 190
    assert abs(m['avg_win'] - 190.0) < 1e-6
    assert m['win_rate'] == 1.0
