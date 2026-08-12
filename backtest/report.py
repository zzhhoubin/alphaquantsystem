# alphaQuantSystem/backtest/report.py
"""Backtest report collector — engine-internal, strategy authors never touch directly"""
from __future__ import annotations
from datetime import date as DateType
from typing import Any, Dict, List, Optional

import pandas as pd
from loguru import logger

from alphaQuantSystem.core import TradeData
from alphaQuantSystem.core.object import AccountSnapshot


class BacktestReporter:
    """Engine-internal collector: trade records + daily settlement + build_result()"""

    def __init__(self, commission_model=None, initial_cash: float = 0.0):
        self._commission_model = commission_model
        self._initial_cash = float(initial_cash)
        self._prev_total_value = float(initial_cash)
        self._trades: List[TradeData] = []
        self._account_snapshots: List[AccountSnapshot] = []
        self._position_snapshots: List[Any] = []
        self._strategy_rows: List[Dict[str, Any]] = []
        self._daily_balance: float = 0.0
        self._daily_market_value: float = 0.0

    def record_trade(self, trade: TradeData) -> None:
        """Per-trade record — called by BacktestExecution.execute()"""
        self._trades.append(trade)

    def record_strategy_row(self, row: Dict[str, Any]) -> None:
        """Strategy metrics row — forwarded by StrategyContext.record()"""
        self._strategy_rows.append(dict(row))

    def on_day_end(self, trade_date: DateType) -> None:
        """Daily settlement — called by StrategyEngine main loop after last bar of the day"""
        total_value = self._daily_balance + self._daily_market_value
        daily_pnl = total_value - self._prev_total_value
        daily_return = daily_pnl / self._prev_total_value if self._prev_total_value > 0 else 0.0
        cumulative_pnl = total_value - self._initial_cash
        cumulative_return = (
            cumulative_pnl / self._initial_cash if self._initial_cash > 0 else 0.0
        )
        snap = AccountSnapshot(
            date=trade_date,
            balance=self._daily_balance,
            market_value=self._daily_market_value,
            total_value=total_value,
            daily_pnl=daily_pnl,
            daily_return=daily_return,
            cumulative_pnl=cumulative_pnl,
            cumulative_return=cumulative_return,
            available_cash=self._daily_balance,
        )
        self._account_snapshots.append(snap)
        self._prev_total_value = total_value

    def set_daily_state(self, balance: float, market_value: float) -> None:
        self._daily_balance = balance
        self._daily_market_value = market_value

    def build_result(self) -> "BacktestResult":
        """Generate BacktestResult at end of backtest"""
        from alphaQuantSystem.backtest.result import BacktestResult
        result = BacktestResult(
            start_date="",
            end_date="",
            initial_cash=0.0,
            strategy_id="",
            commission_model=self._commission_model,
        )
        for trade in self._trades:
            result.add_trade(trade)
        result.set_account_snapshots(self._account_snapshots)
        result.strategy_records = list(self._strategy_rows)
        return result
