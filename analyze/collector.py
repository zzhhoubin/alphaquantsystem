"""
LivePerformanceCollector —— 实盘绩效数据采集器。

在实盘运行期间累积账户快照、成交记录与持仓快照，
最终产出 PerformanceContext，统一对接 compute_performance_metrics()。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from loguru import logger

from alphaQuantSystem.analyze.metrics import PerformanceContext, compute_performance_metrics
from alphaQuantSystem.backtest.account_manager import AccountManager, AccountSnapshot
from alphaQuantSystem.core import Event, EventType, TradeData
from alphaQuantSystem.core.event_engine import EventEngine
from alphaQuantSystem.trader.position_manager import PositionManager

if TYPE_CHECKING:
    from alphaQuantSystem.backtest.commission import CommissionModel


class LivePerformanceCollector:
    """实盘绩效采集器。

    镜像 BacktestEngine._daily_settle() 模式：
      1. 从 PositionManager 获取市值
      2. 调用 account_manager.settle(current_dt)
      3. 调用 position_manager.snapshot(current_dt)

    同时订阅 TRADE 事件累积成交记录、订阅 TIMER 事件触发日终结算。
    """

    def __init__(
        self,
        event_engine: EventEngine,
        initial_cash: float,
        strategy_id: str = 'live',
        commission_model: Optional['CommissionModel'] = None,
    ):
        self.event_engine = event_engine
        self.strategy_id = strategy_id
        self.initial_cash = float(initial_cash)
        self.commission_model = commission_model

        # 独立的 AccountManager / PositionManager，不影响策略层
        self.account_manager = AccountManager(
            event_engine=event_engine,
            initial_cash=initial_cash,
            account_id=f'{strategy_id}_live',
        )
        self.position_manager = PositionManager(
            initial_capital=initial_cash,
        )

        # 成交累积
        self._trades: List[TradeData] = []

        # 会话状态
        self._start_date: str = ''
        self._last_settle_date: Optional[datetime] = None

        # 订阅事件
        self.event_engine.subscribe(EventType.TRADE, self._on_trade)
        self.event_engine.subscribe(EventType.TIMER, self._on_timer)

        logger.info(
            f'[LivePerformanceCollector] 初始化完成 '
            f'strategy_id={strategy_id} initial_cash={initial_cash:,.2f}'
        )

    # ------------------------------------------------------------------
    # 事件处理
    # ------------------------------------------------------------------

    def _on_trade(self, event: Event) -> None:
        """TRADE 事件：更新仓位 + 扣佣金 + 累积记录。

        AccountManager 已自行订阅 TRADE 并更新现金余额；
        此处仅负责 PositionManager 更新与佣金扣减。
        """
        trade: TradeData = event.data

        # 估算佣金
        commission = 0.0
        if self.commission_model is not None:
            try:
                commission = float(self.commission_model.calculate(trade))
            except Exception:
                commission = 0.0

        # 更新仓位（含已实现盈亏 + 佣金）
        self.position_manager.on_trade(
            symbol=trade.symbol,
            direction=trade.direction,
            volume=trade.volume,
            price=trade.price,
            commission=commission,
        )

        # AccountManager.on_trade 已在订阅链中自动扣减交易金额；
        # 此处补扣佣金（AccountManager 不自行计算佣金）
        if commission > 0:
            self.account_manager.add_commission(commission)

        # 累积成交记录
        self._trades.append(trade)

    def _on_timer(self, event: Event) -> None:
        """TIMER 事件：收盘后（15:00）触发日终结算（幂等）。"""
        now = datetime.now()
        if now.hour < 15:
            return
        if (
            self._last_settle_date is not None
            and self._last_settle_date.date() == now.date()
        ):
            return
        self.settle(now)

    # ------------------------------------------------------------------
    # 日终结算
    # ------------------------------------------------------------------

    def settle(self, current_dt: datetime) -> None:
        """日终结算（镜像 BacktestEngine._daily_settle）。"""
        if self._last_settle_date is not None:
            if self._last_settle_date.date() == current_dt.date():
                return

        if not self._start_date:
            self._start_date = current_dt.strftime('%Y-%m-%d')

        # 1. 更新持仓市价 → AccountManager
        market_value = self.position_manager.total_market_value
        self.account_manager.update_market_value(market_value)

        # 2. 账户结算（创建 AccountSnapshot）
        self.account_manager.settle(current_dt)

        # 3. 持仓快照（创建 PositionSnapshot）
        self.position_manager.snapshot(current_dt)

        self._last_settle_date = current_dt

        logger.debug(
            f'[LivePerformanceCollector] 日终结算 {current_dt.date()} '
            f'total_value={self.account_manager.get_total_value():,.2f} '
            f'snapshots={len(self.account_manager.snapshots)}'
        )

    # ------------------------------------------------------------------
    # 绩效输出
    # ------------------------------------------------------------------

    def get_performance_context(self) -> PerformanceContext:
        """从累积数据构建 PerformanceContext。"""
        return PerformanceContext.from_live_data(
            snapshots=self.account_manager.get_snapshots(),
            trades=list(self._trades),
            initial_cash=self.initial_cash,
            strategy_id=self.strategy_id,
            start_date=self._start_date,
            end_date=(
                self._last_settle_date.strftime('%Y-%m-%d')
                if self._last_settle_date
                else ''
            ),
            position_snapshots=self.position_manager.get_snapshots(),
            commission_model=self.commission_model,
        )

    def get_summary(self) -> Dict[str, Any]:
        """便捷方法：计算当前绩效指标。无快照时返回空 dict。"""
        ctx = self.get_performance_context()
        if not ctx.account_snapshots:
            logger.warning('[LivePerformanceCollector] 无结算数据，无法计算绩效指标')
            return {}
        return compute_performance_metrics(ctx)

    # ------------------------------------------------------------------
    # 访问器
    # ------------------------------------------------------------------

    def get_snapshots(self) -> List[AccountSnapshot]:
        """返回全部账户快照。"""
        return self.account_manager.get_snapshots()

    def get_trades(self) -> List[TradeData]:
        """返回全部成交记录。"""
        return list(self._trades)

    def get_total_value(self) -> float:
        """返回当前总资产。"""
        return self.account_manager.get_total_value()

    def get_statistics(self) -> Dict[str, Any]:
        """返回当前账户统计。"""
        return self.account_manager.get_statistics()
