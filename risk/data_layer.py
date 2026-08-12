"""
数据接入层（Layer 1 / 6）

职责：统一行情、资金、持仓、委托数据接入、清洗、时序对齐、字段标准化。

差异化：
  - 实盘：接收交易所 Tick、K线、持仓推送、资金推送、委托状态推送
  - 回测：接收历史回放 Tick/K线、模拟持仓、模拟资金快照

输出：RiskSnapshot（统一结构化快照，供计算层消费）
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from loguru import logger

from alphaQuantSystem.core import (
    AccountData, BarData, Direction, PositionData, TickData,
)


@dataclass
class RiskSnapshot:
    """标准化风控快照 —— 所有数据字段归一化，供计算层无差别消费

    约束：所有字段带确定的时间戳，支持回放、复盘追溯。
    """
    symbol: str
    strategy_id: str
    timestamp: datetime
    scene: str                                          # 'live' | 'backtest'

    # ---- 价格数据 ----
    last_price: float = 0.0
    open_price: float = 0.0
    high_price: float = 0.0
    low_price: float = 0.0
    pre_close: float = 0.0
    limit_up: float = 0.0
    limit_down: float = 0.0
    volume: float = 0.0
    amount: float = 0.0

    # ---- 持仓数据 ----
    position_volume: float = 0.0
    position_price: float = 0.0           # 开仓均价
    position_pnl: float = 0.0             # 持仓浮动盈亏
    position_value: float = 0.0           # 持仓市值
    position_direction: str = "none"      # 'long' | 'short' | 'none'

    # ---- 账户数据 ----
    balance: float = 0.0
    available: float = 0.0
    frozen: float = 0.0

    @property
    def total_equity(self) -> float:
        """总权益。balance 字段已是账户总资产（含持仓市值），直接返回。"""
        return self.balance

    @property
    def has_position(self) -> bool:
        return self.position_volume > 0 and self.position_direction != "none"


class DataLayer:
    """数据接入层：聚合行情、持仓、账户数据，输出标准化 RiskSnapshot。

    实盘模式下通过 update_* 方法实时更新；
    回测模式下通过回放历史数据逐帧更新。
    """

    def __init__(self, scene: str = "live"):
        self.scene = scene
        # 回测 Bar 链：上一根收盘价作为昨收（供当日跌幅等指标）
        self._prev_closes: Dict[str, float] = {}
        # 持仓缓存: key = f"{symbol}:{direction}"
        self._positions: Dict[str, PositionData] = {}
        # 账户缓存: key = account_id
        self._accounts: Dict[str, AccountData] = {}
        # 行情缓存
        self._ticks: Dict[str, TickData] = {}
        self._bars: Dict[str, BarData] = {}
        # 最新快照缓存: key = symbol
        self._snapshots: Dict[str, RiskSnapshot] = {}

    # ---- 数据更新 ----

    def update_tick(self, tick: TickData):
        """更新Tick行情"""
        self._ticks[tick.symbol] = tick
        self._bars.pop(tick.symbol, None)

    def update_bar(self, bar: BarData):
        """更新Bar行情"""
        prev = self._bars.get(bar.symbol)
        if prev is not None and prev.close > 0:
            self._prev_closes[bar.symbol] = prev.close
        self._bars[bar.symbol] = bar

    def update_position(self, pos: PositionData):
        """更新持仓数据"""
        key = f"{pos.symbol}:{pos.direction.value}"
        self._positions[key] = pos

    def update_positions_batch(self, positions: List[PositionData]):
        """批量更新持仓"""
        for pos in positions:
            self.update_position(pos)

    def update_account(self, acc: AccountData):
        """更新账户数据"""
        self._accounts[acc.account_id] = acc

    def clear_positions(self):
        """清空持仓（回测换日/重置时调用）"""
        self._positions.clear()

    def clear_symbol_positions(self, symbol: str) -> None:
        """清除指定标的的持仓缓存（平仓后调用，避免幽灵持仓误触发风控）。"""
        if not symbol:
            return
        prefix = f"{symbol}:"
        for key in list(self._positions):
            if key.startswith(prefix):
                del self._positions[key]

    # ---- 快照生成 ----

    def snapshot(self, symbol: str, strategy_id: str = "") -> RiskSnapshot:
        """生成指定标的的标准化风控快照

        聚合最新行情、持仓、账户数据，所有字段带时间戳。
        若某类数据缺失，对应字段为默认值 0.0。
        """
        now = datetime.now()

        # 行情数据
        tick = self._ticks.get(symbol)
        bar = self._bars.get(symbol)

        if tick is not None:
            last_price = tick.last_price
            open_price = tick.open_price
            high_price = tick.high_price
            low_price = tick.low_price
            pre_close = tick.pre_close
            limit_up = tick.limit_up
            limit_down = tick.limit_down
            volume = tick.volume
            amount = tick.amount
            ts = tick.event_time
        elif bar is not None:
            last_price = bar.close
            open_price = bar.open
            high_price = bar.high
            low_price = bar.low
            pre_close = self._prev_closes.get(symbol, 0.0)
            limit_up = 0.0
            limit_down = 0.0
            volume = bar.volume
            amount = bar.amount
            ts = bar.event_time
        else:
            last_price = 0.0
            open_price = 0.0
            high_price = 0.0
            low_price = 0.0
            pre_close = 0.0
            limit_up = 0.0
            limit_down = 0.0
            volume = 0.0
            amount = 0.0
            ts = now

        # 持仓数据：聚合该标的的 long + short 持仓
        pos_volume = 0.0
        pos_price = 0.0
        pos_pnl = 0.0
        pos_value = 0.0
        pos_direction = "none"
        for key, pos in self._positions.items():
            if pos.symbol == symbol:
                pos_volume += pos.volume
                pos_pnl += pos.pnl
                pos_value += pos.volume * pos.price if pos.price else 0.0
                if pos.direction == Direction.LONG:
                    pos_direction = "long"
                    pos_price = pos.price if pos.price else pos_price
                elif pos.direction == Direction.SHORT:
                    pos_direction = "short"
                    pos_price = pos.price if pos.price else pos_price

        # 账户数据：取第一个可用账户
        accounts = list(self._accounts.values())
        if accounts:
            acc = accounts[0]
            balance = acc.balance
            available = acc.available
            frozen = acc.frozen
        else:
            balance = 0.0
            available = 0.0
            frozen = 0.0

        snapshot = RiskSnapshot(
            symbol=symbol,
            strategy_id=strategy_id,
            timestamp=ts,
            scene=self.scene,
            last_price=last_price,
            open_price=open_price,
            high_price=high_price,
            low_price=low_price,
            pre_close=pre_close,
            limit_up=limit_up,
            limit_down=limit_down,
            volume=volume,
            amount=amount,
            position_volume=pos_volume,
            position_price=pos_price,
            position_pnl=pos_pnl,
            position_value=pos_value,
            position_direction=pos_direction,
            balance=balance,
            available=available,
            frozen=frozen,
        )
        self._snapshots[symbol] = snapshot
        return snapshot

    def get_cached_snapshot(self, symbol: str) -> Optional[RiskSnapshot]:
        """获取最近一次快照缓存"""
        return self._snapshots.get(symbol)
