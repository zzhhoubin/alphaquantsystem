"""
策略引擎 —— 多策略管理，定时推送 BAR 事件
"""
from typing import Any, Dict, List, Optional, Type
from loguru import logger
from alphaQuantSystem.core import EventEngine, Event, EventType, BarData
from alphaQuantSystem.data.data_engine import DataEngine
from .bar_utils import bar_from_ohlcv_row
from .template import BaseStrategy


class StrategyEngine:
    """
    策略引擎
    - 注册/启动/停止多个策略
    - 定时拉取 K 线数据并推送 BAR 事件
    - 支持手动触发 on_bar（用于回测或定时任务）
    """

    def __init__(self, event_engine: EventEngine, stock_pool_config: Optional[Dict[str, Any]] = None):
        """
        职责:
            初始化策略引擎，持有事件总线、数据引擎与策略注册表。

        参数:
            event_engine (EventEngine): 全局事件总线实例。

        返回:
            None

        异常:
            无显式抛出；依赖初始化失败时异常透传。

        调用关系:
            - 由应用装配流程创建。
            - 后续通过 add_strategy/start_all 等方法驱动策略生命周期。
        """
        self.event_engine = event_engine
        self.data_engine = DataEngine()
        self._strategies: Dict[str, BaseStrategy] = {}
        pool_cfg = stock_pool_config or {}
        self.stock_pool_config: Dict[str, Any] = {'enabled': bool(pool_cfg.get('enabled', False)),
                                                  'symbols': list(pool_cfg.get('symbols', []) or []),
                                                  'period': pool_cfg.get('period', 'D'),
                                                  'start_date': pool_cfg.get('start_date', '20200101'),
                                                  'end_date': pool_cfg.get('end_date', '20500101'),
                                                  'hist_data_source': pool_cfg.get('hist_data_source')}

    def add_strategy(self, strategy: BaseStrategy):
        """
        职责:
            将策略实例注册到策略引擎，纳入统一管理。

        参数:
            strategy (BaseStrategy): 待注册策略实例。

        返回:
            None

        异常:
            无显式抛出；重复 strategy_id 会被警告并跳过。

        调用关系:
            - 通常在系统启动时调用。
            - 注册后可被 start_strategy/start_all 启动。
        """
        if strategy.strategy_id in self._strategies:
            logger.warning(f'策略 [{strategy.strategy_id}] 已存在，跳过')
            return
        self._strategies[strategy.strategy_id] = strategy
        logger.info(f'策略 [{strategy.strategy_id}] 已注册')

    def start_strategy(self, strategy_id: str):
        """
        职责:
            启动指定策略实例。

        参数:
            strategy_id (str): 目标策略 ID。

        返回:
            None

        异常:
            无显式抛出；策略不存在时仅记录错误日志。

        调用关系:
            - 由外层控制逻辑调用。
            - 内部调用策略对象的 start()。
        """
        strategy = self._strategies.get(strategy_id)
        if strategy:
            strategy.start()
        else:
            logger.error(f'策略 [{strategy_id}] 不存在')

    def stop_strategy(self, strategy_id: str):
        """
        职责:
            停止指定策略实例。

        参数:
            strategy_id (str): 目标策略 ID。

        返回:
            None

        异常:
            无显式抛出；策略不存在时静默忽略。

        调用关系:
            - 由停机流程或风控外层控制调用。
            - 内部调用策略对象的 stop()。
        """
        strategy = self._strategies.get(strategy_id)
        if strategy:
            strategy.stop()

    def start_all(self):
        """
        职责:
            启动全部已注册策略。

        参数:
            无

        返回:
            None

        异常:
            无显式抛出；单策略启动异常由策略自身处理。

        调用关系:
            - 常由应用启动流程调用。
            - 等价于遍历调用每个策略的 start()。
        """
        for s in self._strategies.values():
            s.start()

    def stop_all(self):
        """
        职责:
            停止全部已注册策略。

        参数:
            无

        返回:
            None

        异常:
            无显式抛出；单策略停止异常由策略自身处理。

        调用关系:
            - 常由应用退出流程调用。
            - 等价于遍历调用每个策略的 stop()。
        """
        for s in self._strategies.values():
            s.stop()

    def push_bar(self, bar: BarData):
        """
        职责:
            将单根 K 线包装为 BAR 事件并投递到事件总线。

        参数:
            bar (BarData): 待推送的 K 线对象。

        返回:
            None

        异常:
            无显式抛出；事件投递异常由 EventEngine.put 透传。

        调用关系:
            - 可由回测驱动、定时器任务或外部数据适配层调用。
            - 事件将被 BaseStrategy._on_bar_event 消费。
        """
        logger.debug('BAR 事件：StrategyEngine.push_bar 推送 K 线 symbol={}', getattr(bar, 'symbol', ''))
        self.event_engine.put(Event(EventType.BAR, bar))

    def fetch_and_push(self, symbol: str, period: str = 'D', start_date: str = '20200101', end_date: str = '20500101',
                       hist_data_source: Optional[str] = None):
        """
        职责:
            拉取指定区间历史 K 线并逐根投递 BAR 事件，驱动策略回放。

        参数:
            symbol (str): 标的代码。
            period (str): K 线周期，例如 "1m"/"D"。
            start_date (str): 起始日期，格式 YYYYMMDD。
            end_date (str): 结束日期，格式 YYYYMMDD。
            hist_data_source (Optional[str]): 历史 K 线数据来源，见 DataEngine.get_hist_data。

        返回:
            None

        异常:
            无显式抛出；数据拉取失败时记录 warning 并提前返回。

        调用关系:
            - 主要用于离线回放/回测驱动。
            - 内部依赖 DataEngine.get_hist_data() 与 push_bar()。
        """
        df = self.data_engine.get_hist_data(symbol, period, start_date, end_date, source=hist_data_source)
        if df.empty:
            logger.warning(f'fetch_and_push: {symbol} 无数据')
            return
        for _, row in df.iterrows():
            bar = bar_from_ohlcv_row(row, symbol, period)
            self.push_bar(bar)

    def fetch_and_push_pool(self, symbols: List[str], period: str = 'D', start_date: str = '20200101',
                            end_date: str = '20500101', hist_data_source: Optional[str] = None):
        """
        职责:
            按股票池批量拉取并推送 BAR，逐标的复用 fetch_and_push().
        """
        for symbol in symbols or []:
            symbol = str(symbol).strip()
            if not symbol:
                continue
            self.fetch_and_push(symbol, period=period, start_date=start_date, end_date=end_date,
                                hist_data_source=hist_data_source)

    def run_pool_once(self):
        """
        职责:
            执行一次全局股票池推送；禁用或空池时跳过。
        """
        cfg = self.stock_pool_config
        if not cfg.get('enabled', False):
            return
        symbols = [str(s).strip() for s in cfg.get('symbols', []) if str(s).strip()]
        if not symbols:
            logger.warning('股票池已启用但 symbols 为空，跳过 run_pool_once')
            return
        self.fetch_and_push_pool(symbols=symbols, period=cfg.get('period', 'D'),
                                 start_date=cfg.get('start_date', '20200101'), end_date=cfg.get('end_date', '20500101'),
                                 hist_data_source=cfg.get('hist_data_source'))

    def get_strategy(self, strategy_id: str) -> Optional[BaseStrategy]:
        """
        职责:
            根据策略 ID 查询策略实例。

        参数:
            strategy_id (str): 策略唯一标识。

        返回:
            Optional[BaseStrategy]: 命中返回策略实例，否则返回 None。

        异常:
            无显式抛出。

        调用关系:
            - 供外部控制层按 ID 获取策略状态或执行控制。
        """
        return self._strategies.get(strategy_id)

    def list_strategies(self) -> List[str]:
        """
        职责:
            返回当前已注册策略 ID 列表。

        参数:
            无

        返回:
            List[str]: 策略 ID 列表。

        异常:
            无显式抛出。

        调用关系:
            - 供监控、调试与控制面展示策略清单。
        """
        return list(self._strategies.keys())
