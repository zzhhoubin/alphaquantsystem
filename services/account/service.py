# alphaQuantSystem/services/account/service.py
from __future__ import annotations
from typing import Optional

from alphaQuantSystem.core import Direction, TradeData


class AccountService:
    """Account service — tracks cash, total value, P&L"""

    def __init__(self, initial_cash: float = 1_000_000.0):
        self._initial_cash = initial_cash
        self._cash = initial_cash
        self._locked_cash: float = 0.0
        self._total_commission: float = 0.0
        self._daily_pnl: float = 0.0
        self._cumulative_pnl: float = 0.0

    @property
    def available(self) -> float:
        return self._cash - self._locked_cash

    @property
    def locked(self) -> float:
        return self._locked_cash

    @property
    def cash(self) -> float:
        return self._cash

    @property
    def total_commission(self) -> float:
        return self._total_commission

    @property
    def daily_pnl(self) -> float:
        return self._daily_pnl

    @property
    def cumulative_pnl(self) -> float:
        return self._cumulative_pnl

    @property
    def starting_cash(self) -> float:
        return self._initial_cash

    def total_value(self, positions_market_value: float) -> float:
        return self._cash + positions_market_value

    def apply_trade(self, trade: TradeData, commission: float = 0.0) -> None:
        trade_value = trade.price * trade.volume
        if trade.direction == Direction.LONG:
            self._cash -= trade_value + commission
        else:
            self._cash += trade_value - commission
        self._total_commission += commission

    def apply_pnl(self, daily_pnl: float) -> None:
        self._daily_pnl = daily_pnl
        self._cumulative_pnl += daily_pnl

    def set_cash(self, cash: float) -> None:
        """实盘：从券商同步可用资金。"""
        self._cash = float(cash)

    def lock_cash(self, amount: float) -> None:
        self._locked_cash += amount

    def unlock_cash(self, amount: float) -> None:
        self._locked_cash = max(0.0, self._locked_cash - amount)

    def get_snapshot(self, positions_market_value: float) -> dict:
        return {
            "available_cash": self.available,
            "locked_cash": self._locked_cash,
            "cash": self._cash,
            "total_value": self.total_value(positions_market_value),
            "daily_pnl": self._daily_pnl,
            "cumulative_pnl": self._cumulative_pnl,
            "starting_cash": self._initial_cash,
            "total_commission": self._total_commission,
        }
