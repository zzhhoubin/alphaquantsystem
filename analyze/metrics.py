"""
统一绩效指标 —— 回测与实盘共用。

组合指标来自权益曲线（日快照或日收益序列）；
交易指标来自成交列表 + 佣金模型（FIFO round-trip，见 backtest.trade_report）。

指标命名约定：
    win_rate_gross / profit_loss_ratio / ...：平仓 round-trip **毛**盈亏（pnl_gross）
    win_rate / profit_loss_ratio_net / ...：**扣买卖双边手续费**净盈亏（pnl）
    daily_win_rate：盈利日占比（仅在有日收益序列时）
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import numpy as np
import pandas as pd

from alphaQuantSystem.core import TradeData
from alphaQuantSystem.backtest.trade_report import build_trade_detail_df

if TYPE_CHECKING:
    from alphaQuantSystem.backtest.account_manager import AccountSnapshot
    from alphaQuantSystem.backtest.commission import CommissionModel
    from alphaQuantSystem.trader.position_manager import PositionSnapshot


# compute_performance_metrics() 返回字段 → 中文名（摘要展示 / Excel 表头可引用）
METRIC_LABELS: Dict[str, str] = {
    # —— 元信息 ——
    'strategy_id': '策略标识',
    'start_date': '回测开始日期',
    'end_date': '回测结束日期',
    'trading_days': '交易日数',
    # —— 组合收益 ——
    'initial_cash': '初始资金',
    'final_value': '期末总资产',
    'total_return': '总收益率',
    'total_return_pct': '总收益率（格式化）',
    'total_pnl': '总盈亏',
    'annual_return': '年化收益率',
    'annual_return_pct': '年化收益率（格式化）',
    # —— 组合风险 ——
    'max_drawdown': '最大回撤',
    'max_drawdown_pct': '最大回撤（格式化）',
    'max_drawdown_duration': '最大回撤持续天数',
    'sharpe_ratio': '夏普比率',
    'volatility': '年化波动率',
    'volatility_pct': '年化波动率（格式化）',
    'daily_win_rate': '日胜率',
    'daily_win_rate_pct': '日胜率（格式化）',
    # —— 成交统计 ——
    'total_trades': '总成交笔数',
    'closed_trades': '已平仓笔数',
    'winning_trades': '盈利平仓笔数',
    'losing_trades': '亏损平仓笔数',
    'total_commission': '累计手续费',
    'avg_position_count': '平均持仓标的数',
    # —— 平仓 round-trip · 毛盈亏（不含手续费）——
    'win_rate_gross': '胜率（毛）',
    'win_rate_gross_pct': '胜率（毛，格式化）',
    'profit_loss_ratio_gross': '盈亏比（毛）',
    'profit_loss_ratio_gross_fmt': '盈亏比（毛，格式化）',
    'avg_win_gross': '平均盈利（毛）',
    'avg_loss_gross': '平均亏损（毛）',
    'profit_factor_gross': '盈利因子（毛）',
    'profit_factor_gross_fmt': '盈利因子（毛，格式化）',
    # —— 平仓 round-trip · 净盈亏（已扣双边手续费）——
    'win_rate': '胜率（净）',
    'win_rate_pct': '胜率（净，格式化）',
    'profit_loss_ratio': '盈亏比（净）',
    'profit_loss_ratio_fmt': '盈亏比（净，格式化）',
    'avg_win': '平均盈利（净）',
    'avg_loss': '平均亏损（净）',
    'profit_factor': '盈利因子（净）',
    'profit_factor_fmt': '盈利因子（净，格式化）',
    # win_rate_net / profit_loss_ratio_net 等为净指标别名，与上列同名
    'win_rate_net': '胜率（净）',
    'win_rate_net_pct': '胜率（净，格式化）',
    'profit_loss_ratio_net': '盈亏比（净）',
    'profit_loss_ratio_net_fmt': '盈亏比（净，格式化）',
    'avg_win_net': '平均盈利（净）',
    'avg_loss_net': '平均亏损（净）',
    'profit_factor_net': '盈利因子（净）',
    'profit_factor_net_fmt': '盈利因子（净，格式化）',
}


@dataclass
class PerformanceContext:
    """
    绩效计算输入（回测 / 实盘通用）。

    组合侧：``account_snapshots`` 或 ``equity_series`` 二选一；
    交易侧：``trades`` + 可选 ``commission_model``。
    """
    initial_cash: float
    strategy_id: str = 'strategy'
    start_date: str = ''
    end_date: str = ''
    trades: List[TradeData] = field(default_factory=list)
    account_snapshots: List['AccountSnapshot'] = field(default_factory=list)
    position_snapshots: List['PositionSnapshot'] = field(default_factory=list)
    commission_model: Optional['CommissionModel'] = None
    equity_series: Optional[pd.Series] = None
    total_commission: Optional[float] = None

    @classmethod
    def from_backtest_result(cls, result: Any) -> 'PerformanceContext':
        last_comm = None
        if result.account_snapshots:
            last_comm = float(getattr(result.account_snapshots[-1], 'commission', 0) or 0)
        return cls(
            initial_cash=float(result.initial_cash),
            strategy_id=str(result.strategy_id),
            start_date=str(result.start_date),
            end_date=str(result.end_date),
            trades=list(result.trades),
            account_snapshots=list(result.account_snapshots),
            position_snapshots=list(result.position_snapshots),
            commission_model=getattr(result, 'commission_model', None),
            total_commission=last_comm,
        )

    @classmethod
    def from_live_data(
        cls,
        snapshots: List['AccountSnapshot'],
        trades: List[TradeData],
        initial_cash: float,
        strategy_id: str = 'live',
        start_date: str = '',
        end_date: str = '',
        position_snapshots: Optional[List['PositionSnapshot']] = None,
        commission_model: Optional['CommissionModel'] = None,
    ) -> 'PerformanceContext':
        """从实盘累积数据构建 PerformanceContext（与 from_backtest_result 对齐）。"""
        last_comm = None
        if snapshots:
            last_comm = float(getattr(snapshots[-1], 'commission', 0) or 0)
        return cls(
            initial_cash=float(initial_cash),
            strategy_id=str(strategy_id),
            start_date=str(start_date),
            end_date=str(end_date),
            trades=list(trades),
            account_snapshots=list(snapshots),
            position_snapshots=list(position_snapshots or []),
            commission_model=commission_model,
            total_commission=last_comm,
        )


def _parse_metric_date(value: str) -> Optional[date]:
    if not value:
        return None
    text = str(value).strip()[:10]
    for fmt in ('%Y-%m-%d', '%Y%m%d'):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _annualized_return(
    total_return: float,
    trading_days: int,
    start_date: str = '',
    end_date: str = '',
) -> float:
    """年化收益率：有回测区间日期时用日历 CAGR，否则按 252 交易日复利年化。"""
    start = _parse_metric_date(start_date)
    end = _parse_metric_date(end_date)
    if start and end and end > start:
        years = (end - start).days / 365.25
        if years > 0:
            return (1 + total_return) ** (1 / years) - 1
    if trading_days <= 0:
        return 0.0
    return (1 + total_return) ** (252 / trading_days) - 1


def _equity_path_for_drawdown(equity_curve: List[float], initial_cash: float) -> List[float]:
    """回撤路径：若首日收盘权益≠初始资金，在曲线前补初始资金点。"""
    if not equity_curve:
        return []
    initial = float(initial_cash)
    if initial <= 0:
        return list(equity_curve)
    first = float(equity_curve[0])
    if abs(first - initial) / max(initial, 1.0) > 1e-9:
        return [initial] + list(equity_curve)
    return list(equity_curve)


def _max_drawdown(equity_curve: List[float]) -> tuple[float, int]:
    if not equity_curve:
        return 0.0, 0
    peak = equity_curve[0]
    max_dd = 0.0
    max_dd_duration = 0
    current_dd_duration = 0
    in_drawdown = False
    for value in equity_curve:
        if value > peak:
            peak = value
            in_drawdown = False
            current_dd_duration = 0
        else:
            drawdown = (peak - value) / peak if peak > 0 else 0.0
            max_dd = max(max_dd, drawdown)
            if not in_drawdown:
                in_drawdown = True
                current_dd_duration = 1
            else:
                current_dd_duration += 1
            max_dd_duration = max(max_dd_duration, current_dd_duration)
    return max_dd, max_dd_duration


def _drawdown_curve(equity_curve: List[float]) -> List[float]:
    """返回逐点回撤序列（0.0~1.0 小数），0.0 表示新高，0.1 表示回撤 10%。"""
    if not equity_curve:
        return []
    peak = equity_curve[0]
    drawdowns: List[float] = []
    for value in equity_curve:
        if value > peak:
            peak = value
        dd = (peak - value) / peak if peak > 0 else 0.0
        drawdowns.append(dd)
    return drawdowns


def _daily_returns_from_equity(
    equity_curve: List[float],
    initial_cash: float,
) -> List[float]:
    """由权益曲线逐日推导日收益率（与 AccountManager.settle 口径一致）。"""
    if not equity_curve:
        return []
    prev = float(initial_cash)
    returns: List[float] = []
    for value in equity_curve:
        tv = float(value)
        returns.append((tv - prev) / prev if prev > 0 else 0.0)
        prev = tv
    return returns


def _resolve_daily_returns(
    equity_curve: List[float],
    stored_returns: List[float],
    initial_cash: float,
) -> List[float]:
    """优先用快照中的 daily_return；若未写入（全为 0）则从权益曲线重算。"""
    if not equity_curve:
        return []
    if stored_returns and sum(abs(r) for r in stored_returns) > 1e-12:
        return stored_returns
    if len(equity_curve) >= 2 and (max(equity_curve) - min(equity_curve)) > 1e-6:
        return _daily_returns_from_equity(equity_curve, initial_cash)
    return stored_returns or []


def _sharpe_ratio(daily_returns: List[float], risk_free_rate: float = 0.03) -> float:
    if not daily_returns or len(daily_returns) < 2:
        return 0.0
    rets = np.array(daily_returns, dtype=float)
    if np.std(rets, ddof=1) == 0:
        return 0.0
    excess = rets - risk_free_rate / 252
    if np.std(excess, ddof=1) == 0:
        return 0.0
    return float(np.mean(excess) / np.std(excess, ddof=1) * np.sqrt(252))


def _extract_round_trip_pnls(detail_df: pd.DataFrame) -> tuple[List[float], List[float]]:
    """从交易明细提取已 FIFO 配对的平仓盈亏（仅统计 pnl 非空的卖出）。"""
    gross_pnls: List[float] = []
    net_pnls: List[float] = []
    if detail_df.empty or 'action' not in detail_df.columns:
        return gross_pnls, net_pnls
    sells = detail_df[detail_df['action'] == '卖出']
    if sells.empty:
        return gross_pnls, net_pnls
    matched = sells[sells['pnl'].notna()] if 'pnl' in sells.columns else sells
    if matched.empty:
        return gross_pnls, net_pnls
    if 'pnl_gross' in matched.columns:
        gross_pnls = matched['pnl_gross'].astype(float).tolist()
    if 'pnl' in matched.columns:
        net_pnls = matched['pnl'].astype(float).tolist()
    return gross_pnls, net_pnls


def _round_trip_stats(trade_pnls: List[float]) -> tuple[float, float, float, float, int, int, int]:
    if not trade_pnls:
        return 0.0, 0.0, 0.0, 0.0, 0, 0, 0
    winning = [p for p in trade_pnls if p > 0]
    losing = [p for p in trade_pnls if p < 0]
    win_rate = len(winning) / len(trade_pnls)
    avg_win = float(np.mean(winning)) if winning else 0.0
    avg_loss = abs(float(np.mean(losing))) if losing else 0.0
    pl_ratio = avg_win / avg_loss if avg_loss > 0 else (float('inf') if avg_win > 0 else 0.0)
    return win_rate, pl_ratio, avg_win, avg_loss, len(trade_pnls), len(winning), len(losing)


def _profit_factor(trade_pnls: List[float]) -> float:
    win_total = sum(p for p in trade_pnls if p > 0)
    loss_total = abs(sum(p for p in trade_pnls if p < 0))
    if loss_total <= 0:
        return float('inf') if win_total > 0 else float('nan')
    return win_total / loss_total


def _fmt_pct(value: float) -> str:
    return f'{value:.2%}'


def _fmt_ratio(x: float) -> str:
    if math.isnan(x):
        return 'N/A'
    if math.isinf(x):
        return '∞' if x > 0 else 'N/A'
    return f'{x:.4f}'


def compute_performance_metrics(ctx: PerformanceContext) -> Dict[str, Any]:
    """
    计算统一绩效指标字典（回测摘要 / 实盘监控 / Excel 摘要 sheet 共用）。

    返回键的中文名见模块级 ``METRIC_LABELS``。
    """
    equity_curve: List[float] = []
    daily_returns: List[float] = []
    trading_days = 0

    if ctx.account_snapshots:
        equity_curve = [float(s.total_value) for s in ctx.account_snapshots]
        stored_returns = [float(getattr(s, 'daily_return', 0) or 0) for s in ctx.account_snapshots]
        daily_returns = _resolve_daily_returns(
            equity_curve, stored_returns, float(ctx.initial_cash),
        )
        trading_days = len(equity_curve)
    elif ctx.equity_series is not None and not ctx.equity_series.empty:
        s = ctx.equity_series.sort_index()
        s = s[~s.index.duplicated(keep='last')].astype(float)
        equity_curve = s.tolist()
        if len(equity_curve) >= 2:
            daily_returns = [
                equity_curve[i] / equity_curve[i - 1] - 1.0
                for i in range(1, len(equity_curve))
            ]
        else:
            daily_returns = []
        trading_days = len(equity_curve)

    if not equity_curve:
        return {}

    final_value = equity_curve[-1]
    initial = float(ctx.initial_cash)
    if initial > 0:
        total_return = (final_value - initial) / initial
    elif len(equity_curve) >= 2 and equity_curve[0] > 0:
        initial = float(equity_curve[0])
        total_return = final_value / initial - 1.0
    else:
        total_return = 0.0
    total_pnl = final_value - initial
    annual_return = _annualized_return(
        total_return, trading_days, ctx.start_date, ctx.end_date,
    )
    dd_curve = _equity_path_for_drawdown(equity_curve, float(ctx.initial_cash))
    max_dd, max_dd_duration = _max_drawdown(dd_curve)
    sharpe = _sharpe_ratio(daily_returns)
    volatility = (
        float(np.std(daily_returns, ddof=1) * np.sqrt(252)) if len(daily_returns) >= 2 else 0.0
    )
    daily_win_rate = (
        sum(1 for r in daily_returns if r > 0) / len(daily_returns) if daily_returns else 0.0
    )

    detail_df = build_trade_detail_df(ctx.trades, float(ctx.initial_cash), ctx.commission_model)
    gross_pnls, net_pnls = _extract_round_trip_pnls(detail_df)

    wr_g, plr_g, aw_g, al_g, closed_g, won_g, lost_g = _round_trip_stats(gross_pnls)
    if net_pnls:
        wr, plr, aw, al, closed, won, lost = _round_trip_stats(net_pnls)
    else:
        wr, plr, aw, al, closed, won, lost = wr_g, plr_g, aw_g, al_g, closed_g, won_g, lost_g
    pf_gross = _profit_factor(gross_pnls)
    pf_net = _profit_factor(net_pnls) if net_pnls else float('nan')

    avg_pos = 0.0
    if ctx.position_snapshots:
        avg_pos = sum(len(s.positions) for s in ctx.position_snapshots) / len(ctx.position_snapshots)

    total_commission = ctx.total_commission
    if total_commission is None and not detail_df.empty and 'commission' in detail_df.columns:
        total_commission = float(detail_df['commission'].sum())

    return {
        # —— 元信息 ——
        'strategy_id': ctx.strategy_id,  # 策略标识
        'start_date': ctx.start_date,  # 回测开始日期
        'end_date': ctx.end_date,  # 回测结束日期
        'trading_days': trading_days,  # 交易日数
        # —— 组合收益 ——
        'initial_cash': initial,  # 初始资金
        'final_value': final_value,  # 期末总资产（现金 + 持仓市值）
        'total_return': total_return,  # 总收益率（小数，如 0.1 = 10%）
        'total_return_pct': _fmt_pct(total_return),  # 总收益率（格式化）
        'total_pnl': total_pnl,  # 总盈亏（金额，含浮盈浮亏）
        'annual_return': annual_return,  # 年化收益率（小数）
        'annual_return_pct': _fmt_pct(annual_return),  # 年化收益率（格式化）
        # —— 组合风险 ——
        'max_drawdown': max_dd,  # 最大回撤（小数）
        'max_drawdown_pct': _fmt_pct(max_dd),  # 最大回撤（格式化）
        'max_drawdown_duration': max_dd_duration,  # 最大回撤持续天数
        'sharpe_ratio': sharpe,  # 夏普比率（年化，无风险利率 3%）
        'volatility': volatility,  # 年化波动率（小数）
        'volatility_pct': _fmt_pct(volatility),  # 年化波动率（格式化）
        'daily_win_rate': daily_win_rate,  # 日胜率（盈利日占比）
        'daily_win_rate_pct': _fmt_pct(daily_win_rate),  # 日胜率（格式化）
        # —— 成交统计 ——
        'total_trades': len(ctx.trades),  # 总成交笔数（买 + 卖）
        'closed_trades': closed,  # 已平仓笔数（FIFO 配对的卖出）
        'winning_trades': won,  # 盈利平仓笔数
        'losing_trades': lost,  # 亏损平仓笔数
        'total_commission': float(total_commission or 0.0),  # 累计手续费
        'avg_position_count': avg_pos,  # 平均持仓标的数
        # —— 平仓 round-trip · 毛盈亏（不含手续费）——
        'win_rate_gross': wr_g,  # 胜率（毛）
        'win_rate_gross_pct': _fmt_pct(wr_g),  # 胜率（毛，格式化）
        'profit_loss_ratio_gross': plr_g if math.isfinite(plr_g) else float('inf'),  # 盈亏比（毛）
        'profit_loss_ratio_gross_fmt': _fmt_ratio(plr_g),  # 盈亏比（毛，格式化）
        'avg_win_gross': aw_g,  # 平均盈利（毛）
        'avg_loss_gross': al_g,  # 平均亏损（毛，正数）
        'profit_factor_gross': pf_gross if math.isfinite(pf_gross) else float('inf'),  # 盈利因子（毛）
        'profit_factor_gross_fmt': _fmt_ratio(pf_gross),  # 盈利因子（毛，格式化）
        # —— 平仓 round-trip · 净盈亏（已扣双边手续费）——
        'win_rate': wr,  # 胜率（净）
        'win_rate_pct': _fmt_pct(wr),  # 胜率（净，格式化）
        'profit_loss_ratio': plr if math.isfinite(plr) else float('inf'),  # 盈亏比（净）
        'profit_loss_ratio_fmt': _fmt_ratio(plr),  # 盈亏比（净，格式化）
        'avg_win': aw,  # 平均盈利（净）
        'avg_loss': al,  # 平均亏损（净，正数）
        'profit_factor': pf_net if math.isfinite(pf_net) else float('inf'),  # 盈利因子（净）
        'profit_factor_fmt': _fmt_ratio(pf_net),  # 盈利因子（净，格式化）
        # 净指标别名（与 win_rate / profit_loss_ratio 等同）
        'win_rate_net': wr,  # 胜率（净）
        'win_rate_net_pct': _fmt_pct(wr),  # 胜率（净，格式化）
        'profit_loss_ratio_net': plr if math.isfinite(plr) else float('inf'),  # 盈亏比（净）
        'profit_loss_ratio_net_fmt': _fmt_ratio(plr),  # 盈亏比（净，格式化）
        'avg_win_net': aw,  # 平均盈利（净）
        'avg_loss_net': al,  # 平均亏损（净，正数）
        'profit_factor_net': pf_net if math.isfinite(pf_net) else float('inf'),  # 盈利因子（净）
        'profit_factor_net_fmt': _fmt_ratio(pf_net),  # 盈利因子（净，格式化）
    }
