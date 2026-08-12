"""
QMT 网关 —— 行情订阅 + 实盘交易
封装 xtquant，向上只暴露事件总线接口
"""
import random
from datetime import datetime
from typing import Any, Dict, Optional
from loguru import logger
from alphaQuantSystem.core import EventEngine, Event, EventType, TickData, OrderData, TradeData, PositionData, AccountData, Direction, OrderType, OrderStatus
QMT_PATH_SIMULATED = 'D:/国金QMT交易端模拟/userdata_mini'
ACCOUNT_SIMULATED = '55012491'
# QMT_PATH_REAL = 'D:/国金QMT交易端模拟/userdata_mini'
# ACCOUNT_REAL = '8882405326'
try:
    from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback
    from xtquant.xttype import StockAccount
    from xtquant import xtconstant, xtdata as _xtdata
    _HAS_XTQ = True
except ImportError:
    _HAS_XTQ = False
    logger.warning('xtquant 未安装，QmtGateway 将以离线模式运行')

class _QmtCallback:
    """
    职责:
        将 xtquant 回调对象适配为系统事件，统一投递到 EventEngine。

    参数:
        gateway (QmtGateway): 网关实例，用于访问事件总线。

    返回:
        None

    异常:
        无显式抛出；回调字段异常由调用方或上游 SDK 保证。

    调用关系:
        - 由 QmtGateway.connect() 创建并挂载到 XtQuantTrader。
    """

    def __init__(self, gateway: 'QmtGateway'):
        """
        职责:
            保存网关引用以便在回调中投递系统事件。

        参数:
            gateway (QmtGateway): 网关实例。

        返回:
            None

        异常:
            无显式抛出。

        调用关系:
            - 仅由 QmtGateway.connect() 调用。
        """
        self._gw = gateway

    def on_stock_order(self, order):
        """
        职责:
            处理委托回报并转换为 ORDER 事件。
        参数:
            order: xtquant 委托回报对象。
        返回:
            None
        异常:
            无显式抛出。
        调用关系:
            - 由 xtquant 回调触发，投递 EventType.ORDER。
        """
        od = OrderData(order_id=str(order.order_id), strategy_id=order.order_remark or '', symbol=order.stock_code, direction=Direction.LONG if order.order_type in (23,) else Direction.SHORT, order_type=OrderType.LIMIT, volume=float(order.order_volume), price=float(order.price), status=OrderStatus.NOTTRADED, traded=float(order.traded_volume), event_time=datetime.now(), order_remark=order.order_remark or '')
        self._gw.event_engine.put(Event(EventType.ORDER, od))

    def on_stock_trade(self, trade):
        """
        职责:
            处理成交回报并转换为 TRADE 事件。
        参数:
            trade: xtquant 成交回报对象。
        返回:
            None
        异常:
            无显式抛出。
        调用关系:
            - 由 xtquant 回调触发，投递 EventType.TRADE。
        """
        td = TradeData(trade_id=str(trade.traded_id), order_id=str(trade.order_id), strategy_id=trade.order_remark or '', symbol=trade.stock_code, direction=Direction.LONG if trade.offset_flag == 48 else Direction.SHORT, price=float(trade.traded_price), volume=float(trade.traded_volume), event_time=datetime.now())
        self._gw.event_engine.put(Event(EventType.TRADE, td))

    def on_stock_position(self, position):
        """
        职责:
            处理持仓推送并转换为 POSITION 事件。
        参数:
            position: xtquant 持仓对象。
        返回:
            None
        异常:
            无显式抛出。
        调用关系:
            - 由 xtquant 回调触发，投递 EventType.POSITION。
        """
        pd_ = PositionData(symbol=position.stock_code, direction=Direction.LONG, volume=float(position.volume), frozen=float(position.frozen_volume), price=float(position.avg_price), pnl=float(position.open_pnl))
        self._gw.event_engine.put(Event(EventType.POSITION, pd_))

    def on_stock_asset(self, asset):
        """
        职责:
            处理资产推送并转换为 ACCOUNT 事件。
        参数:
            asset: xtquant 资产对象。
        返回:
            None
        异常:
            无显式抛出。
        调用关系:
            - 由 xtquant 回调触发，投递 EventType.ACCOUNT。
        """
        self._gw._record_account_asset(asset)
        ad = AccountData(account_id=asset.account_id, balance=float(asset.total_asset), frozen=float(asset.total_asset - asset.cash), available=float(asset.cash))
        self._gw.event_engine.put(Event(EventType.ACCOUNT, ad))

    def on_disconnected(self):
        """
        职责:
            处理连接断开通知并记录日志。
        参数:
            无
        返回:
            None
        异常:
            无显式抛出。
        调用关系:
            - 由 xtquant 回调触发。
        """
        logger.warning('QMT 连接断开')

    def on_order_error(self, order_error):
        """
        职责:
            记录委托错误回调信息。
        参数:
            order_error: xtquant 委托错误对象。
        返回:
            None
        异常:
            无显式抛出。
        调用关系:
            - 由 xtquant 回调触发。
        """
        logger.error(f'委托报错: {order_error.order_remark} | {order_error.error_msg}')

    def on_cancel_error(self, cancel_error):
        """
        职责:
            记录撤单错误回调信息。
        参数:
            cancel_error: xtquant 撤单错误对象。
        返回:
            None
        异常:
            无显式抛出。
        调用关系:
            - 由 xtquant 回调触发。
        """
        logger.error(f'撤单失败: {cancel_error}')

    def on_order_stock_async_response(self, response):
        """
        职责:
            记录异步下单响应。
        参数:
            response: xtquant 异步下单响应对象。
        返回:
            None
        异常:
            无显式抛出。
        调用关系:
            - 由 xtquant 回调触发。
        """
        logger.debug(f'异步委托回调: {response.order_remark}')

    def on_cancel_order_stock_async_response(self, response):
        """
        职责:
            记录异步撤单响应。
        参数:
            response: xtquant 异步撤单响应对象。
        返回:
            None
        异常:
            无显式抛出。
        调用关系:
            - 由 xtquant 回调触发。
        """
        logger.debug(f'异步撤单回调: {response}')

    def on_account_status(self, status):
        """
        职责:
            记录账户状态变化。
        参数:
            status: xtquant 账户状态对象。
        返回:
            None
        异常:
            无显式抛出。
        调用关系:
            - 由 xtquant 回调触发。
        """
        logger.debug(f'账户状态: {status}')

