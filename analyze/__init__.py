"""
绩效分析 —— 回测与实盘共用指标 + QuantStats 可视化。
"""
from alphaQuantSystem.backtest.trade_report import TRADE_DETAIL_FIELD_DOCS

from .metrics import METRIC_LABELS, PerformanceContext, compute_performance_metrics
from .performance import PerformanceAnalyzer
from .collector import LivePerformanceCollector

__all__ = [
    'PerformanceAnalyzer',
    'METRIC_LABELS',
    'PerformanceContext',
    'compute_performance_metrics',
    'LivePerformanceCollector',
    'TRADE_DETAIL_FIELD_DOCS',
]
