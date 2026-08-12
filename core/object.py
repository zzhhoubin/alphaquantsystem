"""
标准数据对象定义

注意：时间字段命名为 event_time，避免与类型 datetime 同名。
在部分 Python 发行版（如 VeighNa Studio）中，字段名 datetime 会导致 dataclass 处理注解时递归错误。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

class Direction(str, Enum):
    LONG = 'long'
    SHORT = 'short'

class OrderType(str, Enum):
    LIMIT = 'limit'
    MARKET = 'market'

class OrderStatus(str, Enum):
    SUBMITTING = 'submitting'
    NOTTRADED = 'nottraded'
    PARTTRADED = 'parttraded'
    ALLTRADED = 'alltraded'
    CANCELLED = 'cancelled'
    REJECTED = 'rejected'

@dataclass
class TickData:
    symbol: str
    last_price: float
    volume: float
    amount: float
    open_price: float
    high_price: float
    low_price: float
    pre_close: float
    event_time: datetime = field(default_factory=datetime.now)
    limit_up: float = 0.0
    limit_down: float = 0.0

    @property
    def datetime(self) -> datetime:
        return self.event_time

@dataclass
class BarData:
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float = 0.0
    event_time: datetime = field(default_factory=datetime.now)
    interval: str = '1d'

    @property
    def datetime(self) -> datetime:
        return self.event_time

@dataclass
class SignalData:
    strategy_id: str
    symbol: str
    direction: Direction
    volume: float
    price: float = 0.0
    event_time: datetime = field(default_factory=datetime.now)
    signal_id: str = ''
    tag: Optional[str] = None
    reason: str = ''
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def datetime(self) -> datetime:
        return self.event_time

@dataclass
class OrderData:
    order_id: str
    strategy_id: str
    symbol: str
    direction: Direction
    order_type: OrderType
    volume: float
    price: float
    status: OrderStatus = OrderStatus.SUBMITTING
    traded: float = 0.0
    event_time: datetime = field(default_factory=datetime.now)
    signal_id: str = ''
    tag: Optional[str] = None
    order_remark: str = ''

    @property
    def datetime(self) -> datetime:
        return self.event_time

@dataclass
class TradeData:
    trade_id: str
    order_id: str
    strategy_id: str
    symbol: str
    direction: Direction
    price: float
    volume: float
    event_time: datetime = field(default_factory=datetime.now)
    signal_id: str = ''
    tag: Optional[str] = None

    @property
    def datetime(self) -> datetime:
        return self.event_time

@dataclass
class PositionData:
    symbol: str
    direction: Direction
    volume: float
    frozen: float = 0.0
    price: float = 0.0
    pnl: float = 0.0
    event_time: datetime = field(default_factory=datetime.now)

    @property
    def datetime(self) -> datetime:
        return self.event_time

@dataclass
class AccountData:
    account_id: str
    balance: float
    frozen: float
    available: float
    event_time: datetime = field(default_factory=datetime.now)

    @property
    def datetime(self) -> datetime:
        return self.event_time

@dataclass
class LogData:
    msg: str
    level: str = 'INFO'
    event_time: datetime = field(default_factory=datetime.now)

    @property
    def datetime(self) -> datetime:
        return self.event_time

@dataclass
class ScheduleEvent:
    """Live trading scheduled event"""
    time: str  # Format: "HH:MM" (24-hour)
    handler_name: str
    event_time: datetime = field(default_factory=datetime.now)

    @property
    def datetime(self) -> datetime:
        return self.event_time

@dataclass
class AccountSnapshot:
    """Daily account snapshot"""
    date: datetime
    balance: float = 0.0
    market_value: float = 0.0
    total_value: float = 0.0
    daily_return: float = 0.0
    cumulative_return: float = 0.0
    daily_pnl: float = 0.0
    cumulative_pnl: float = 0.0
    available_cash: float = 0.0

    @property
    def datetime(self) -> datetime:
        return self.date if isinstance(self.date, datetime) else datetime.now()
