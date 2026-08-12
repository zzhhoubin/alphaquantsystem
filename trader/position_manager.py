"""
仓位管理器 —— 修复原版成本计算 bug，支持多标的
增强回测功能：快照记录、历史追踪
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional, List
import pandas as pd
from loguru import logger
from alphaQuantSystem.core import Direction


@dataclass
class Position:
    symbol: str
    direction: Direction = Direction.LONG
    volume: float = 0.0
    frozen: float = 0.0
    avg_price: float = 0.0
    current_price: float = 0.0
    realized_pnl: float = 0.0
    update_time: datetime = field(default_factory=datetime.now)

    @property
    def market_value(self) -> float:
        return self.volume * self.current_price

    @property
    def pnl(self) -> float:
        """未实现盈亏"""
        return (self.current_price - self.avg_price) * self.volume

    @property
    def pnl_pct(self) -> float:
        """未实现盈亏百分比"""
        if self.avg_price == 0:
            return 0.0
        return (self.current_price - self.avg_price) / self.avg_price

    @property
    def total_pnl(self) -> float:
        """总盈亏（已实现+未实现）"""
        return self.realized_pnl + self.pnl


@dataclass
class PositionSnapshot:
    """持仓快照（用于回测分析）"""
    date: datetime
    positions: Dict[str, Position]
    total_market_value: float
    total_pnl: float
    total_realized_pnl: float
    total_unrealized_pnl: float


class PositionManager:
    """
    统一仓位管理
    - 维护各标的持仓
    - 更新均价（修复原版 bug：先更新 quantity 再算成本）
    - 提供仓位报告
    - 【新增】回测快照记录、历史追踪
    """

    def __init__(self, initial_capital: float = 1000000.0, max_position_ratio: float = 0.2):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.max_position_value = initial_capital * max_position_ratio
        self._positions: Dict[str, Position] = {}
        self._history = []

        # 【新增】回测快照列表
        self._snapshots: List[PositionSnapshot] = []

    def on_trade(self, symbol: str, direction: Direction, volume: float, price: float, commission: float = 0.0):
        """成交回报 → 更新仓位（含佣金，与 AccountManager 口径一致）"""
        volume = abs(float(volume))
        price = float(price)
        commission = float(commission or 0)
        if volume <= 0 or price <= 0:
            logger.warning(f'忽略非法成交: {symbol} {direction.value} volume={volume} price={price}')
            return
        if symbol not in self._positions:
            self._positions[symbol] = Position(symbol=symbol, direction=direction)
        pos = self._positions[symbol]
        trade_value = volume * price
        if direction == Direction.LONG:
            old_value = pos.avg_price * pos.volume
            pos.volume += volume
            pos.avg_price = (old_value + trade_value) / pos.volume if pos.volume > 0 else price
            self.cash -= trade_value
        else:
            sellable = min(volume, pos.volume)
            if sellable <= 0:
                logger.warning(f'仓位不足，忽略卖出: {symbol} req={volume} hold={pos.volume}')
                return
            if volume > pos.volume:
                logger.warning(f'仓位不足，按可卖数量成交: {symbol} req={volume} fill={sellable}')
            pos.realized_pnl += (price - pos.avg_price) * sellable
            pos.volume -= sellable
            self.cash += sellable * price
            if pos.volume == 0:
                pos.avg_price = 0.0
        self.cash -= commission
        pos.current_price = price
        pos.update_time = datetime.now()
        self._history.append(
            {'timestamp': datetime.now(), 'symbol': symbol, 'direction': direction.value, 'volume': volume,
             'price': price, 'position_volume': pos.volume, 'avg_price': pos.avg_price,
             'realized_pnl': pos.realized_pnl, 'unrealized_pnl': pos.pnl, 'total_pnl': pos.total_pnl})
        logger.info(f'仓位更新: {symbol} {direction.value} {volume}股 @{price:.3f} | 剩余: {pos.volume}股')

    def update_price(self, symbol: str, price: float):
        """行情推送 → 更新最新价"""
        if symbol in self._positions:
            self._positions[symbol].current_price = price
            self._positions[symbol].update_time = datetime.now()

    def get_position(self, symbol: str) -> Optional[Position]:
        return self._positions.get(symbol)

    def get_all_positions(self) -> Dict[str, Position]:
        return {k: v for k, v in self._positions.items() if v.volume > 0}

    def check_position_limit(
            self,
            symbol: str,
            volume: float,
            price: float,
            *,
            portfolio_cap: Optional[float] = None,
            current_symbol_value: Optional[float] = None,
    ) -> bool:
        """
        本标的拟开仓名义 + 当前市值 不得超过 ``portfolio_cap``（未传时沿用 ``initial_capital * max_position_ratio``）。
        ``current_symbol_value`` 由调用方传入时优先于本地账本（便于回测 ``position_source=engine`` 与权益一致）。
        """
        proposed_value = volume * price
        if current_symbol_value is not None:
            current_value = max(0.0, float(current_symbol_value))
        else:
            pos = self._positions.get(symbol)
            current_value = 0.0
            if pos and pos.volume > 0:
                base_price = pos.current_price if pos.current_price > 0 else pos.avg_price
                current_value = pos.volume * base_price
        cap = self.max_position_value if portfolio_cap is None else float(portfolio_cap)
        if cap <= 0:
            return proposed_value <= 0
        return current_value + proposed_value <= cap + 1e-9

    @property
    def total_market_value(self) -> float:
        return sum((p.market_value for p in self._positions.values()))

    @property
    def total_assets(self) -> float:
        return self.cash + self.total_market_value

    @property
    def leverage(self) -> float:
        if self.total_assets == 0:
            return 0.0
        return self.total_market_value / self.total_assets

    def generate_report(self) -> pd.DataFrame:
        rows = []
        for symbol, pos in self._positions.items():
            if pos.volume > 0:
                rows.append({'symbol': symbol, 'volume': pos.volume, 'avg_price': pos.avg_price,
                             'current_price': pos.current_price, 'market_value': pos.market_value,
                             'realized_pnl': pos.realized_pnl, 'unrealized_pnl': pos.pnl, 'total_pnl': pos.total_pnl,
                             'pnl': pos.pnl, 'pnl_pct': f'{pos.pnl_pct:.2%}'})
        return pd.DataFrame(rows)

    def snapshot(self, date: datetime):
        """
        保存持仓快照（用于回测分析）
        调用关系：由 BacktestEngine 在每日结束时调用

        参数:
            date: 快照日期
        """
        import copy

        # 深拷贝当前持仓
        positions_copy = {k: copy.deepcopy(v) for k, v in self._positions.items() if v.volume > 0}

        # 计算汇总指标
        total_market_value = sum(p.market_value for p in positions_copy.values())
        total_realized_pnl = sum(p.realized_pnl for p in positions_copy.values())
        total_unrealized_pnl = sum(p.pnl for p in positions_copy.values())
        total_pnl = total_realized_pnl + total_unrealized_pnl

        snapshot = PositionSnapshot(
            date=date,
            positions=positions_copy,
            total_market_value=total_market_value,
            total_pnl=total_pnl,
            total_realized_pnl=total_realized_pnl,
            total_unrealized_pnl=total_unrealized_pnl
        )

        self._snapshots.append(snapshot)

        logger.debug(
            f'[PositionManager] 快照: {date.date()} '
            f'持仓数={len(positions_copy)} 市值={total_market_value:.2f} '
            f'总盈亏={total_pnl:.2f}'
        )

    def get_snapshots(self) -> List[PositionSnapshot]:
        """
        获取所有持仓快照
        返回:
            快照列表
        """
        return self._snapshots.copy()

    def get_history(self) -> pd.DataFrame:
        """
        获取历史交易记录
        返回:
            交易历史DataFrame
        """
        if not self._history:
            return pd.DataFrame()
        return pd.DataFrame(self._history)

    def get_total_realized_pnl(self) -> float:
        """获取总已实现盈亏"""
        return sum(p.realized_pnl for p in self._positions.values())

    def get_total_unrealized_pnl(self) -> float:
        """获取总未实现盈亏"""
        return sum(p.pnl for p in self._positions.values())

    def get_total_pnl(self) -> float:
        """获取总盈亏"""
        return self.get_total_realized_pnl() + self.get_total_unrealized_pnl()
