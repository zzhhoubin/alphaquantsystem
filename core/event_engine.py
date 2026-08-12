"""
事件引擎 —— 借鉴 vn.py 设计
单一事件总线：Queue + 后台线程分发
"""
from collections import defaultdict
from queue import Empty, Queue
from threading import Thread
from typing import Callable, Dict, List
import traceback

from loguru import logger

try:
    from .event_type import EventType
    from .object import LogData
except ImportError:
    from alphaQuantSystem.core.event_type import EventType
    from alphaQuantSystem.core.object import LogData


class Event:
    """
    职责:
        封装事件类型与事件载荷，作为事件总线中的最小传输单元。

    参数:
        type (EventType): 事件类型，决定路由到哪些订阅处理器。
        data (Any, optional): 事件负载，可为行情、信号、订单、日志等对象。

    返回:
        None

    异常:
        无显式抛出；参数类型不符合约定时由调用方自行保证。

    调用关系:
        - 由业务模块创建后通过 EventEngine.put() 入队。
        - 在 EventEngine._process() 中被分发到具体 handler。
    """
    __slots__ = ('type', 'data')

    def __init__(self, type: EventType, data=None):
        self.type = type
        self.data = data


HandlerType = Callable[[Event], None]


class EventEngine:
    """
    事件引擎
    - put()  : 任意线程投递事件（线程安全）
    - subscribe() / unsubscribe() : 注册/注销处理器
    - start() / stop() : 启动/停止后台分发线程
    """

    def __init__(self, interval: float = 1.0, *, sync_mode: bool = False):
        """
        职责:
            初始化事件引擎运行状态、事件队列、处理器映射与后台线程对象。

        参数:
            interval (float): 定时器线程投递 TIMER 事件的周期（秒）。
            sync_mode (bool): 为 True 时不启动后台线程，仅由调用方同步 drain（回测用）。

        返回:
            None

        异常:
        无显式抛出；线程或队列初始化异常将由 Python 运行时抛出。

        调用关系:
            - 通常由应用启动阶段（如 main.build_app）创建单例实例。
            - 后续由各模块调用 subscribe()/put() 建立事件流。
        """
        self._queue: Queue = Queue()
        self._active: bool = False
        self._thread: Thread = Thread(target=self._run, daemon=True)
        self._timer_thread: Thread = Thread(target=self._run_timer, daemon=True)
        self._interval: float = interval
        self._sync_mode: bool = sync_mode
        self._handlers: Dict[EventType, List[HandlerType]] = defaultdict(list)
        self._general_handlers: List[HandlerType] = []

    def start(self):
        """
        职责:
            启动事件分发线程与定时器线程，使事件总线进入工作状态。

        参数:
            无

        返回:
            None

        异常:
            无显式抛出；线程启动失败时由底层线程库抛出异常。

        调用关系:
            - 由应用启动入口调用。
            - 启动后 _run() 与 _run_timer() 持续驱动事件处理。
        """
        if self._sync_mode:
            self._active = True
            return
        self._active = True
        self._thread.start()
        self._timer_thread.start()

    def stop(self):
        """
        职责:
            停止事件分发线程并等待退出，释放事件循环资源。

        参数:
            无

        返回:
            None

        异常:
            无显式抛出；join 超时仅结束等待，不主动抛错。

        调用关系:
            - 由应用退出流程调用。
            - 与 start() 成对使用。
        """
        self._active = False
        if self._sync_mode:
            return
        self._thread.join(timeout=3)

    def put(self, event: Event):
        """
        职责:
            线程安全地向事件队列投递事件。

        参数:
            event (Event): 待入队的事件对象。

        返回:
            None

        异常:
            无显式抛出；队列异常由 queue.Queue 透传。

        调用关系:
            - 由策略、风控、交易、网关等模块调用。
            - 入队后由 _run() 线程取出并交给 _process() 处理。
        """
        self._queue.put(event)

    def subscribe(self, event_type: EventType, handler: HandlerType):
        """
        职责:
            为指定事件类型注册处理器。

        参数:
            event_type (EventType): 订阅的事件类型。
            handler (HandlerType): 事件处理回调，签名为 handler(event)。

        返回:
            None

        异常:
            无显式抛出；重复注册会被去重处理。

        调用关系:
            - 由各业务模块在初始化时调用。
            - 注册后在 _process() 分发同类型事件时被执行。
        """
        handlers = self._handlers[event_type]
        if handler not in handlers:
            handlers.append(handler)

    def unsubscribe(self, event_type: EventType, handler: HandlerType):
        """
        职责:
            注销指定事件类型下的处理器。

        参数:
            event_type (EventType): 事件类型。
            handler (HandlerType): 待移除处理器。

        返回:
            None

        异常:
            无显式抛出；若处理器不存在则静默忽略。

        调用关系:
            - 由策略/模块卸载或停止时调用。
            - 与 subscribe() 成对使用。
        """
        handlers = self._handlers[event_type]
        if handler in handlers:
            handlers.remove(handler)

    def poll(self):
        """
        职责:
            非阻塞取出队列中的下一个事件，队列为空时返回 None。
            BacktestEngine 同步模式下使用，避免直接访问 _queue。

        返回:
            Event 或 None
        """
        try:
            return self._queue.get_nowait()
        except Empty:
            return None

    def dispatch(self, event: Event):
        """
        职责:
            同步分发单个事件到已注册处理器。
            BacktestEngine 同步模式下使用，避免直接访问 _process()。

        参数:
            event (Event): 待分发事件。
        """
        self._process(event)

    def subscribe_all(self, handler: HandlerType):
        """
        职责:
            注册“全事件”处理器，接收事件总线中流经的所有事件。

        参数:
            handler (HandlerType): 通用处理器，签名为 handler(event)。

        返回:
            None

        异常:
            无显式抛出；重复注册会被去重处理。

        调用关系:
            - 常用于日志审计、监控埋点、调试追踪。
            - 在 _process() 中于类型处理器后执行。
        """
        if handler not in self._general_handlers:
            self._general_handlers.append(handler)

    def _run(self):
        """
        职责:
            后台循环消费事件队列，并触发事件分发。

        参数:
            无

        返回:
            None

        异常:
            queue.Empty 会被吞掉用于轮询继续；其他异常由 _process 内部处理。

        调用关系:
            - 由 start() 启动的后台线程执行。
            - 每次取到事件后调用 _process()。
        """
        while self._active:
            try:
                event = self._queue.get(block=True, timeout=0.2)
                self._process(event)
            except Empty:
                pass

    def _run_timer(self):
        """
        职责:
            定时投递 TIMER 事件，为策略轮询等逻辑提供统一心跳。

        参数:
            无

        返回:
            None

        异常:
            无显式抛出；time.sleep 异常极少见，通常由运行时处理。

        调用关系:
            - 由 start() 启动的定时线程执行。
            - 通过 put(Event(EventType.TIMER)) 注入心跳事件。
        """
        import time
        while self._active:
            time.sleep(self._interval)
            self.put(Event(EventType.TIMER))

    def _process(self, event: Event):
        """
        职责:
            将单个事件分发给类型处理器与全局处理器，并统一处理异常上报。

        参数:
            event (Event): 待分发事件。

        返回:
            None

        异常:
            不向外抛出业务 handler 异常；异常被转换为 LOG 事件并尝试分发。

        调用关系:
            - 由 _run() 在取到事件后调用。
            - 内部调用已注册的 subscribe()/subscribe_all() 处理器。
        """
        for handler in self._handlers.get(event.type, []):
            try:
                handler(event)
            except Exception as e:
                err_event = Event(EventType.LOG, LogData(
                    msg=f"[EventEngine] handler error type={event.type.value} handler={getattr(handler, '__qualname__', repr(handler))} err={e}\n{traceback.format_exc()}",
                    level='ERROR'))
                for h in self._handlers.get(EventType.LOG, []):
                    try:
                        h(err_event)
                    except Exception:
                        logger.exception('[EventEngine] LOG handler failed')
        for handler in self._general_handlers:
            try:
                handler(event)
            except Exception:
                err_event = Event(EventType.LOG, LogData(
                    msg=f"[EventEngine] general handler error type={event.type.value} handler={getattr(handler, '__qualname__', repr(handler))}\n{traceback.format_exc()}",
                    level='ERROR'))
                for h in self._handlers.get(EventType.LOG, []):
                    try:
                        h(err_event)
                    except Exception:
                        logger.exception('[EventEngine] LOG handler failed')