class QmtGateway:
    """
    QMT 网关
    职责：
      1. 连接 QMT 交易端
      2. 订阅行情 → 投递 TICK / BAR 事件
      3. 接收 ORDER_REQUEST 事件 → 调用 xtquant 下单
      4. 把 QMT 回调转换为 ORDER / TRADE / POSITION / ACCOUNT 事件
    """

    def __init__(self, event_engine: EventEngine, is_real: bool=False):
        """
        职责:
            初始化 QMT 网关配置与连接状态。

        参数:
            event_engine (EventEngine): 全局事件总线。
            is_real (bool): 是否使用实盘账号配置。

        返回:
            None

        异常:
            无显式抛出。

        调用关系:
            - 由应用装配层创建后调用 connect()/subscribe_tick()/send_order()。
        """
        self.event_engine = event_engine
        self.is_real = is_real
        self.path = QMT_PATH_REAL if is_real else QMT_PATH_SIMULATED
        self.account_id = ACCOUNT_REAL if is_real else ACCOUNT_SIMULATED
        self.session_id = random.randint(100000, 999999)
        self._xt_trader: Optional[object] = None
        self._acc: Optional[object] = None
        self._connected = False
        self._account_cache: Dict[str, Any] = {'cash': 0.0, 'total_value': 0.0, 'datetime': None}

    def connect(self):
        """
        职责:
            连接 QMT 客户端并注册回调。

        参数:
            无

        返回:
            None

        异常:
            无显式抛出；连接失败写日志并保持未连接状态。

        调用关系:
            - 由应用启动流程调用。
            - 成功后可执行下单与查询接口。
        """
        if not _HAS_XTQ:
            logger.warning('xtquant 不可用，跳过 QMT 连接')
            return
        xt_trader = XtQuantTrader(self.path, self.session_id)
        acc = StockAccount(account_id=self.account_id, account_type='STOCK')
        cb = _QmtCallback(self)
        for method in ['on_disconnected', 'on_stock_order', 'on_stock_asset', 'on_stock_trade', 'on_stock_position', 'on_order_error', 'on_cancel_error', 'on_order_stock_async_response', 'on_cancel_order_stock_async_response', 'on_account_status']:
            setattr(xt_trader, method, getattr(cb, method))
        xt_trader.register_callback(xt_trader)
        xt_trader.start()
        result = xt_trader.connect()
        if result == 0:
            xt_trader.subscribe(acc)
            self._xt_trader = xt_trader
            self._acc = acc
            self._connected = True
            logger.info('QMT 连接成功')
        else:
            logger.error(f'QMT 连接失败，返回码: {result}')

    def disconnect(self):
        """
        职责:
            断开 QMT 连接并重置连接状态。
        参数:
            无
        返回:
            None
        异常:
            无显式抛出。
        调用关系:
            - 由应用退出流程调用。
        """
        if self._xt_trader:
            self._xt_trader.stop()
            self._connected = False

    def _is_ready(self) -> bool:
        """
        职责:
            判断网关是否达到可执行交易与查询的最小就绪条件。

        参数:
            无

        返回:
            bool: True 表示连接成功且交易对象/账户对象均已初始化。

        异常:
            无显式抛出。

        调用关系:
            - 由 send_order/cancel_order/query_account/query_positions 调用。
        """
        return bool(_HAS_XTQ and self._connected and (self._xt_trader is not None) and (self._acc is not None))

    @staticmethod
    def _normalize_xt_tick_dict(tick) -> Optional[dict]:
        """
        职责:
            兼容 xtdata 回调不同数据形态，归一化为单条 tick 字典。
        参数:
            tick: dict 或 list 格式的 tick 原始数据。
        返回:
            Optional[dict]: 成功返回单条 tick，失败返回 None。
        异常:
            无显式抛出。
        调用关系:
            - 由 subscribe_tick() 内部回调调用。
        """
        if isinstance(tick, dict):
            return tick
        if isinstance(tick, list):
            for item in reversed(tick):
                if isinstance(item, dict):
                    return item
        return None

    def subscribe_tick(self, symbol: str):
        """
        职责:
            订阅实时 tick，并将回调转换为系统 TICK 事件。

        参数:
            symbol (str): 订阅标的代码。

        返回:
            None

        异常:
            无显式抛出；xtquant 不可用时直接返回。

        调用关系:
            - 由实盘启动流程调用。
            - 内部调用 _xtdata.subscribe_quote()。
        """
        if not _HAS_XTQ:
            return

        def _on_tick(data):
            if not isinstance(data, dict):
                logger.debug(f'subscribe_tick 回调根类型非 dict: {type(data)}')
                return
            for code, tick in data.items():
                tick_dict = QmtGateway._normalize_xt_tick_dict(tick)
                if not tick_dict:
                    logger.debug(f'subscribe_tick 无法解析: {code} -> {type(tick)}')
                    continue
                td = TickData(symbol=code, last_price=float(tick_dict.get('lastPrice', 0.0) or 0.0), volume=float(tick_dict.get('volume', 0.0) or 0.0), amount=float(tick_dict.get('amount', 0.0) or 0.0), open_price=float(tick_dict.get('open', 0.0) or 0.0), high_price=float(tick_dict.get('high', 0.0) or 0.0), low_price=float(tick_dict.get('low', 0.0) or 0.0), pre_close=float(tick_dict.get('lastClose', 0.0) or 0.0), limit_up=float(tick_dict.get('upperLimit', 0.0) or 0.0), limit_down=float(tick_dict.get('lowerLimit', 0.0) or 0.0), event_time=datetime.now())
                self.event_engine.put(Event(EventType.TICK, td))
        _xtdata.subscribe_quote(symbol, period='tick', callback=_on_tick)

    def send_order(self, order: OrderData) -> Optional[int]:
        """
        职责:
            将标准订单映射为 xtquant 参数并异步发送委托。

        参数:
            order (OrderData): 标准订单对象。

        返回:
            Optional[int]: 下单成功返回 order_id，失败返回 None。

        异常:
            无显式抛出；连接不可用时仅记录 warning。

        调用关系:
            - 由 QmtTrader._send() 调用。
            - 下单后通过 xtquant 回调进入 _QmtCallback。
        """
        if not self._is_ready():
            logger.warning(f'QMT 未就绪，订单未发送: {order.symbol} (connected={self._connected}, trader={self._xt_trader is not None}, acc={self._acc is not None})')
            return None
        order_type = xtconstant.STOCK_BUY if order.direction == Direction.LONG else xtconstant.STOCK_SELL
        price_type = xtconstant.FIX_PRICE if order.order_type == OrderType.LIMIT else xtconstant.LATEST_PRICE
        order_id = self._xt_trader.order_stock_async(account=self._acc, stock_code=order.symbol, order_type=order_type, order_volume=int(order.volume), price_type=price_type, price=order.price, strategy_name=order.strategy_id, order_remark=order.order_remark)
        px_label = '市价' if order.order_type != OrderType.LIMIT else f'@{order.price}'
        logger.info(f'下单 {order.symbol} {order.direction.value} {order.volume}股 {px_label} → order_id={order_id}')
        return order_id

    def cancel_order(self, order_id: int):
        """
        职责:
            按订单号发起异步撤单。
        参数:
            order_id (int): 目标订单 ID。
        返回:
            None
        异常:
            无显式抛出；未连接时直接返回。
        调用关系:
            - 由上层交易控制逻辑调用。
        """
        if not self._is_ready():
            return
        self._xt_trader.cancel_order_stock_async(account=self._acc, order_id=order_id)

    def query_account(self):
        """
        职责:
            异步查询账户资产信息。
        参数:
            无
        返回:
            None
        异常:
            无显式抛出；未连接时直接返回。
        调用关系:
            - 由监控或控制层调用，结果通过回调事件返回。
        """
        if self._is_ready():
            self._xt_trader.query_stock_asset_async(account=self._acc)

    def query_positions(self):
        """
        职责:
            异步查询持仓信息。
        参数:
            无
        返回:
            None
        异常:
            无显式抛出；未连接时直接返回。
        调用关系:
            - 由监控或控制层调用，结果通过回调事件返回。
        """
        if self._is_ready():
            self._xt_trader.query_stock_positions_async(account=self._acc)

    def _record_account_asset(self, asset) -> None:
        """由资金推送或同步查询更新缓存，供 ``get_account_snapshot``。"""
        try:
            self._account_cache = {'cash': float(getattr(asset, 'cash', 0.0) or 0.0), 'total_value': float(getattr(asset, 'total_asset', 0.0) or 0.0), 'datetime': datetime.now()}
        except Exception as e:
            logger.warning(f'更新资金缓存失败: {e}')

    def get_account_snapshot(self) -> Dict[str, Any]:
        """
        返回与回测 ``external_get_account`` 一致的字典：``cash``、``total_value``、``datetime``。
        已连接时尝试 ``query_stock_asset`` 同步刷新；失败或未连则返回最近一次推送缓存。
        """
        if _HAS_XTQ and self._is_ready() and self._xt_trader and self._acc:
            fn = getattr(self._xt_trader, 'query_stock_asset', None)
            if callable(fn):
                try:
                    asset = fn(self._acc)
                    if asset is not None:
                        self._record_account_asset(asset)
                except Exception as e:
                    logger.debug(f'sync query_stock_asset 失败，沿用缓存: {e}')
        return {'cash': float(self._account_cache.get('cash', 0.0) or 0.0), 'total_value': float(self._account_cache.get('total_value', 0.0) or 0.0), 'datetime': self._account_cache.get('datetime')}

    def get_position_volumes(self, symbol: str) -> Optional[tuple[float, float]]:
        """
        职责:
            同步查询指定标的的总持仓与可用持仓。
        参数:
            symbol (str): 标的代码（如 000001.SZ）。
        返回:
            Optional[tuple[float, float]]: (total_volume, available_volume)，失败返回 None。
        """
        if not self._is_ready():
            return None
        try:
            positions = self._xt_trader.query_stock_positions(self._acc) or []
        except Exception as e:
            logger.warning(f'查询持仓失败: {symbol}, err={e}')
            return None
        for pos in positions:
            code = getattr(pos, 'stock_code', '')
            if code != symbol:
                continue
            total = float(getattr(pos, 'm_nVolume', getattr(pos, 'volume', 0.0)) or 0.0)
            available = getattr(pos, 'm_nCanUseVolume', None)
            if available is None:
                frozen = float(getattr(pos, 'frozen_volume', 0.0) or 0.0)
                available = total - frozen
            return (total, max(0.0, float(available)))
        return (0.0, 0.0)

    def get_position_snapshot(self, symbol: str) -> Optional[dict]:
        """
        同步查询单标仓位快照（供策略直接读取仓位真值）。
        """
        if not self._is_ready():
            return None
        try:
            positions = self._xt_trader.query_stock_positions(self._acc) or []
        except Exception as e:
            logger.warning(f'查询单标仓位失败: {symbol}, err={e}')
            return None
        for pos in positions:
            code = getattr(pos, 'stock_code', '')
            if code != symbol:
                continue
            total = float(getattr(pos, 'm_nVolume', getattr(pos, 'volume', 0.0)) or 0.0)
            available = getattr(pos, 'm_nCanUseVolume', None)
            if available is None:
                frozen = float(getattr(pos, 'frozen_volume', 0.0) or 0.0)
                available = total - frozen
            return {'symbol': code, 'volume': max(0.0, total), 'available': max(0.0, float(available)), 'avg_price': float(getattr(pos, 'avg_price', 0.0) or 0.0), 'current_price': float(getattr(pos, 'last_price', 0.0) or 0.0)}
        return {'symbol': symbol, 'volume': 0.0, 'available': 0.0, 'avg_price': 0.0, 'current_price': 0.0}

    def get_all_position_snapshots(self) -> dict:
        """
        同步查询全量仓位快照（symbol -> snapshot）。
        """
        if not self._is_ready():
            return {}
        try:
            positions = self._xt_trader.query_stock_positions(self._acc) or []
        except Exception as e:
            logger.warning(f'查询全量仓位失败: err={e}')
            return {}
        snapshots = {}
        for pos in positions:
            code = getattr(pos, 'stock_code', '')
            if not code:
                continue
            total = float(getattr(pos, 'm_nVolume', getattr(pos, 'volume', 0.0)) or 0.0)
            available = getattr(pos, 'm_nCanUseVolume', None)
            if available is None:
                frozen = float(getattr(pos, 'frozen_volume', 0.0) or 0.0)
                available = total - frozen
            snapshots[code] = {'symbol': code, 'volume': max(0.0, total), 'available': max(0.0, float(available)), 'avg_price': float(getattr(pos, 'avg_price', 0.0) or 0.0), 'current_price': float(getattr(pos, 'last_price', 0.0) or 0.0)}
        return snapshots
