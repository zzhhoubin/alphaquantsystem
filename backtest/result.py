"""
回测结果分析器
负责存储回测结果、计算绩效指标、生成报告和图表
"""
from typing import Any, Dict, List, Optional

import pandas as pd
from loguru import logger

from alphaQuantSystem.analyze.metrics import PerformanceContext, compute_performance_metrics
from alphaQuantSystem.backtest.account_manager import AccountSnapshot
from alphaQuantSystem.backtest.trade_report import (
    build_trade_detail_df,
    TRADE_DETAIL_FIELD_DOCS,
)
from alphaQuantSystem.core import TradeData
from alphaQuantSystem.trader.position_manager import PositionSnapshot

try:
    from alphaQuantSystem.backtest.commission import CommissionModel
except ImportError:
    CommissionModel = None  # type: ignore


def _detail_df_to_trade_log(detail: pd.DataFrame) -> List[dict]:
    """将框架交易明细转为 runtime 兼容的 trade_log 列表。"""
    if detail is None or detail.empty:
        return []
    rows: List[dict] = []
    for _, r in detail.iterrows():
        is_buy = r.get('action') == '买入'
        dt = r.get('datetime')
        rows.append({
            'datetime': dt.isoformat() if hasattr(dt, 'isoformat') else str(dt),
            'trade_date': dt.date().isoformat() if hasattr(dt, 'date') else str(dt)[:10],
            'symbol': r.get('symbol'),
            'side': 'buy' if is_buy else 'sell',
            'price': float(r.get('price', 0) or 0),
            'volume': float(r.get('volume', 0) or 0),
            'amount': float(r.get('amount', 0) or 0),
            'commission': float(r.get('commission', 0) or 0),
            'pnl': float(r.get('pnl', 0) or 0) if not is_buy else 0.0,
            'available_cash': float(r.get('cash_after', 0) or 0),
            'total_assets': float(r.get('total_value', 0) or 0),
        })
    return rows


