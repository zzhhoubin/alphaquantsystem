"""
绩效分析 —— QuantStats 封装
"""
from __future__ import annotations

import math
import warnings
from contextlib import contextmanager
from typing import Optional, Union

import pandas as pd
from loguru import logger

try:
    import quantstats as qs

    _HAS_QS = True
except ImportError:
    _HAS_QS = False


def _configure_matplotlib_cjk() -> None:
    """
    QuantStats 绘图会使用策略/基准名称等中文；默认 DejaVu Sans 无 CJK 字模会刷屏 ``Glyph ... missing``。
    在系统中选取首个可用的中文字体设为 ``font.sans-serif`` 首选。
    """
    try:
        import matplotlib
        from matplotlib import font_manager
    except ImportError:
        return
    candidates = (
        'Microsoft YaHei',
        'Microsoft YaHei UI',
        'SimHei',
        'SimSun',
        'NSimSun',
        'KaiTi',
        'FangSong',
        'STSong',
        'Noto Sans CJK SC',
        'Source Han Sans SC',
        'WenQuanYi Micro Hei',
    )
    names = {f.name for f in font_manager.fontManager.ttflist}
    ordered = [c for c in candidates if c in names]
    if not ordered:
        return
    matplotlib.rcParams['font.sans-serif'] = ordered + ['DejaVu Sans', 'sans-serif']
    matplotlib.rcParams['axes.unicode_minus'] = False


