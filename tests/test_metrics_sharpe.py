"""绩效指标：日收益率与夏普比率。"""
from __future__ import annotations

from datetime import date

from alphaQuantSystem.analyze.metrics import (
    PerformanceContext,
    compute_performance_metrics,
    _resolve_daily_returns,
)
from alphaQuantSystem.core.object import AccountSnapshot


def test_resolve_daily_returns_from_equity_when_snapshot_field_zero():
    # 快照仅含各交易日收盘权益，不含初始资金日
    equity = [1_010_000.0, 1_005_000.0, 1_020_000.0]
    stored = [0.0, 0.0, 0.0]
    derived = _resolve_daily_returns(equity, stored, 1_000_000.0)
    assert len(derived) == 3
    assert abs(derived[0] - 0.01) < 1e-9
    assert abs(derived[1] - (-0.004950495)) < 1e-6


def test_sharpe_volatility_not_zero_when_equity_moves():
    snapshots = [
        AccountSnapshot(date=date(2024, 1, 2), total_value=1_010_000.0),
        AccountSnapshot(date=date(2024, 1, 3), total_value=1_005_000.0),
        AccountSnapshot(date=date(2024, 1, 4), total_value=1_020_000.0),
    ]
    ctx = PerformanceContext(initial_cash=1_000_000.0, account_snapshots=snapshots)
    metrics = compute_performance_metrics(ctx)
    assert metrics["volatility"] > 0
    assert abs(metrics["sharpe_ratio"]) < 50


def test_sharpe_uses_populated_daily_return():
    snapshots = [
        AccountSnapshot(date=date(2024, 1, 2), total_value=1_010_000.0, daily_return=0.01),
        AccountSnapshot(date=date(2024, 1, 3), total_value=1_005_000.0, daily_return=-0.00495),
    ]
    ctx = PerformanceContext(initial_cash=1_000_000.0, account_snapshots=snapshots)
    metrics = compute_performance_metrics(ctx)
    assert metrics["trading_days"] == 2
    assert metrics["volatility"] > 0
