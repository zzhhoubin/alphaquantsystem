"""
QMT 实盘交易引擎
订阅 ORDER_REQUEST 事件（已经过风控），调用 QmtGateway 下单
同时监听 TRADE 事件，更新 PositionManager
"""
from typing import Optional
from loguru import logger
from alphaQuantSystem.core import EventEngine, Event, EventType, OrderData, TradeData, SignalData, Direction, OrderType, OrderType
from alphaQuantSystem.gateway.qmt_gateway import QmtGateway
from alphaQuantSystem.utils.helpers import adjust_symbol, round_volume, split_order_volumes
from .position_manager import PositionManager

def _calc_slippage(symbol: str, price: float, direction: Direction, slippage: float=0.01) -> float:
    """
    职责:
        根据标的类型与买卖方向计算滑点后的下单价格。

    参数:
        symbol (str): 标的代码（含交易所后缀或纯六码）。
        price (float): 基准价格（通常为策略给定限价）。
        direction (Direction): 下单方向，LONG/SHORT。
        slippage (float): 基础滑点值。

    返回:
        float: 应用于下单的价格。

    异常:
        无显式抛出；参数非法时由上层逻辑保证。

    调用关系:
        - 由 QmtTrader._send() 调用，作为下单前价格标准化步骤。
    """
    code = symbol[:6]
    if code[:3] in ['110', '113', '123', '127', '128', '111'] or code[:2] in ['11', '12']:
        slip = slippage / 10
    else:
        slip = slippage
    return price + slip if direction == Direction.LONG else price - slip

class QmtTrader:
    """
    实盘交易引擎
    - 接收 ORDER_REQUEST → 调用 QmtGateway 下单
    - 接收 TRADE 回报 → 更新 PositionManager
    """

    def __init__(self, event_engine: EventEngine, gateway: QmtGateway, initial_capital: float=1000000.0, slippage: float=0.01):
        """
        职责:
            初始化交易执行器，订阅订单请求与成交事件，承接风控后订单执行。

        参数:
            event_engine (EventEngine): 事件总线实例。
            gateway (QmtGateway): QMT 网关实例，负责实际下单与回报。
            initial_capital (float): 账本初始资金，用于 PositionManager。
            slippage (float): 下单滑点参数。

        返回:
            None

        异常:
            无显式抛出；依赖对象初始化异常透传。

        调用关系:
            - 由应用装配层创建。
            - 订阅 ORDER_REQUEST 与 TRADE 事件形成执行闭环。
        """
        self.event_engine = event_engine
        self.gateway = gateway
        self.position_manager = PositionManager(initial_capital=initial_capital)
        self.slippage = slippage
        self.event_engine.subscribe(EventType.ORDER_REQUEST, self._on_order_request)
        self.event_engine.subscribe(EventType.TRADE, self._on_trade)

    def _on_order_request(self, event: Event):
        """
        职责:
            处理风控放行后的订单请求事件，并发起下单流程。

        参数:
            event (Event): 事件类型为 ORDER_REQUEST，载荷为 OrderData。

        返回:
            None

        异常:
            无显式抛出；下游 _send() 的异常由其内部或调用链处理。

        调用关系:
            - 由 EventEngine 在 ORDER_REQUEST 事件到达时回调。
            - 内部调用 _send() 执行订单标准化与下单。
        """
        order: OrderData = event.data
        self._send(order)

    def _on_trade(self, event: Event):
        """
        职责:
            处理成交回报事件并更新本地持仓账本。

        参数:
            event (Event): 事件类型为 TRADE，载荷为 TradeData。

        返回:
            None

        异常:
            无显式抛出；PositionManager 异常透传。

        调用关系:
            - 由 EventEngine 在 TRADE 事件到达时回调。
            - 内部调用 PositionManager.on_trade() 更新仓位与现金。
        """
        trade: TradeData = event.data
        self.position_manager.on_trade(symbol=trade.symbol, direction=trade.direction, volume=trade.volume, price=trade.price)

    def _send(self, order: OrderData):
        """
        职责:
            对订单进行代码、数量、价格标准化后，通过网关发送下单请求。

        参数:
            order (OrderData): 待发送订单。

        返回:
            None

        异常:
            无显式抛出；网关下单异常由 gateway.send_order() 透传或记录。

        调用关系:
            - 由 _on_order_request() 调用。
            - 内部依赖 adjust_symbol()/round_volume()/_calc_slippage()。
            - 最终调用 gateway.send_order()。
        """
        symbol = adjust_symbol(order.symbol)
        volumes = split_order_volumes(symbol, order.volume)
        if not volumes:
            logger.warning(f'委托数量取整后为 0，跳过: {symbol}')
            return
        if len(volumes) > 1:
            logger.info(
                '大单拆分为 {} 笔: {} 合计 {} 股',
                len(volumes), symbol, sum(volumes),
            )
        for volume in volumes:
            self._send_one(order, symbol, volume)

    def _send_one(self, order: OrderData, symbol: str, volume: int) -> None:
        if order.direction == Direction.SHORT:
            volumes = self.gateway.get_position_volumes(symbol)
            if volumes is None:
                logger.warning(f'无法获取QMT持仓，拒绝卖出: {symbol} req={volume}')
                return
            total, available = volumes
            if available <= 0:
                logger.warning(f'可用仓位为 0，拒绝卖出: {symbol} req={volume}')
                return
            if volume > available:
                adj_volume = round_volume(symbol, available)
                if adj_volume <= 0:
                    logger.warning(f'可用仓位取整后为 0，拒绝卖出: {symbol} available={available}')
                    return
                logger.warning(f'卖出数量超过可用仓位，自动截断: {symbol} total={total} available={available} req={volume} -> {adj_volume}')
                volume = adj_volume
        if order.order_type == OrderType.MARKET or order.price <= 0:
            price = 0.0
        else:
            price = _calc_slippage(symbol, order.price, order.direction, self.slippage)
        adjusted = OrderData(order_id=order.order_id, strategy_id=order.strategy_id, symbol=symbol, direction=order.direction, order_type=order.order_type, volume=volume, price=price, order_remark=order.order_remark)
        self.gateway.send_order(adjusted)

    def buy(self, strategy_id: str, symbol: str, volume: float, price: float):
        """
        职责:
            便捷创建买入信号并投递到事件总线。

        参数:
            strategy_id (str): 策略 ID。
            symbol (str): 标的代码。
            volume (float): 下单数量。
            price (float): 下单价格。

        返回:
            None

        异常:
            无显式抛出；事件投递异常由 EventEngine.put() 透传。

        调用关系:
            - 常供上层策略或脚本直接调用。
            - 事件将流向 RiskGateway 进行风控。
        """
        signal = SignalData(strategy_id=strategy_id, symbol=symbol, direction=Direction.LONG, volume=volume, price=price)
        self.event_engine.put(Event(EventType.SIGNAL, signal))

    def sell(self, strategy_id: str, symbol: str, volume: float, price: float):
        """
        职责:
            便捷创建卖出信号并投递到事件总线。

        参数:
            strategy_id (str): 策略 ID。
            symbol (str): 标的代码。
            volume (float): 下单数量。
            price (float): 下单价格。

        返回:
            None

        异常:
            无显式抛出；事件投递异常由 EventEngine.put() 透传。

        调用关系:
            - 常供上层策略或脚本直接调用。
            - 事件将流向 RiskGateway 进行风控。
        """
        signal = SignalData(strategy_id=strategy_id, symbol=symbol, direction=Direction.SHORT, volume=volume, price=price)
        self.event_engine.put(Event(EventType.SIGNAL, signal))
