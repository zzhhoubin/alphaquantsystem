"""
回测模块 —— 事件驱动自建回测引擎 + 统一绩效报告。
"""
from .result import BacktestResult
from .commission import (
    CommissionModel,
    AShareCommission,
    ETFCommission,
    AdaptiveCommission,
)
from .trade_report import build_trade_detail_df, build_trade_detail_records, TRADE_DETAIL_FIELD_DOCS
from .matching_engine import MatchingEngine
from .report import BacktestReporter

__all__ = [
    'BacktestResult',
    'CommissionModel',
    'AShareCommission',
    'ETFCommission',
    'AdaptiveCommission',
    'build_trade_detail_df',
    'build_trade_detail_records',
    'TRADE_DETAIL_FIELD_DOCS',
    'MatchingEngine',
    'BacktestReporter',
]