class BacktestResult:
    """
    回测结果类
    职责：
    - 存储回测数据（交易、账户、持仓）
    - 计算绩效指标
    - 生成报告和图表
    """

    def __init__(self,
                 start_date: str,
                 end_date: str,
                 initial_cash: float,
                 strategy_id: str = 'backtest',
                 commission_model: Optional['CommissionModel'] = None):
        """
        参数:
            start_date: 回测开始日期
            end_date: 回测结束日期
            initial_cash: 初始资金
            strategy_id: 策略ID
            commission_model: 佣金模型（用于交易明细中的 commission / pnl 等字段）
        """
        self.start_date = start_date
        self.end_date = end_date
        self.initial_cash = initial_cash
        self.strategy_id = strategy_id
        self.commission_model = commission_model

        # 数据存储
        self.trades: List[TradeData] = []
        self.account_snapshots: List[AccountSnapshot] = []
        self.position_snapshots: List[PositionSnapshot] = []

        # 绩效指标（延迟计算）
        self._metrics: Optional[Dict] = None
        self._trade_detail_df: Optional[pd.DataFrame] = None
        self.strategy_records: List[Dict[str, Any]] = []

    def add_trade(self, trade: TradeData):
        """添加交易记录"""
        self.trades.append(trade)

    def set_account_snapshots(self, snapshots: List[AccountSnapshot]):
        """设置账户快照"""
        self.account_snapshots = snapshots

    def set_position_snapshots(self, snapshots: List[PositionSnapshot]):
        """设置持仓快照"""
        self.position_snapshots = snapshots

    def calculate_metrics(self) -> Dict:
        """计算统一绩效指标（见 analyze.metrics）。"""
        if self._metrics is not None:
            return self._metrics

        if not self.account_snapshots:
            logger.warning('[BacktestResult] 无账户快照数据，无法计算指标')
            return {}

        self._metrics = compute_performance_metrics(PerformanceContext.from_backtest_result(self))
        return self._metrics

    def to_summary_dict(
        self,
        *,
        include_equity_curve: bool = False,
        include_trade_log: bool = False,
        include_strategy_records: bool = False,
    ) -> Dict:
        """
        转为 runtime / 策略脚本兼容的 summary dict。
        指标口径与 analyze.metrics 一致。
        """
        metrics = self.calculate_metrics()
        if not metrics:
            return {}

        sharpe = metrics.get('sharpe_ratio')
        summary: Dict = {
            'initial_cash': metrics['initial_cash'],
            'final_value': metrics['final_value'],
            'total_return': metrics['total_return_pct'],
            'total_return_float': metrics['total_return'],
            'sharpe_ratio': round(sharpe, 4) if sharpe else 'N/A',
            'max_drawdown': metrics['max_drawdown_pct'],
            'max_drawdown_float': metrics['max_drawdown'],
            'total_trades': metrics['total_trades'],
            'closed_trades': metrics['closed_trades'],
            'winning_trades': metrics['winning_trades'],
            'losing_trades': metrics['losing_trades'],
            'win_rate': metrics['win_rate_pct'],
            'win_rate_float': metrics['win_rate'],
            'trade_profit_loss_ratio': metrics['profit_loss_ratio_fmt'],
            'trade_profit_loss_ratio_float': (
                metrics['profit_loss_ratio'] if metrics['profit_loss_ratio'] != float('inf') else None
            ),
            'trade_profit_factor': metrics['profit_factor_fmt'],
            'trade_profit_factor_float': (
                metrics['profit_factor'] if metrics['profit_factor'] != float('inf') else None
            ),
            'win_rate_net': metrics['win_rate_net_pct'],
            'win_rate_net_float': metrics['win_rate_net'],
            'trade_profit_loss_ratio_net': metrics['profit_loss_ratio_net_fmt'],
            'total_commission': metrics['total_commission'],
            'annual_return': metrics['annual_return_pct'],
            'annual_return_float': metrics['annual_return'],
            'trading_days': metrics['trading_days'],
            'strategy_id': metrics['strategy_id'],
            'start_date': metrics['start_date'],
            'end_date': metrics['end_date'],
        }

        if include_equity_curve:
            eq_df = self.get_equity_curve_df()
            summary['equity_curve'] = [
                {'date': row['date'].isoformat() if hasattr(row['date'], 'isoformat') else str(row['date']),
                 'total_value': float(row['total_value'])}
                for _, row in eq_df.iterrows()
            ]
            summary['max_drawdown_equity'] = metrics['max_drawdown_pct']
            summary['max_drawdown_equity_float'] = metrics['max_drawdown']

        if include_trade_log:
            detail = self.get_trades_detail_df()
            summary['trade_log'] = _detail_df_to_trade_log(detail)
            summary['trade_log_schema'] = dict(TRADE_DETAIL_FIELD_DOCS)

        if include_strategy_records:
            summary['strategy_records'] = self.strategy_records

        return summary

    def get_trades_df(self) -> pd.DataFrame:
        """
        获取简要交易记录（兼容旧版：不含佣金 / 现金 / 盈亏列）
        """
        if not self.trades:
            return pd.DataFrame()

        records = []
        for trade in self.trades:
            records.append({
                'trade_id': trade.trade_id,
                'datetime': trade.event_time,
                'symbol': trade.symbol,
                'direction': trade.direction.value,
                'price': trade.price,
                'volume': trade.volume,
                'amount': trade.price * trade.volume,
            })

        return pd.DataFrame(records)

    def get_trades_detail_df(self) -> pd.DataFrame:
        """
        获取完整交易明细（含佣金、现金、毛/净盈亏）。
        字段含义见 backtest.trade_report.TRADE_DETAIL_FIELD_DOCS。
        """
        if self._trade_detail_df is None:
            self._trade_detail_df = build_trade_detail_df(
                self.trades, self.initial_cash, self.commission_model
            )
        return self._trade_detail_df.copy()

    @staticmethod
    def get_trade_field_docs_df() -> pd.DataFrame:
        """交易记录字段说明表（用于 Excel 说明 sheet）。"""
        return pd.DataFrame([
            {'field': k, 'description': v}
            for k, v in TRADE_DETAIL_FIELD_DOCS.items()
        ])

    def get_strategy_records_df(self) -> pd.DataFrame:
        """Strategy custom metrics DataFrame (from ctx.record); empty DataFrame if no records"""
        if not self.strategy_records:
            return pd.DataFrame()
        return pd.DataFrame(self.strategy_records)

    def get_equity_curve_df(self) -> pd.DataFrame:
        """
        获取权益曲线DataFrame
        返回:
            权益曲线表
        """
        if not self.account_snapshots:
            return pd.DataFrame()

        records = []
        for snap in self.account_snapshots:
            records.append({
                'date': snap.date,
                'balance': snap.balance,
                'market_value': snap.market_value,
                'total_value': snap.total_value,
                'daily_return': snap.daily_return,
                'cumulative_return': snap.cumulative_return,
                'daily_pnl': snap.daily_pnl,
                'cumulative_pnl': snap.cumulative_pnl
            })

        return pd.DataFrame(records)

    def print_summary(self):
        """打印回测摘要"""
        metrics = self.calculate_metrics()

        print('\n' + '='*60)
        print(f'回测摘要 - {self.strategy_id}')
        print('='*60)
        if not metrics:
            print(f'回测区间: {self.start_date} ~ {self.end_date}')
            print('无有效回测结果（行情数据为空或账户快照缺失），请检查数据源与标的代码。')
            print('='*60)
            return

        print(f'回测区间: {metrics.get("start_date", self.start_date)} ~ {metrics.get("end_date", self.end_date)}')
        print(f'交易日数: {metrics.get("trading_days", 0)}')
        print('\n【收益指标】')
        print(f'初始资金: {metrics["initial_cash"]:,.2f}')
        print(f'最终资金: {metrics["final_value"]:,.2f}')
        print(f'总收益率: {metrics["total_return_pct"]}')
        print(f'年化收益: {metrics["annual_return_pct"]}')
        print(f'总盈亏: {metrics["total_pnl"]:,.2f}')
        print('\n【风险指标】')
        print(f'最大回撤: {metrics["max_drawdown_pct"]}')
        print(f'回撤持续: {metrics.get("max_drawdown_duration", 0)}天')
        print(f'夏普比率: {metrics["sharpe_ratio"]:.2f}')
        print(f'波动率: {metrics["volatility_pct"]}')
        print('\n【交易统计 · 平仓 round-trip（净，已扣双边手续费）】')
        print(f'总成交笔数: {metrics["total_trades"]}')
        print(f'已平仓: {metrics["closed_trades"]} (盈 {metrics["winning_trades"]} / 亏 {metrics["losing_trades"]})')
        print(f'胜率: {metrics["win_rate_pct"]}')
        print(f'盈亏比: {metrics["profit_loss_ratio_fmt"]}')
        print(f'平均盈利: {metrics["avg_win"]:.2f}')
        print(f'平均亏损: {metrics["avg_loss"]:.2f}')
        print(f'Profit Factor: {metrics["profit_factor_fmt"]}')
        if self.commission_model is not None and metrics.get('win_rate_gross_pct'):
            print('\n【参考 · 毛盈亏（不含手续费）】')
            print(f'胜率(毛): {metrics["win_rate_gross_pct"]}')
            print(f'盈亏比(毛): {metrics["profit_loss_ratio_gross_fmt"]}')
            print(f'Profit Factor(毛): {metrics["profit_factor_gross_fmt"]}')
        print(f'总手续费: {metrics["total_commission"]:.2f}')
        print(f'平均持仓数: {metrics.get("avg_position_count", 0):.1f}')
        print('='*60 + '\n')

    def plot(self, save_path: Optional[str] = None):
        """
        绘制回测结果图表
        参数:
            save_path: 图片保存路径（可选）
        """
        try:
            import matplotlib.pyplot as plt
            import matplotlib.dates as mdates
            from matplotlib import rcParams

            # 设置中文字体
            rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei']
            rcParams['axes.unicode_minus'] = False

            if not self.account_snapshots:
                logger.warning('[BacktestResult] 无数据可绘图')
                return

            # 创建图表
            fig, axes = plt.subplots(2, 1, figsize=(14, 10))

            # 1. 权益曲线
            dates = [snap.date for snap in self.account_snapshots]
            equity = [snap.total_value for snap in self.account_snapshots]

            axes[0].plot(dates, equity, label='总资产', linewidth=2, color='#2E86AB')
            axes[0].axhline(y=self.initial_cash, color='gray', linestyle='--', label='初始资金', alpha=0.5)
            axes[0].set_title('权益曲线', fontsize=14, fontweight='bold')
            axes[0].set_ylabel('资产 (元)', fontsize=12)
            axes[0].legend(loc='best')
            axes[0].grid(True, alpha=0.3)
            axes[0].xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))

            # 2. 回撤曲线
            from alphaQuantSystem.analyze.metrics import _drawdown_curve
            drawdowns = [-dd * 100 for dd in _drawdown_curve(equity)]

            axes[1].fill_between(dates, drawdowns, 0, alpha=0.3, color='red', label='回撤')
            axes[1].plot(dates, drawdowns, color='darkred', linewidth=1.5)
            axes[1].set_title('回撤曲线', fontsize=14, fontweight='bold')
            axes[1].set_ylabel('回撤 (%)', fontsize=12)
            axes[1].set_xlabel('日期', fontsize=12)
            axes[1].legend(loc='best')
            axes[1].grid(True, alpha=0.3)
            axes[1].xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))

            plt.tight_layout()

            if save_path:
                plt.savefig(save_path, dpi=300, bbox_inches='tight')
                logger.info(f'[BacktestResult] 图表已保存: {save_path}')

            plt.show()

        except ImportError as e:
            logger.warning(f'[BacktestResult] 缺少绘图库: {e}')
        except Exception as e:
            logger.exception(f'[BacktestResult] 绘图失败: {e}')

    def to_excel(self, filepath: str):
        """
        导出Excel报告
        参数:
            filepath: 文件路径
        """
        try:
            with pd.ExcelWriter(filepath, engine='openpyxl') as writer:
                # 摘要
                metrics = self.calculate_metrics()
                summary_df = pd.DataFrame([metrics])
                summary_df.to_excel(writer, sheet_name='摘要', index=False)

                # 权益曲线
                equity_df = self.get_equity_curve_df()
                if not equity_df.empty:
                    equity_df.to_excel(writer, sheet_name='权益曲线', index=False)

                # 交易记录（有佣金模型时输出完整明细）
                if self.commission_model is not None:
                    trades_df = self.get_trades_detail_df()
                    docs_df = self.get_trade_field_docs_df()
                else:
                    trades_df = self.get_trades_df()
                    docs_df = pd.DataFrame()
                if not trades_df.empty:
                    trades_df.to_excel(writer, sheet_name='交易记录', index=False)
                if not docs_df.empty:
                    docs_df.to_excel(writer, sheet_name='交易记录说明', index=False)

                # 策略自定义指标
                if self.strategy_records:
                    records_df = pd.DataFrame(self.strategy_records)
                    records_df.to_excel(writer, sheet_name='策略指标', index=False)

            logger.info(f'[BacktestResult] Excel报告已保存: {filepath}')

        except Exception as e:
            logger.exception(f'[BacktestResult] 导出Excel失败: {e}')
