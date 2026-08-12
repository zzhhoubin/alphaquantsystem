"""
资金管理器 - 回测账户资金管理
负责资金账户的更新、结算和统计
"""
from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict

from loguru import logger

from alphaQuantSystem.core import (
    Event, EventEngine, EventType,
    TradeData, AccountData, Direction
)


@dataclass
class AccountSnapshot:
    """账户快照（用于记录每日账户状态）"""
    date: datetime
    balance: float  # 现金余额
    available: float  # 可用资金
    frozen: float  # 冻结资金
    market_value: float  # 持仓市值
    total_value: float  # 总资产
    commission: float  # 累计手续费
    daily_pnl: float  # 当日盈亏
    cumulative_pnl: float  # 累计盈亏
    daily_return: float  # 当日收益率
    cumulative_return: float  # 累计收益率


class AccountManager:
    """
    资金管理器
    职责：
    - 监听 TRADE 事件更新资金
    - 日终结算账户状态
    - 发送 ACCOUNT 事件
    - 记录账户历史快照
    """

    def __init__(self,
                 event_engine: EventEngine,
                 initial_cash: float,
                 account_id: str = 'backtest_account'):
        """
        参数:
            event_engine: 事件引擎
            initial_cash: 初始资金
            account_id: 账户ID
        """
        self.event_engine = event_engine
        self.account_id = account_id
        self.initial_cash = initial_cash

        # 账户状态
        self.balance = initial_cash  # 现金余额
        self.available = initial_cash  # 可用资金
        self.frozen = 0.0  # 冻结资金
        self.total_commission = 0.0  # 累计手续费

        # 持仓市值（由外部传入）
        self.market_value = 0.0

        # 历史快照
        self.snapshots: List[AccountSnapshot] = []
        self.daily_values: List[float] = [initial_cash]  # 每日总资产

        # 前一日总资产（用于计算当日盈亏）
        self.prev_total_value = initial_cash

        # 订阅事件
        self.event_engine.subscribe(EventType.TRADE, self.on_trade)

        logger.info(f'[AccountManager] 资金管理器初始化完成，初始资金={initial_cash:.2f}')

    def on_trade(self, event: Event):
        """
        处理成交事件，更新资金
        调用关系：由 EventEngine 在收到 TRADE 事件时回调
        """
        trade: TradeData = event.data

        trade_value = trade.volume * trade.price

        if trade.direction == Direction.LONG:
            # 买入：扣减资金
            self.balance -= trade_value
            self.available -= trade_value
            logger.debug(f'[AccountManager] 买入 {trade.symbol} 扣款={trade_value:.2f} 余额={self.balance:.2f}')
        else:
            # 卖出：增加资金
            self.balance += trade_value
            self.available += trade_value
            logger.debug(f'[AccountManager] 卖出 {trade.symbol} 入账={trade_value:.2f} 余额={self.balance:.2f}')

        # 扣减手续费（由 MatchingEngine 计算后外部调用 add_commission）
        # 这里不重复计算

    def add_commission(self, commission: float):
        """
        添加手续费（由撮合引擎调用）
        调用关系：由 MatchingEngine 在成交后调用

        参数:
            commission: 手续费金额
        """
        self.balance -= commission
        self.available -= commission
        self.total_commission += commission
        logger.debug(f'[AccountManager] 扣除手续费={commission:.2f} 累计={self.total_commission:.2f}')

    def update_market_value(self, market_value: float):
        """
        更新持仓市值（由外部调用）
        调用关系：由 BacktestEngine 在每个Bar更新后调用

        参数:
            market_value: 当前持仓总市值
        """
        self.market_value = market_value

    def settle(self, current_dt: datetime):
        """
        日终结算
        调用关系：由 BacktestEngine 在每日结束时调用

        参数:
            current_dt: 当前日期
        """
        # 计算总资产
        total_value = self.balance + self.market_value

        # 计算当日盈亏
        daily_pnl = total_value - self.prev_total_value
        daily_return = daily_pnl / self.prev_total_value if self.prev_total_value > 0 else 0.0

        # 计算累计盈亏
        cumulative_pnl = total_value - self.initial_cash
        cumulative_return = cumulative_pnl / self.initial_cash if self.initial_cash > 0 else 0.0

        # 创建快照
        snapshot = AccountSnapshot(
            date=current_dt,
            balance=self.balance,
            available=self.available,
            frozen=self.frozen,
            market_value=self.market_value,
            total_value=total_value,
            commission=self.total_commission,
            daily_pnl=daily_pnl,
            cumulative_pnl=cumulative_pnl,
            daily_return=daily_return,
            cumulative_return=cumulative_return
        )

        self.snapshots.append(snapshot)
        self.daily_values.append(total_value)

        # 更新前一日总资产
        self.prev_total_value = total_value

        # 发送账户事件
        account_data = AccountData(
            account_id=self.account_id,
            balance=self.balance,
            frozen=self.frozen,
            available=self.available,
            event_time=current_dt
        )
        self.event_engine.put(Event(EventType.ACCOUNT, account_data))

        logger.debug(
            f'[AccountManager] 日终结算 {current_dt.date()} '
            f'总资产={total_value:.2f} 现金={self.balance:.2f} '
            f'市值={self.market_value:.2f} 日盈亏={daily_pnl:.2f} '
            f'累计盈亏={cumulative_pnl:.2f}'
        )

    def get_account_data(self) -> AccountData:
        """
        获取当前账户数据
        返回:
            AccountData 对象
        """
        return AccountData(
            account_id=self.account_id,
            balance=self.balance,
            frozen=self.frozen,
            available=self.available,
            event_time=datetime.now()
        )

    def get_total_value(self) -> float:
        """获取当前总资产"""
        return self.balance + self.market_value

    def get_statistics(self) -> Dict:
        """
        获取账户统计信息
        返回:
            统计数据字典
        """
        total_value = self.get_total_value()
        total_return = (total_value - self.initial_cash) / self.initial_cash if self.initial_cash > 0 else 0.0

        return {
            'initial_cash': self.initial_cash,
            'balance': self.balance,
            'market_value': self.market_value,
            'total_value': total_value,
            'total_commission': self.total_commission,
            'total_pnl': total_value - self.initial_cash,
            'total_return': total_return,
            'snapshots_count': len(self.snapshots)
        }

    def get_equity_curve(self) -> List[float]:
        """
        获取权益曲线
        返回:
            每日总资产列表
        """
        return self.daily_values.copy()

    def get_snapshots(self) -> List[AccountSnapshot]:
        """
        获取所有账户快照
        返回:
            快照列表
        """
        return self.snapshots.copy()
