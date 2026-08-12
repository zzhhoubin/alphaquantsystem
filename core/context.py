# alphaQuantSystem/core/context.py
"""StrategyContext — the sole interface between strategy and framework"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from alphaQuantSystem.core import BarData
    from alphaQuantSystem.backtest.report import BacktestReporter
    from alphaQuantSystem.services.position import PositionService
    from alphaQuantSystem.services.account import AccountService
    from alphaQuantSystem.data.data_engine import DataEngine


@dataclass
class PositionView:
    """Single symbol position view — snapshot from PositionService"""
    total_amount: float = 0.0
    closeable_amount: float = 0.0
    avg_cost: float = 0.0
    price: float = 0.0
    value: float = 0.0
    pnl: float = 0.0


@dataclass
class PortfolioView:
    """Portfolio view — refreshed by engine before each BAR/SCHEDULE event"""
    positions: Dict[str, PositionView] = field(default_factory=dict)
    available_cash: float = 0.0
    locked_cash: float = 0.0
    total_value: float = 0.0
    positions_value: float = 0.0
    starting_cash: float = 0.0
    daily_pnl: float = 0.0
    cumulative_pnl: float = 0.0


class StrategyContext:
    """Strategy context — strategies access the world only through self.ctx"""

    def __init__(self):
        # Time
        self.current_dt: datetime = datetime.now()
        self.current_date: date = date.today()
        self.previous_date: Optional[date] = None

        # Portfolio
        self.portfolio: PortfolioView = PortfolioView()

        # Internal refs (set by engine)
        self._mode: str = "backtest"
        self._warmup: bool = False
        self._data_engine: Optional["DataEngine"] = None
        self._position_svc: Optional["PositionService"] = None
        self._account_svc: Optional["AccountService"] = None
        self._reporter: Optional["BacktestReporter"] = None
        self._live_sync_broker: Optional[Callable[[], None]] = None

    # ── Data access ──
    def hist(self, symbol: str, period: str = "D", count: int = 200) -> List["BarData"]:
        """Get historical K-line data — the only data access point"""
        if self._data_engine is None:
            return []
        end = self.current_date.strftime("%Y%m%d")
        start = (self.current_date - timedelta(days=count * 3)).strftime("%Y%m%d")
        df = self._data_engine.get_hist_data(symbol, period, start, end)
        if df.empty:
            return []
        from alphaQuantSystem.strategy.bar_utils import bar_from_ohlcv_row
        bars = []
        for _, row in df.iterrows():
            bars.append(bar_from_ohlcv_row(row, symbol, period))
        return bars[-count:] if len(bars) > count else bars

    # ── Meta ──
    def is_warmup(self) -> bool:
        return self._warmup

    def log(self, msg: str, level: str = "INFO") -> None:
        from loguru import logger
        log_fn = getattr(logger, "info" if level == "INFO" else "warning" if level == "WARNING" else "error" if level == "ERROR" else "debug")
        log_fn(f"[Strategy] {msg}")

    # ── Strategy metrics recording ──
    def record(self, **kwargs) -> None:
        """Record strategy custom metrics for backtest report"""
        if self.is_warmup():
            return
        if self._mode != "backtest":
            return
        if self._reporter is not None:
            row = {"datetime": self.current_dt, "date": self.current_date, **kwargs}
            self._reporter.record_strategy_row(row)