@contextmanager
def _suppress_qs_future_warnings():
    """
    QuantStats 0.0.6x 仍使用 pandas 已弃用的 M/A/Q 频率与若干会触发 FutureWarning 的写法；
    在调用其 reports/stats 期间屏蔽 FutureWarning，避免污染回测脚本输出。
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        yield


@contextmanager
def _quantstats_plot_context():
    """调用 QuantStats 带图报告时的告警环境：中文字体 + 屏蔽缺字备用告警。"""
    _configure_matplotlib_cjk()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        warnings.filterwarnings(
            'ignore',
            message=r'Glyph \d+ .* missing from font',
            category=UserWarning,
        )
        yield


def _prepare_equity_series(equity: pd.Series, *, anchor_initial: Optional[float] = None) -> pd.Series:
    """与 ``PerformanceAnalyzer.load_from_equity`` 相同的权益序列清洗与首日锚定逻辑。"""
    s = pd.Series(
        pd.to_numeric(equity, errors='coerce').to_numpy(dtype=float),
        index=pd.to_datetime(equity.index),
        name=getattr(equity, 'name', None) or 'equity',
    )
    s = s.sort_index()
    s = s[~s.index.duplicated(keep='last')]
    if s.isna().all() or s.dropna().empty:
        raise ValueError('权益序列无有效数值')
    s = s.dropna()
    if anchor_initial is not None and float(anchor_initial) > 0:
        a = float(anchor_initial)
        first_dt = s.index[0]
        syn = first_dt - pd.Timedelta(days=1)
        while syn in s.index:
            syn -= pd.Timedelta(days=1)
        s = pd.concat([pd.Series([a], index=[syn], dtype=float, name=s.name), s]).sort_index()
    return s


def _fmt_payoff_ratio(x: float) -> str:
    """盈亏比（平均盈利日收益 / 平均亏损日收益绝对值）展示格式化。"""
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return 'N/A'
    if math.isnan(xf):
        return 'N/A'
    if math.isinf(xf):
        return '∞' if xf > 0 else 'N/A'
    return f'{xf:.4f}'


class PerformanceAnalyzer:
    """
    QuantStats 绩效分析封装
    输入：净值序列（pd.Series，index 为日期）
    """

    def __init__(self, returns: Optional[pd.Series] = None):
        if not _HAS_QS:
            raise ImportError('quantstats 未安装，请执行: pip install quantstats')
        self._returns = returns
        self._equity_levels: Optional[pd.Series] = None

    def load_returns(self, returns: pd.Series):
        self._returns = returns

    def load_from_equity(self, equity: pd.Series, *, anchor_initial: Optional[float] = None) -> None:
        """
        从权益曲线构造日收益率序列供 QuantStats 使用。

        ``anchor_initial``：回测初始资金。若传入，会在首根 K 线净值前插入一个虚拟时点
        （值为初始资金），使日收益序列从「真·初始资金」连到首日收盘净值；否则总收益
        会锚在 ``equity`` 的第一个样本上，与 ``BacktestEngine`` 的 ``(终值-初始现金)/初始现金`` 不一致。
        """
        s = _prepare_equity_series(equity, anchor_initial=anchor_initial)
        self._equity_levels = s
        self._returns = s.pct_change().dropna()
        if self._returns.empty:
            raise ValueError('权益序列过短，无法计算日收益率')

    def report(self, output: str = 'html', filename: str = 'report.html',
               benchmark: Optional[Union[str, pd.Series]] = None):
        """
        生成完整绩效报告
        output: 'html' | 'full' | 'basic'
        benchmark: QuantStats 兼容 —— 标的代码字符串（拉远程收益）或与本策略 **同日索引** 对齐的日收益率 ``Series``。
        """
        self._check()
        bench_arg: Optional[Union[str, pd.Series]] = benchmark
        if isinstance(benchmark, pd.Series) and not benchmark.empty:
            br = benchmark.copy()
            br.index = pd.to_datetime(br.index)
            br = br.sort_index()
            br = br[~br.index.duplicated(keep='last')]
            br = br.reindex(self._returns.index)
            br = br.ffill().bfill()
            if br.isna().any():
                br = br.fillna(0.0)
            bench_arg = br
        if output == 'html':
            with _quantstats_plot_context():
                qs.reports.html(self._returns, benchmark=bench_arg, output=filename)
            logger.info(f'绩效报告已生成: {filename}')
        elif output == 'full':
            with _quantstats_plot_context():
                qs.reports.full(self._returns, benchmark=bench_arg)
        else:
            with _quantstats_plot_context():
                qs.reports.basic(self._returns, benchmark=bench_arg)

    def summary(self) -> dict:
        """返回与 analyze.metrics 一致的关键指标（可选 QuantStats 补充）。"""
        self._check()
        from alphaQuantSystem.analyze.metrics import PerformanceContext, compute_performance_metrics

        ctx = PerformanceContext(
            initial_cash=float(self._equity_levels.iloc[0]) if self._equity_levels is not None and len(
                self._equity_levels) else 0.0,
            equity_series=self._equity_levels,
        )
        m = compute_performance_metrics(ctx)
        if m:
            return {
                'total_return': m['total_return_pct'],
                'cagr': m['annual_return_pct'],
                'sharpe': round(m['sharpe_ratio'], 4),
                'max_drawdown': m['max_drawdown_pct'],
                'volatility': m['volatility_pct'],
                'daily_win_rate': m['daily_win_rate_pct'],
            }

        r = self._returns
        with _suppress_qs_future_warnings():
            sortino = round(qs.stats.sortino(r), 4)
            calmar = round(qs.stats.calmar(r), 4)
            plr = qs.stats.win_loss_ratio(r)
            return {
                'total_return': f'{qs.stats.comp(r):.2%}',
                'cagr': f'{qs.stats.cagr(r):.2%}',
                'sharpe': round(qs.stats.sharpe(r), 4),
                'sortino': sortino,
                'max_drawdown': f'{qs.stats.max_drawdown(r):.2%}',
                'calmar': calmar,
                'win_rate': f'{qs.stats.win_rate(r):.2%}',
                'profit_loss_ratio': _fmt_payoff_ratio(plr),
                'volatility': f'{qs.stats.volatility(r):.2%}',
            }

    def _check(self):
        if self._returns is None or self._returns.empty:
            raise ValueError('请先加载收益率序列')
