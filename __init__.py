"""alphaQuant — Quantitative Trading Framework"""
from __future__ import annotations

from typing import Any

__all__ = [
    "App",
    "BaseStrategy",
    "StrategyContext",
    "BarData", "TickData", "SignalData", "TradeData", "OrderData",
    "PositionView", "PortfolioView",
]

_LAZY_EXPORTS = {
    "App": (".app", "App"),
    "BaseStrategy": (".strategy.template", "BaseStrategy"),
    "StrategyContext": (".core.context", "StrategyContext"),
    "PositionView": (".core.context", "PositionView"),
    "PortfolioView": (".core.context", "PortfolioView"),
    "BarData": (".core", "BarData"),
    "TickData": (".core", "TickData"),
    "SignalData": (".core", "SignalData"),
    "TradeData": (".core", "TradeData"),
    "OrderData": (".core", "OrderData"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr = target
    from importlib import import_module
    value = getattr(import_module(module_name, __name__), attr)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
