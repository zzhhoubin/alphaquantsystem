"""
事件类型定义
"""
from enum import Enum

class EventType(str, Enum):
    TICK = 'tick'
    BAR = 'bar'
    SIGNAL = 'signal'
    ORDER_REQUEST = 'order_request'
    ORDER = 'order'
    TRADE = 'trade'
    POSITION = 'position'
    ACCOUNT = 'account'
    RISK_BLOCK = 'risk_block'
    TIMER = 'timer'
    SCHEDULE = 'schedule'  # 实盘定时任务（见 engine.schedule）
    LOG = 'log'