def test_event_engine_basic():
    """
    简单自测:
    1) 指定类型事件可被对应 handler 收到
    2) 全局 handler 可收到所有事件
    3) handler 抛错后会转为 LOG 事件
    4) unsubscribe 后不再收到对应事件
    """
    import time
    from threading import Event as ThreadEvent
    engine = EventEngine(interval=0.05)
    got_signal = ThreadEvent()
    got_log = ThreadEvent()
    result = {'signal_payload': None, 'general_event_types': [], 'log_messages': [], 'unsub_called_count': 0}

    def on_signal(event: Event):
        result['signal_payload'] = event.data
        got_signal.set()

    def on_signal_bad(event: Event):
        raise RuntimeError('intentional test error')

    def on_log(event: Event):
        if event.data:
            result['log_messages'].append(event.data.msg)
        got_log.set()

    def on_general(event: Event):
        result['general_event_types'].append(event.type.value)

    def on_tick_once(event: Event):
        result['unsub_called_count'] += 1

    engine.subscribe(EventType.SIGNAL, on_signal)
    engine.subscribe(EventType.SIGNAL, on_signal_bad)
    engine.subscribe(EventType.LOG, on_log)
    engine.subscribe(EventType.TICK, on_tick_once)
    engine.subscribe_all(on_general)
    engine.start()
    try:
        payload = {'symbol': '000001.SZ', 'action': 'buy', 'volume': 100}
        engine.put(Event(EventType.SIGNAL, payload))
        assert got_signal.wait(1.0), 'SIGNAL handler 未在预期时间内收到事件'
        assert result['signal_payload'] == payload, 'SIGNAL handler 输出数据不一致'
        deadline = time.time() + 1.0
        while EventType.SIGNAL.value not in result['general_event_types'] and time.time() < deadline:
            time.sleep(0.01)
        assert EventType.SIGNAL.value in result['general_event_types'], 'general handler 未收到 SIGNAL'
        assert got_log.wait(1.0), '异常未转换为 LOG 事件'
        assert any(('intentional test error' in msg for msg in result['log_messages'])), 'LOG 内容未包含原始异常'
        engine.unsubscribe(EventType.TICK, on_tick_once)
        engine.put(Event(EventType.TICK, {'symbol': '000001.SZ'}))
        time.sleep(0.2)
        assert result['unsub_called_count'] == 0, 'unsubscribe 后 handler 仍被触发'
    finally:
        engine.stop()


if __name__ == '__main__':
    test_event_engine_basic()
