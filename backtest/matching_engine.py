"""
撮合引擎 - 回测订单撮合逻辑
负责将信号转换为订单，并根据市场数据模拟成交
"""
from datetime import datetime
from typing import Callable, Dict, List, Optional
import uuid

from loguru import logger

from alphaQuantSystem.core import (
    Event, EventEngine, EventType,
    SignalData, OrderData, TradeData, BarData,
    Direction, OrderType, OrderStatus
)
from alphaQuantSystem.backtest.commission import CommissionModel, AShareCommission


class MatchingEngine:
    """
    撮合引擎
    职责：
    - 监听 SIGNAL 事件
    - 将信号转为订单
    - 根据市场数据模拟成交
    - 发送 TRADE 事件
    """

    def __init__(self,
                 event_engine: Optional[EventEngine] = None,
                 commission_model: Optional[CommissionModel] = None,
                 slippage: float = 0.0,
                 lot_size: int = 100):
        """
        参数:
            event_engine: 事件引擎 (optional, for stateless match() usage)
            commission_model: 佣金模型
            slippage: 滑点（百分比，如0.001表示千一）
            lot_size: 整手股数（A股默认100）
        """
        self.event_engine = event_engine
        self.commission_model = commission_model or AShareCommission()
        self.slippage = slippage
        self.lot_size = lot_size

        # 订单管理
        self.orders: Dict[str, OrderData] = {}  # order_id -> OrderData
        self.pending_orders: List[OrderData] = []  # 限价单队列

        # 当前Bar数据（用于撮合）
        self.current_bars: Dict[str, BarData] = {}  # symbol -> BarData

        # 成交记录
        self.trades: List[TradeData] = []  # 所有成交记录

        # 统计
        self.total_commission = 0.0
        self.trade_count = 0

        self._get_hold_volume: Optional[Callable[[str], float]] = None
        self._get_available_cash: Optional[Callable[[], float]] = None
        self._sync_trade_drain: Optional[Callable[[], None]] = None

        # 订阅事件（SIGNAL 由 BacktestEngine 统一管理，此处只订阅 BAR）
        if self.event_engine is not None:
            self.event_engine.subscribe(EventType.BAR, self.on_bar)

        logger.info('[MatchingEngine] 撮合引擎初始化完成')

    def set_hold_volume_resolver(self, resolver: Callable[[str], float]):
        """回测引擎注入持仓查询，卖出前校验可卖数量。"""
        self._get_hold_volume = resolver

    def set_available_cash_resolver(self, resolver: Callable[[], float]):
        """回测引擎注入可用现金查询，买入前校验资金（含预估佣金）。"""
        self._get_available_cash = resolver

    def set_sync_trade_drain(self, handler: Callable[[], None]):
        """回测引擎注入：每笔成交入队后立即同步消费 TRADE，避免连续信号现金检查读到 stale balance。"""
        self._sync_trade_drain = handler

    def on_bar(self, event: Event):
        """
        接收Bar事件，更新当前市场数据
        调用关系：由 EventEngine 在收到 BAR 事件时回调
        """
        bar: BarData = event.data
        self.current_bars[bar.symbol] = bar

    def on_signal(self, event: Event):
        """
        接收信号事件，转换为订单并尝试撮合
        调用关系：由 EventEngine 在收到 SIGNAL 事件时回调
        """
        signal: SignalData = event.data

        # 创建订单
        order = self._create_order_from_signal(signal)
        self.orders[order.order_id] = order

        # 尝试撮合
        if order.order_type == OrderType.MARKET:
            # 市价单：立即按当前Bar收盘价成交
            self._match_market_order(order)
        elif order.order_type == OrderType.LIMIT:
            # 限价单：加入待撮合队列
            self.pending_orders.append(order)
            logger.debug(f'[MatchingEngine] 限价单入队: {order.symbol} {order.direction.value} {order.volume}@{order.price}')

    def on_signal_sync(self, signal: SignalData):
        """
        同步处理信号（用于回测，避免事件队列延迟）
        调用关系：由 BacktestEngine 直接调用
        """
        # 创建订单
        order = self._create_order_from_signal(signal)
        self.orders[order.order_id] = order

        # 尝试撮合
        if order.order_type == OrderType.MARKET:
            # 市价单：立即按当前Bar收盘价成交
            self._match_market_order(order)
        elif order.order_type == OrderType.LIMIT:
            # 限价单：加入待撮合队列
            self.pending_orders.append(order)
            logger.debug(f'[MatchingEngine] 限价单入队: {order.symbol} {order.direction.value} {order.volume}@{order.price}')

    def match_pending_orders(self):
        """
        撮合待成交限价单
        调用关系：由 BacktestEngine 在每个Bar结束时调用

        参数:
            current_dt: 当前时间
        """
        matched_orders = []

        for order in self.pending_orders:
            bar = self.current_bars.get(order.symbol)
            if bar is None:
                continue

            # 判断是否可以成交
            if self._can_fill_limit_order(order, bar):
                self._match_limit_order(order, bar)
                if order.status == OrderStatus.ALLTRADED:
                    matched_orders.append(order)
                elif order.status == OrderStatus.REJECTED and order.direction == Direction.SHORT:
                    matched_orders.append(order)

        # 移除已成交订单
        for order in matched_orders:
            self.pending_orders.remove(order)

    def _create_order_from_signal(self, signal: SignalData) -> OrderData:
        """
        将信号转换为订单
        调用关系：由 on_signal 调用
        """
        order_id = self._generate_order_id()

        # 判断订单类型（价格为0表示市价单）
        order_type = OrderType.MARKET if signal.price == 0 else OrderType.LIMIT

        # 提取备注信息
        order_remark = signal.meta.get('reason', '') if signal.meta else ''

        order = OrderData(
            order_id=order_id,
            strategy_id=signal.strategy_id,
            symbol=signal.symbol,
            direction=signal.direction,
            order_type=order_type,
            volume=signal.volume,
            price=signal.price,
            status=OrderStatus.SUBMITTING,
            traded=0.0,
            event_time=signal.event_time,
            order_remark=order_remark
        )

        logger.debug(
            f'[MatchingEngine] 订单创建: {order.symbol} '
            f'{order.direction.value} {order.volume}股 '
            f'类型={order.order_type.value} 价格={order.price}'
        )

        return order

    def _match_market_order(self, order: OrderData):
        """
        撮合市价单（按当前Bar收盘价成交）
        调用关系：由 on_signal 调用
        """
        bar = self.current_bars.get(order.symbol)
        if bar is None:
            logger.warning(f'[MatchingEngine] 市价单撮合失败：无当前行情 {order.symbol}')
            order.status = OrderStatus.REJECTED
            return

        fill_volume = order.volume
        if order.direction == Direction.SHORT:
            fill_volume = self._clip_sell_volume(order.symbol, fill_volume)
            if fill_volume <= 0:
                logger.warning(
                    f'[MatchingEngine] 卖出拒单：无持仓或不可卖 {order.symbol} req={order.volume}'
                )
                order.status = OrderStatus.REJECTED
                return

        # 成交价格：收盘价 + 滑点
        fill_price = self._apply_slippage(bar.close, order.direction)

        if order.direction == Direction.LONG:
            fill_volume = self._clip_buy_volume(
                fill_price, fill_volume, order.symbol, order.strategy_id, order.order_id,
            )
            if fill_volume <= 0:
                logger.warning(
                    f'[MatchingEngine] 买入拒单：现金不足 {order.symbol} '
                    f'req={order.volume} price={fill_price:.4f}'
                )
                order.status = OrderStatus.REJECTED
                return

        # 生成成交
        self._execute_order(order, fill_price, fill_volume, bar.event_time)

    def _match_limit_order(self, order: OrderData, bar: BarData):
        """
        撮合限价单
        调用关系：由 match_pending_orders 调用
        """
        fill_price = order.price
        fill_volume = order.volume

        if order.direction == Direction.LONG:
            fill_volume = self._clip_buy_volume(
                fill_price, fill_volume, order.symbol, order.strategy_id, order.order_id,
            )
            if fill_volume <= 0:
                logger.warning(
                    f'[MatchingEngine] 限价买入拒单：现金不足 {order.symbol} '
                    f'req={order.volume}@{fill_price:.4f}'
                )
                order.status = OrderStatus.REJECTED
                return
        elif order.direction == Direction.SHORT:
            fill_volume = self._clip_sell_volume(order.symbol, fill_volume)
            if fill_volume <= 0:
                logger.warning(
                    f'[MatchingEngine] 限价卖出拒单：无持仓或不可卖 {order.symbol} req={order.volume}'
                )
                order.status = OrderStatus.REJECTED
                return

        self._execute_order(order, fill_price, fill_volume, bar.event_time)

    def _clip_buy_volume(
        self,
        fill_price: float,
        requested: float,
        symbol: str,
        strategy_id: str,
        order_id: str,
    ) -> float:
        """按可用现金（含佣金）裁剪买入数量；无 cash resolver 时原样返回。"""
        if self._get_available_cash is None or fill_price <= 0 or requested <= 0:
            return requested

        cash = float(self._get_available_cash() or 0)
        if cash <= 0:
            return 0.0

        max_vol = int(min(requested, cash / fill_price))
        max_vol = max_vol // self.lot_size * self.lot_size
        if max_vol <= 0:
            return 0.0

        vol = max_vol
        while vol > 0:
            probe = TradeData(
                trade_id='probe',
                order_id=order_id,
                strategy_id=strategy_id,
                symbol=symbol,
                direction=Direction.LONG,
                price=fill_price,
                volume=float(vol),
                event_time=datetime.now(),
            )
            cost = fill_price * vol + self.commission_model.calculate(probe, quiet=True)
            if cost <= cash + 1e-6:
                if vol < requested:
                    logger.warning(
                        f'[MatchingEngine] 买入按现金裁剪: {symbol} '
                        f'req={requested} fill={vol} cash={cash:.2f} need={cost:.2f}'
                    )
                return float(vol)
            vol -= self.lot_size
        return 0.0

    def _clip_sell_volume(self, symbol: str, requested: float) -> float:
        """按可卖持仓裁剪卖出数量；无 resolver 或零持仓时返回 0。"""
        if requested <= 0:
            return 0.0
        if self._get_hold_volume is None:
            logger.warning('[MatchingEngine] 卖出拒单：未注入持仓查询 resolver')
            return 0.0
        hold = float(self._get_hold_volume(symbol) or 0)
        if hold <= 0:
            return 0.0
        if requested > hold:
            logger.warning(
                f'[MatchingEngine] 卖出按持仓裁剪: {symbol} req={requested} hold={hold}'
            )
            return hold // self.lot_size * self.lot_size
        return requested // self.lot_size * self.lot_size

    def _can_fill_limit_order(self, order: OrderData, bar: BarData) -> bool:
        """
        判断限价单是否可以成交
        规则：
        - 买入：限价 >= 当日最低价
        - 卖出：限价 <= 当日最高价

        调用关系：由 match_pending_orders 调用
        """
        if order.direction == Direction.LONG:
            # 买入：限价大于等于最低价即可成交
            return order.price >= bar.low
        else:
            # 卖出：限价小于等于最高价即可成交
            return order.price <= bar.high

    def _execute_order(self, order: OrderData, price: float, volume: float, event_time: datetime):
        """
        执行订单成交
        调用关系：由 _match_market_order 和 _match_limit_order 调用
        """
        if volume <= 0:
            order.status = OrderStatus.REJECTED
            return

        trade_id = self._generate_trade_id()

        # 创建成交记录
        trade = TradeData(
            trade_id=trade_id,
            order_id=order.order_id,
            strategy_id=order.strategy_id,
            symbol=order.symbol,
            direction=order.direction,
            price=price,
            volume=volume,
            event_time=event_time
        )

        # 计算佣金
        commission = self.commission_model.calculate(trade, quiet=True)

        if order.direction == Direction.LONG and self._get_available_cash is not None:
            cash = float(self._get_available_cash() or 0)
            need = price * volume + commission
            if need > cash + 1e-6:
                logger.warning(
                    f'[MatchingEngine] 买入拒单（最终校验）: {trade.symbol} '
                    f'need={need:.2f} cash={cash:.2f}'
                )
                order.status = OrderStatus.REJECTED
                return

        if order.direction == Direction.SHORT:
            if self._get_hold_volume is None:
                logger.warning(
                    f'[MatchingEngine] 卖出拒单（最终校验）: {trade.symbol} 未注入持仓查询'
                )
                order.status = OrderStatus.REJECTED
                return
            hold = float(self._get_hold_volume(trade.symbol) or 0)
            if volume <= 0 or volume > hold + 1e-6:
                logger.warning(
                    f'[MatchingEngine] 卖出拒单（最终校验）: {trade.symbol} '
                    f'vol={volume} hold={hold}'
                )
                order.status = OrderStatus.REJECTED
                return

        self.total_commission += commission

        # 更新订单状态
        order.traded = volume
        order.status = OrderStatus.ALLTRADED

        # 更新统计
        self.trade_count += 1

        # 保存成交记录
        self.trades.append(trade)

        # 发送成交事件
        self.event_engine.put(Event(EventType.TRADE, trade))
        if self._sync_trade_drain is not None:
            self._sync_trade_drain()

        trade_day = (
            event_time.strftime("%Y-%m-%d")
            if hasattr(event_time, "strftime")
            else str(event_time)[:10]
        )
        side = "买入" if trade.direction == Direction.LONG else "卖出"
        logger.info(
            f'[MatchingEngine] 成交 {trade_day} {side} {trade.symbol} '
            f'{trade.volume:.0f}股@{trade.price:.3f} 佣金={commission:.2f}'
        )

    def _apply_slippage(self, price: float, direction: Direction) -> float:
        """
        应用滑点
        调用关系：由 _match_market_order 调用
        """
        if self.slippage == 0:
            return price

        if direction == Direction.LONG:
            # 买入：价格上浮
            return price * (1 + self.slippage)
        else:
            # 卖出：价格下滑
            return price * (1 - self.slippage)

    def _generate_order_id(self) -> str:
        """生成订单ID"""
        return f'O{datetime.now().strftime("%Y%m%d%H%M%S%f")}{uuid.uuid4().hex[:8]}'

    def _generate_trade_id(self) -> str:
        """生成成交ID"""
        return f'T{datetime.now().strftime("%Y%m%d%H%M%S%f")}{uuid.uuid4().hex[:8]}'

    def get_statistics(self) -> Dict:
        """
        获取撮合统计信息
        返回:
            统计数据字典
        """
        return {
            'total_trades': self.trade_count,
            'total_commission': self.total_commission,
            'pending_orders': len(self.pending_orders),
            'total_orders': len(self.orders)
        }

    def get_trades(self) -> List[TradeData]:
        """
        获取所有成交记录
        返回:
            成交记录列表
        """
        return self.trades.copy()

    def match(self, order: "OrderData", bar: "BarData") -> Optional["TradeData"]:
        """给定 Order + Bar 撮合成交；复用限价/资金/持仓裁剪逻辑（无事件总线副作用）。"""
        if order.order_type == OrderType.LIMIT and order.price > 0:
            if not self._can_fill_limit_order(order, bar):
                return None
            fill_price = order.price
        else:
            fill_price = self._apply_slippage(bar.open, order.direction)

        order_id = order.order_id or self._generate_order_id()
        fill_volume = float(order.volume)

        if order.direction == Direction.LONG:
            fill_volume = self._clip_buy_volume(
                fill_price, fill_volume, order.symbol, order.strategy_id, order_id,
            )
        else:
            fill_volume = self._clip_sell_volume(order.symbol, fill_volume)

        if fill_volume <= 0:
            return None

        trade = TradeData(
            trade_id=self._generate_trade_id(),
            order_id=order_id,
            strategy_id=order.strategy_id,
            symbol=order.symbol,
            direction=order.direction,
            price=fill_price,
            volume=fill_volume,
            event_time=bar.event_time,
            signal_id=getattr(order, 'signal_id', ''),
            tag=getattr(order, 'tag', None),
        )

        commission = self.commission_model.calculate(trade, quiet=True)
        if order.direction == Direction.LONG and self._get_available_cash is not None:
            cash = float(self._get_available_cash() or 0)
            if fill_price * fill_volume + commission > cash + 1e-6:
                return None
        if order.direction == Direction.SHORT and self._get_hold_volume is not None:
            hold = float(self._get_hold_volume(order.symbol) or 0)
            if fill_volume > hold + 1e-6:
                return None

        self.total_commission += commission
        self.trade_count += 1
        self.trades.append(trade)
        return trade
