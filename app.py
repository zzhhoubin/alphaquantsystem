# alphaQuantSystem/app.py
"""App Builder — the single user-facing entry point to the framework"""
from __future__ import annotations
from typing import Any, Dict, List, Literal, Optional, Type, Union

from alphaQuantSystem.core.config_manager import ConfigManager
from alphaQuantSystem.strategy.template import BaseStrategy
from alphaQuantSystem.engine.engine import StrategyEngine
from alphaQuantSystem.backtest.result import BacktestResult
from alphaQuantSystem.monitor import setup_logger
from alphaQuantSystem.monitor.logger import is_logger_configured

_logger_initialized = False


class App:
    """App Builder — assemble and run quantitative applications

    Usage:
        app = App()
            .use_qmt(is_live=True)
            .with_data(sources=["qmt", "akshare"])
            .with_risk(max_order_notional=300000)
            .add_strategy(DoubleMA, params={...}, symbols=["510050.SH"], warmup_bars=25)
            .run(mode="backtest", start="20240101", end="20241231")
    """

    def __init__(self):
        global _logger_initialized
        if not _logger_initialized:
            if not is_logger_configured():
                setup_logger()
            _logger_initialized = True
        self._config = ConfigManager()
        self._engine = StrategyEngine()
        self._mode_config: Dict[str, Any] = {}
        self._qmt_is_live: bool = False
        self._use_qmt_configured: bool = False
        self._global_risk: Dict[str, Any] = {}
        self._global_trading: Dict[str, Any] = {}

    # ── Infrastructure ──
    def with_config(self, config: Union[str, Dict[str, Any]]) -> "App":
        """Load global config (YAML file path or dict)"""
        if isinstance(config, str):
            self._config.load_yaml(config)
        elif isinstance(config, dict):
            self._config.load_dict(config)
        return self

    # ── Gateway ──
    def use_qmt(self, is_live: bool = True) -> "App":
        """Configure QMT gateway"""
        self._use_qmt_configured = True
        self._qmt_is_live = is_live
        return self

    # ── Data ──
    def with_data(self, sources: List[str] = None, cache_dir: str = None) -> "App":
        """Configure data source priority and cache"""
        if sources:
            self._mode_config["data_sources"] = sources
        if cache_dir:
            self._mode_config["data_cache_dir"] = cache_dir
        return self

    # ── Global risk defaults ──
    def with_risk(self, **kwargs) -> "App":
        """Set global risk defaults (fallback for all strategies)"""
        self._global_risk.update(kwargs)
        return self

    # ── Global trading params ──
    def with_trading(self, **kwargs) -> "App":
        """Set global trading params (commission, stamp_duty, slippage, etc.)"""
        self._global_trading.update(kwargs)
        return self

    # ── Register strategy ──
    def add_strategy(
        self,
        strategy_cls: Type[BaseStrategy],
        *,
        params: Optional[Dict[str, Any]] = None,
        symbols: Optional[List[str]] = None,
        strategy_id: Optional[str] = None,
        warmup_bars: int = 0,
        period: str = "D",
        risk: Optional[Dict[str, Any]] = None,
        schedule: Optional[Dict[str, str]] = None,
    ) -> "App":
        """Register a strategy"""
        self._engine.register(
            strategy_cls=strategy_cls,
            params=params,
            symbols=symbols,
            strategy_id=strategy_id,
            warmup_bars=warmup_bars,
            period=period,
            risk=risk,
            schedule=schedule,
        )
        return self

    # ── Run ──
    def run(
        self,
        mode: Literal["live", "backtest"],
        *,
        start: Optional[str] = None,
        end: Optional[str] = None,
        initial_cash: float = 1_000_000,
    ) -> Union[None, BacktestResult, Dict[str, BacktestResult]]:
        """Launch the application

        Args:
            mode: "live" | "backtest"
            start: Backtest start date "YYYYMMDD" (required for backtest)
            end: Backtest end date "YYYYMMDD" (required for backtest)
            initial_cash: Backtest initial capital

        Returns:
            mode="live" -> None
            mode="backtest" (single strategy) -> BacktestResult
            mode="backtest" (multi-strategy) -> Dict[str, BacktestResult]
        """
        if self._global_risk:
            for reg in self._engine._strategy_regs:
                if reg.risk is None:
                    reg.risk = dict(self._global_risk)
                elif reg.risk.get("enabled") is False:
                    continue

        sources = self._mode_config.get("data_sources") or []
        hist_source = sources[0] if sources else None

        return self._engine.run(
            mode=mode, start=start, end=end, initial_cash=initial_cash,
            commission_rate=self._global_trading.get("commission_rate", 0.0003),
            slippage=self._global_trading.get("slippage", 0.0),
            hist_source=hist_source,
            qmt_is_live=self._qmt_is_live if mode == "live" else False,
        )
