# alphaQuantSystem/services/position/service.py
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from alphaQuantSystem.core import Direction, TradeData


@dataclass
class Position:
    """Single symbol position"""
    symbol: str
    volume: float = 0.0
    frozen: float = 0.0
    avg_price: float = 0.0
    current_price: float = 0.0
    market_value: float = 0.0
    pnl: float = 0.0

    @property
    def closeable_amount(self) -> float:
        return max(0.0, self.volume - self.frozen)

    @property
    def total_amount(self) -> float:
        return self.volume


@dataclass
class PositionSnapshot:
    date: object
    positions: Dict[str, Position] = field(default_factory=dict)


class PositionService:
    """Position tracking service — tracks all positions, provides snapshots"""

    def __init__(self):
        self._positions: Dict[str, Position] = {}

    def reset(self) -> None:
        """清空持仓（回测开始前调用）。"""
        self._positions.clear()

    def get(self, symbol: str) -> Optional[Position]:
        return self._positions.get(symbol)

    def all(self) -> Dict[str, Position]:
        return dict(self._positions)

    def snapshot(self) -> Dict[str, Position]:
        """Return full position snapshot (shallow copy)"""
        return {k: Position(
            symbol=v.symbol, volume=v.volume, frozen=v.frozen,
            avg_price=v.avg_price, current_price=v.current_price,
            market_value=v.market_value, pnl=v.pnl,
        ) for k, v in self._positions.items()}

    def sync_from_broker(self, snapshots: Dict[str, dict]) -> None:
        """实盘：用券商持仓快照覆盖本地账本。"""
        self._positions.clear()
        for sym, snap in snapshots.items():
            vol = float(snap.get("volume", 0) or 0)
            if vol <= 0:
                continue
            avail = float(snap.get("available", vol) or vol)
            price = float(snap.get("current_price", 0) or 0)
            avg = float(snap.get("avg_price", 0) or 0)
            self._positions[sym] = Position(
                symbol=sym,
                volume=vol,
                frozen=max(0.0, vol - avail),
                avg_price=avg,
                current_price=price,
                market_value=vol * price,
            )

    def apply_trade(self, trade: TradeData) -> None:
        """Apply trade — called by BacktestExecution or live TRADE handler"""
        pos = self._positions.get(trade.symbol)
        if pos is None:
            pos = Position(symbol=trade.symbol)
            self._positions[trade.symbol] = pos

        if trade.direction == Direction.LONG:
            total_cost = pos.volume * pos.avg_price + trade.volume * trade.price
            pos.volume += trade.volume
            if pos.volume > 0:
                pos.avg_price = total_cost / pos.volume
        else:
            pos.volume -= trade.volume
            if pos.volume <= 1e-8:
                del self._positions[trade.symbol]

    def update_price(self, symbol: str, price: float) -> None:
        """Update market price"""
        pos = self._positions.get(symbol)
        if pos:
            pos.current_price = price
            pos.market_value = pos.volume * price
            pos.pnl = (price - pos.avg_price) * pos.volume

    def total_market_value(self) -> float:
        return sum(p.market_value for p in self._positions.values())
