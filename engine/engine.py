# alphaQuantSystem/engine/engine.py
"""Unified strategy engine — the single runtime engine for the entire framework"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Type

from loguru import logger

from alphaQuantSystem.core import EventEngine, Event, EventType, BarData, TradeData, OrderData, ScheduleEvent, Direction
from alphaQuantSystem.monitor.trace import trace
from alphaQuantSystem.core.context import StrategyContext, PortfolioView, PositionView
from alphaQuantSystem.engine.backtest_data_feed import BacktestDataFeed
from alphaQuantSystem.engine.execution import ExecutionHandler, BacktestExecution, LiveExecution
from alphaQuantSystem.engine.live_data_feed import LiveDataFeed
from alphaQuantSystem.engine.schedule import Schedule
from alphaQuantSystem.engine.signal_pipeline import SignalPipeline
from alphaQuantSystem.services.account import AccountService
from alphaQuantSystem.services.position import PositionService
from alphaQuantSystem.risk.policies import RiskAction
from alphaQuantSystem.risk.risk_gateway import RiskGateway
from alphaQuantSystem.risk.risk_limits import RiskLimits
from alphaQuantSystem.services.risk import GatewayRiskAdapter
from alphaQuantSystem.strategy.template import BaseStrategy


@dataclass
class StrategyReg:
    """Strategy registration entry"""
    strategy: Optional[BaseStrategy] = None
    strategy_cls: Optional[Type[BaseStrategy]] = None
    symbols: List[str] = field(default_factory=list)
    warmup_bars: int = 0
    period: str = "D"
    strategy_id: str = ""
    risk: Optional[Dict[str, Any]] = None
    schedule: Optional[Dict[str, str]] = None
    params: Dict[str, Any] = field(default_factory=dict)
    pipeline: Optional[SignalPipeline] = None
    risk_gateway: Optional[RiskGateway] = None


class StrategyEngine:
    """Unified runtime engine. _run_backtest() / _run_live()."""

    def __init__(self):
        self._strategy_regs: List[StrategyReg] = []
        self._position_svc = PositionService()
        self._account_svc = AccountService()
        self._backtest_feed: Optional[BacktestDataFeed] = None
        self._live_feed: Optional[LiveDataFeed] = None
        self._event_engine: Optional[EventEngine] = None
        self._schedule: Optional[Schedule] = None
        self._backtest_reporter = None
        self._signal_pipeline: Optional[SignalPipeline] = None
        self._execution_handler: Optional[ExecutionHandler] = None
        self._running = False
        self._qmt_gateway = None

    def register(
        self,
        strategy_cls: Type[BaseStrategy],
        *,
        params: Optional[Dict[str, Any]] = None,
        symbols: Optional[List[str]] = None,
        strategy_id: Optional[str] = None,
        warmup_bars: int = 0,
        period: str = "D",
        risk: Optional[Dict[str, Any]] = None,
        schedule: Optional[Dict[str, str]] = None,
    ) -> None:
        sid = strategy_id or strategy_cls.__name__
        if schedule:
            sch = Schedule()
            sch.validate_handler(strategy_cls, schedule)
        self._strategy_regs.append(StrategyReg(
            strategy_cls=strategy_cls,
            symbols=symbols or [],
            warmup_bars=warmup_bars,
            period=period,
            strategy_id=sid,
            risk=risk,
            schedule=schedule,
            params=params or {},
        ))

    def _create_strategy_instances(self, mode: str) -> None:
        """Create strategy instances + ctx for each registration"""
        for reg in self._strategy_regs:
            ctx = StrategyContext()
            ctx._mode = mode
            ctx._position_svc = self._position_svc
            ctx._account_svc = self._account_svc
            strategy = reg.strategy_cls(ctx)
            for k, v in reg.params.items():
                if hasattr(strategy, k):
                    setattr(strategy, k, v)
            reg.strategy = strategy

    def _refresh_ctx(self, ctx: StrategyContext, event_time: datetime) -> None:
        """Refresh ctx time and portfolio"""
        ctx.current_dt = event_time
        new_date = event_time.date() if hasattr(event_time, 'date') else date.today()
        if new_date != ctx.current_date:
            ctx.previous_date = ctx.current_date
            ctx.current_date = new_date

        positions = {}
        total_mv = 0.0
        for sym, pos in self._position_svc.snapshot().items():
            pv = PositionView(
                total_amount=pos.total_amount,
                closeable_amount=pos.closeable_amount,
                avg_cost=pos.avg_price,
                price=pos.current_price,
                value=pos.market_value,
                pnl=pos.pnl,
            )
            positions[sym] = pv
            total_mv += pos.market_value

        ctx.portfolio = PortfolioView(
            positions=positions,
            available_cash=self._account_svc.available,
            locked_cash=self._account_svc.locked,
            total_value=self._account_svc.total_value(total_mv),
            positions_value=total_mv,
            starting_cash=self._account_svc.starting_cash,
            daily_pnl=self._account_svc.daily_pnl,
            cumulative_pnl=self._account_svc.cumulative_pnl,
        )

    def _process_bar(self, reg: StrategyReg, bar: BarData, monitor_when: str = "each_bar") -> None:
        """每 bar 处理（回测/实盘共用），遵循先风控、后策略顺序（§6.0）。

        1. 风控监控 → pipeline.drain()    # 强平卖出优先落实
        2. increment_period（仅策略周期 bar 调用，分钟监控 bar 不递增）
        3. 策略信号 → pipeline.drain()    # 队列内先卖后买
        """
        strategy = reg.strategy
        trace(
            "Engine", "process_bar",
            strategy=reg.strategy_id, symbol=bar.symbol, time=bar.event_time,
            period=bar.interval, monitor_when=monitor_when,
        )
        # 1. 风控监控（先于策略买卖）
        self._run_post_bar_risk_monitoring(reg, bar, monitor_when=monitor_when)
        if reg.pipeline is not None:
            reg.pipeline.drain(bar=bar)
        # increment_period 仅策略周期 bar 递增（§6.1），分钟风控 bar 不递增
        gateway = reg.risk_gateway
        if gateway is not None:
            pos = self._position_svc.get(bar.symbol)
            if pos is not None and pos.volume > 0:
                gateway.calc_layer.increment_period(bar.symbol)
                trace("Engine", "increment_period", symbol=bar.symbol, periods=gateway.calc_layer._entry_periods.get(bar.symbol))
        # 2. 策略 on_bar
        self._refresh_ctx(strategy.ctx, bar.event_time)
        strategy.on_bar(bar)
        if reg.pipeline is not None:
            reg.pipeline.drain(bar=bar)

    def _sync_risk_gateway(self, reg: StrategyReg, bar: BarData, symbols: Optional[List[str]] = None) -> None:
        """将账户/持仓/行情同步到 RiskGateway DataLayer。

        Args:
            symbols: 仅同步这些标的的持仓（分钟风控时只同步当前 bar 标的，其余复用上次快照）。
                     为 None 时同步全部持仓。
        """
        gateway = reg.risk_gateway
        if gateway is None:
            return
        if bar.symbol in self._position_svc.snapshot():
            self._position_svc.update_price(bar.symbol, bar.close)
        sync_syms = set(symbols) if symbols is not None else set(self._position_svc.snapshot().keys())
        sync_syms.add(bar.symbol)
        for sym, pos in self._position_svc.snapshot().items():
            if sym not in sync_syms:
                continue
            if pos.volume > 0:
                gateway.sync_position(
                    sym, Direction.LONG, pos.volume, pos.avg_price, pos.pnl,
                )
            else:
                gateway.clear_position(sym)
        bar_pos = self._position_svc.get(bar.symbol)
        if bar_pos is None or bar_pos.volume <= 0:
            gateway.clear_position(bar.symbol)
        total_mv = self._position_svc.total_market_value()
        gateway.sync_account(
            balance=self._account_svc.total_value(total_mv),
            available=self._account_svc.available,
            frozen=self._account_svc.locked,
        )
        gateway.data_layer.update_bar(bar)

    def _on_pipeline_trade(self, reg: StrategyReg, trade: TradeData, bar: Optional[BarData]) -> None:
        """成交后通知风控层更新开仓/平仓状态与频控计数。"""
        side = "BUY" if trade.direction == Direction.LONG else "SELL"
        trace(
            "Engine", "trade callback",
            side=side, symbol=trade.symbol, vol=trade.volume, price=trade.price, tag=trade.tag,
        )
        gateway = reg.risk_gateway
        if gateway is None:
            return
        ts = bar.event_time if bar is not None else trade.event_time
        if trade.direction == Direction.LONG:
            pos = self._position_svc.get(trade.symbol)
            entry_px = pos.avg_price if pos is not None else trade.price
            total_mv = self._position_svc.total_market_value()
            gateway.on_strategy_open(
                trade.symbol, entry_px, ts,
                equity=self._account_svc.total_value(total_mv),
            )
        else:
            entry_px = gateway.calc_layer._entry_prices.get(trade.symbol, trade.price)
            pnl = (trade.price - entry_px) * trade.volume
            gateway.on_strategy_close(trade.symbol, pnl, ts)
            if trade.tag == "risk_close_drawdown":
                total_mv = self._position_svc.total_market_value()
                gateway.on_max_drawdown_close(
                    trade.symbol,
                    self._account_svc.total_value(total_mv),
                )
        gateway.record_trade(ts)

    def _run_post_bar_risk_monitoring(self, reg: StrategyReg, bar: BarData, monitor_when: str = "each_bar") -> None:
        """回测/实盘：Bar 结束后做持仓监控，触发止盈止损等强平信号。

        Args:
            monitor_when: 监控时机 — "each_bar" | "day_end"

        注意：increment_period 仅策略周期 bar 调用，分钟风险 bar 上不递增持仓周期（§6.1）。
        """
        gateway = reg.risk_gateway
        if gateway is None or bar.symbol not in reg.symbols:
            return
        self._sync_risk_gateway(reg, bar)

        # increment_period 在策略周期 bar 上由 _process_bar 调用方决定；
        # 分钟监控 bar 不调用（由外部控制），此处不再自动递增。

        # 账户级回撤等规则空仓时也需评估；清仓类动作仅在有持仓时执行
        events = gateway.check_monitoring_risk(bar.symbol, reg.strategy_id, monitor_when=monitor_when)
        trace(
            "Risk", "monitor",
            symbol=bar.symbol, time=bar.event_time, when=monitor_when,
            events=len(events), holding=self._position_svc.get(bar.symbol).volume if self._position_svc.get(bar.symbol) else 0,
        )
        strategy = reg.strategy
        for event in events:
            trace("Risk", "event", action=event.action, rule=event.rule_id, reason=event.reason)
            if event.action == RiskAction.FULL_CLOSE:
                pos = self._position_svc.get(bar.symbol)
                if pos is None or pos.volume <= 0:
                    continue
                close_tag = (
                    "risk_close_drawdown"
                    if "max_drawdown" in event.rule_id
                    else "risk_close"
                )
                strategy.sell(
                    bar.symbol, pos.closeable_amount, price=bar.close,
                    reason=f"风控:{event.reason}", tag=close_tag,
                )
            elif event.action == RiskAction.PARTIAL_CLOSE:
                pos = self._position_svc.get(bar.symbol)
                if pos is None or pos.volume <= 0:
                    continue
                sell_vol = pos.closeable_amount * event.close_ratio
                if sell_vol > 0:
                    strategy.sell(
                        bar.symbol, sell_vol, price=bar.close,
                        reason=f"风控:{event.reason}", tag="risk_partial",
                    )

    def _process_warmup_bar(self, reg: StrategyReg, bar: BarData) -> None:
        """Warmup bar: only on_warmup_bar, signals discarded"""
        strategy = reg.strategy
        strategy.ctx._warmup = True
        self._refresh_ctx(strategy.ctx, bar.event_time)
        strategy.on_warmup_bar(bar)

    # ── 双周期风控辅助 ──

    @staticmethod
    def _resolve_monitor_config(reg: StrategyReg) -> Optional[Dict[str, Any]]:
        """解析策略的风控监控配置。None 表示跟随策略周期。"""
        if reg.risk is None:
            return None
        monitor = reg.risk.get("monitor")
        if monitor is None or not isinstance(monitor, dict):
            return None
        return monitor

    @classmethod
    def _resolve_monitor_period(cls, reg: StrategyReg) -> str:
        """解析风控监控周期；未配置时回退到策略周期。"""
        monitor = cls._resolve_monitor_config(reg)
        if monitor is None:
            return reg.period
        return monitor.get("period", reg.period)

    @classmethod
    def _resolve_monitor_price(cls, reg: StrategyReg) -> str:
        """解析风控盯市价格字段；未配置时默认 close。"""
        monitor = cls._resolve_monitor_config(reg)
        if monitor is None:
            return "close"
        return monitor.get("price", "close")

    @classmethod
    def _resolve_monitor_when(cls, reg: StrategyReg) -> str:
        """解析风控监控时机；未配置时默认 each_bar。"""
        monitor = cls._resolve_monitor_config(reg)
        if monitor is None:
            return "each_bar"
        return monitor.get("when", "each_bar")

    @classmethod
    def _needs_intraday_risk(cls, reg: StrategyReg) -> bool:
        """判断策略是否需要日内风控（monitor.period 细于 strategy.period）。"""
        monitor_period = cls._resolve_monitor_period(reg)
        # 分钟级周期（1m/5m/15m 等）属于日内；D/W/M 非日内
        intraday_periods = {"1m", "1", "5m", "5", "15m", "15", "30m", "30", "60m", "60", "1h"}
        strat_is_daily = reg.period in ("D", "W", "M")
        monitor_is_intraday = monitor_period in intraday_periods
        return strat_is_daily and monitor_is_intraday

    @staticmethod
    def _is_last_minute_bar_of_day(bar: BarData, all_bars_for_day: List[BarData]) -> bool:
        """判断当前分钟 bar 是否为当日的最后一根（按 symbol 分组时使用最大时间）。"""
        if not all_bars_for_day:
            return True
        max_time = max(b.event_time for b in all_bars_for_day)
        return bar.event_time >= max_time

    def _is_day_end(self, bar: BarData) -> bool:
        return bar.interval in ("1d", "D")

    def _process_intraday_risk(self, reg: StrategyReg, bar: BarData) -> None:
        """分钟级别风控监控（不调用策略 on_bar、不递增 holding_periods，§6.1）。

        仅同步行情 + 评估风控 → 下发强平信号 → drain。
        """
        gateway = reg.risk_gateway
        if gateway is None or bar.symbol not in reg.symbols:
            return
        trace("Engine", "intraday_risk", symbol=bar.symbol, time=bar.event_time, period=bar.interval)
        self._sync_risk_gateway(reg, bar)
        # 分钟 bar 上不调用 increment_period（holding_periods 仍按策略周期计数）
        events = gateway.check_monitoring_risk(bar.symbol, reg.strategy_id, monitor_when="each_bar")
        if events:
            trace("Risk", "intraday events", symbol=bar.symbol, count=len(events))
        strategy = reg.strategy
        for event in events:
            trace("Risk", "intraday event", action=event.action, rule=event.rule_id, reason=event.reason)
            if event.action == RiskAction.FULL_CLOSE:
                pos = self._position_svc.get(bar.symbol)
                if pos is None or pos.volume <= 0:
                    continue
                close_tag = (
                    "risk_close_drawdown"
                    if "max_drawdown" in event.rule_id
                    else "risk_close"
                )
                strategy.sell(
                    bar.symbol, pos.closeable_amount, price=bar.close,
                    reason=f"风控:{event.reason}", tag=close_tag,
                )
            elif event.action == RiskAction.PARTIAL_CLOSE:
                pos = self._position_svc.get(bar.symbol)
                if pos is None or pos.volume <= 0:
                    continue
                sell_vol = pos.closeable_amount * event.close_ratio
                if sell_vol > 0:
                    strategy.sell(
                        bar.symbol, sell_vol, price=bar.close,
                        reason=f"风控:{event.reason}", tag="risk_partial",
                    )
        if reg.pipeline is not None:
            reg.pipeline.drain(bar=bar)

    def _process_intraday_risk_for_day(
        self, reg: StrategyReg, trade_date, day_bars: List[BarData],
    ) -> None:
        """对单个交易日的所有 1m bars 运行风控监控（阶段一，§6.1）。

        仅评估前日及以前形成的持仓；当日新仓在阶段二买入，不参与当日分钟监控。
        """
        monitor_period = self._resolve_monitor_period(reg)
        for symbol in reg.symbols:
            for bar_1m in self._backtest_feed.iter_intraday_bars(symbol, trade_date, monitor_period):
                self._process_intraday_risk(reg, bar_1m)
        # 分钟数据用完后释放当日缓存
        self._backtest_feed.clear_intraday_cache()

    def _settle_backtest_day(
        self,
        reporter: "BacktestReporter",
        day_bars: List[BarData],
        trade_date: date,
    ) -> None:
        """日终结算：先更新当日全部标的收盘价，再写入一条账户快照。"""
        if not day_bars or not self._is_day_end(day_bars[0]):
            return
        for bar in day_bars:
            self._position_svc.update_price(bar.symbol, bar.close)
        mv = self._position_svc.total_market_value()
        reporter.set_daily_state(self._account_svc.cash, mv)
        reporter.on_day_end(trade_date)

    def _ensure_event_engine(self) -> EventEngine:
        """获取或创建同步模式事件引擎（实盘主循环 poll/dispatch，无后台竞争线程）。"""
        if self._event_engine is None:
            self._event_engine = EventEngine(sync_mode=True)
            self._event_engine.start()
        return self._event_engine

    @staticmethod
    def _is_risk_enabled(reg: StrategyReg) -> bool:
        """策略级风控开关：risk={\"enabled\": False} 时跳过全部风控。"""
        if reg.risk is not None and reg.risk.get("enabled") is False:
            return False
        return True

    def _create_pipeline_risk(
        self,
        reg: StrategyReg,
        scene: str,
    ) -> Optional[GatewayRiskAdapter]:
        """按策略配置创建风控适配器；关闭时返回 None。"""
        if not self._is_risk_enabled(reg):
            reg.risk_gateway = None
            return None
        return self._create_gateway_risk(reg, scene)

    def _create_gateway_risk(
        self,
        reg: StrategyReg,
        scene: str,
    ) -> GatewayRiskAdapter:
        """为策略注册六层 RiskGateway，并适配到 SignalPipeline。"""
        limits = RiskLimits()
        limits.update_limits({"scene": scene})
        if reg.risk:
            limits.update_limits({k: v for k, v in reg.risk.items() if k != "enabled"})
        gateway = RiskGateway(self._ensure_event_engine(), limits)
        reg.risk_gateway = gateway
        return GatewayRiskAdapter(
            gateway=gateway,
            position_svc=self._position_svc,
            account_svc=self._account_svc,
        )

    # ── Backtest ──

    def _run_backtest(
        self, start: str, end: str, initial_cash: float,
        *,
        commission_rate: float = 0.0003,
        slippage: float = 0.0,
        hist_source: Optional[str] = None,
    ) -> "BacktestResult":
        from alphaQuantSystem.backtest.matching_engine import MatchingEngine
        from alphaQuantSystem.backtest.commission import ETFCommission, AShareCommission
        from alphaQuantSystem.backtest.report import BacktestReporter

        self._create_strategy_instances("backtest")
        self._position_svc.reset()
        self._account_svc = AccountService(initial_cash)
        self._event_engine = None

        max_warmup = max((r.warmup_bars for r in self._strategy_regs), default=0)
        all_symbols = sorted({s for r in self._strategy_regs for s in r.symbols})
        period = self._strategy_regs[0].period if self._strategy_regs else "D"

        self._backtest_feed = BacktestDataFeed()
        self._backtest_feed.subscribe(
            symbols=all_symbols, period=period,
            start=start, end=end, warmup_bars=max_warmup,
            source=hist_source,
        )
        if not self._backtest_feed._hist_data:
            raise RuntimeError(
                f"回测行情为空，请检查标的代码与数据源: symbols={all_symbols}, source={hist_source!r}"
            )

        is_etf = all(len(s.split(".")[0]) >= 2 and s.split(".")[0][:2] in ("51", "15", "16", "56", "58") for s in all_symbols)
        if is_etf:
            comm_model = ETFCommission(commission_rate=commission_rate, min_commission=0.0)
        else:
            comm_model = AShareCommission(commission_rate=commission_rate, stamp_duty_rate=0.0, min_commission=0.0)

        reporter = BacktestReporter(commission_model=comm_model, initial_cash=initial_cash)
        matcher = MatchingEngine(commission_model=comm_model, slippage=slippage)
        backtest_exec = BacktestExecution(
            matcher=matcher,
            position_svc=self._position_svc,
            account_svc=self._account_svc,
            reporter=reporter,
        )

        for reg in self._strategy_regs:
            risk = self._create_pipeline_risk(reg, scene="backtest")
            pipeline = SignalPipeline(execution=backtest_exec, risk=risk)
            pipeline.set_strategy(reg.strategy)
            pipeline.set_trade_callback(lambda t, b, r=reg: self._on_pipeline_trade(r, t, b))
            reg.strategy.set_pipeline(pipeline)
            reg.pipeline = pipeline
            reg.strategy.ctx._reporter = reporter
            reg.strategy.ctx._mode = "backtest"
            if reg.risk_gateway is not None:
                reg.risk_gateway.set_equity_baseline(initial_cash)

        trace("Engine", "backtest start", start=start, end=end, symbols=all_symbols, warmup=max_warmup)

        # Warmup
        for reg in self._strategy_regs:
            reg.strategy.on_init()

        for period_idx, bars in enumerate(self._backtest_feed.iter_warmup_periods()):
            for reg in self._strategy_regs:
                if period_idx >= max_warmup - reg.warmup_bars:
                    for bar in bars:
                        if bar.symbol in reg.symbols:
                            self._process_warmup_bar(reg, bar)

        trace("Engine", "warmup done, on_start")

        # on_start
        for reg in self._strategy_regs:
            reg.strategy.ctx._warmup = False
            reg.strategy.on_start()

        # Main loop — 按交易日分组
        # D 周期策略 + 分钟风控：日内风控在日 K 策略之前（§6.1）
        # D 周期策略 + D 风控 / 1m 策略 + 1m 风控：每 bar 先风控后策略（§6.0）
        pending_day_bars: List[BarData] = []
        pending_day: Optional[date] = None
        intraday_done: Dict[int, date] = {}  # id(reg) → 已完成日内风控的交易日

        for bar in self._backtest_feed.iter_bars():
            bar_day = bar.event_time.date()
            if pending_day is not None and bar_day != pending_day:
                # 日切换：结算前一日
                trace("Engine", "day settle", date=pending_day)
                self._settle_backtest_day(reporter, pending_day_bars, pending_day)
                pending_day_bars = []
            pending_day = bar_day
            pending_day_bars.append(bar)

            # ── 阶段一：日内风控（先于当日一切策略买卖，§6.1）──
            for reg in self._strategy_regs:
                if bar.symbol not in reg.symbols:
                    continue
                reg_key = id(reg)
                if (
                    self._needs_intraday_risk(reg)
                    and intraday_done.get(reg_key) != bar_day
                ):
                    trace("Engine", "phase1 intraday risk", date=bar_day, strategy=reg.strategy_id)
                    self._process_intraday_risk_for_day(reg, bar_day, pending_day_bars)
                    intraday_done[reg_key] = bar_day

            # ── 阶段二：日 K / 分钟 bar（先风控后策略，§6.0）──
            # D 周期 bar 的 monitor_when 固定为 "day_end"（每根日 K 天然是当日最后一根）
            for reg in self._strategy_regs:
                if bar.symbol in reg.symbols:
                    self._process_bar(reg, bar, monitor_when="day_end")

        if pending_day is not None and pending_day_bars:
            self._settle_backtest_day(reporter, pending_day_bars, pending_day)

        trace("Engine", "backtest loop done, on_stop")

        # on_stop
        for reg in self._strategy_regs:
            reg.strategy.on_stop()

        result = reporter.build_result()
        result.start_date = start
        result.end_date = end
        result.initial_cash = initial_cash
        result.commission_model = comm_model
        result.strategy_id = (
            self._strategy_regs[0].strategy_id
            if self._strategy_regs and self._strategy_regs[0].strategy_id
            else (self._strategy_regs[0].strategy_cls.__name__ if self._strategy_regs else "backtest")
        )
        return result

    def _sync_live_broker_state(self) -> None:
        """实盘：从 QMT 同步资金与持仓到 Service 层。"""
        gateway = self._qmt_gateway
        if gateway is None:
            return
        acc = gateway.get_account_snapshot()
        self._account_svc.set_cash(float(acc.get("cash", 0) or 0))
        self._position_svc.sync_from_broker(gateway.get_all_position_snapshots())

    def _live_sync_and_refresh(self, reg: "StrategyReg") -> None:
        """实盘：同步券商状态并刷新策略 ctx 快照。"""
        self._sync_live_broker_state()
        self._refresh_ctx(reg.strategy.ctx, datetime.now())

    # ── Live ──

    def _run_live(self, *, qmt_is_live: bool = False, slippage: float = 0.0) -> None:
        from alphaQuantSystem.data import DataEngine
        from alphaQuantSystem.gateway.qmt_gateway import QmtGateway
        from alphaQuantSystem.trader.qmt_trader import QmtTrader

        self._create_strategy_instances("live")
        self._event_engine = None
        self._position_svc.reset()

        ee = self._ensure_event_engine()
        gateway = QmtGateway(ee, is_real=qmt_is_live)
        gateway.connect()
        self._qmt_gateway = gateway
        QmtTrader(ee, gateway, initial_capital=self._account_svc.starting_cash, slippage=slippage or 0.01)

        data_engine = DataEngine()
        all_symbols = sorted({s for r in self._strategy_regs for s in r.symbols})

        self._live_feed = LiveDataFeed(mode="push_with_poll_fallback")
        self._live_feed.subscribe(all_symbols, period="1m")
        self._schedule = Schedule()

        for reg in self._strategy_regs:
            reg.strategy.ctx._data_engine = data_engine
            reg.strategy.ctx._position_svc = self._position_svc
            reg.strategy.ctx._account_svc = self._account_svc
            live_exec = LiveExecution(event_engine=ee, gateway=gateway)
            risk = self._create_pipeline_risk(reg, scene="live")
            pipeline = SignalPipeline(execution=live_exec, risk=risk)
            pipeline.set_strategy(reg.strategy)
            pipeline.set_trade_callback(lambda t, b, r=reg: self._on_pipeline_trade(r, t, b))
            reg.strategy.set_pipeline(pipeline)
            reg.pipeline = pipeline
            reg.strategy.ctx._mode = "live"
            if reg.risk_gateway is not None:
                self._sync_live_broker_state()
                reg.risk_gateway.set_equity_baseline(self._account_svc.total_value(
                    self._position_svc.total_market_value()
                ))
            if reg.schedule:
                for time_str, method_name in reg.schedule.items():
                    self._schedule.at(time_str, method_name)

        ee.subscribe(EventType.BAR, self._on_live_bar)
        ee.subscribe(EventType.SCHEDULE, self._on_live_schedule)
        ee.subscribe(EventType.TRADE, self._on_live_trade)
        ee.subscribe(EventType.ORDER, self._on_live_order)

        self._sync_live_broker_state()
        acc = gateway.get_account_snapshot()
        logger.info(
            "[Engine] QMT account={} cash={:.2f} is_live={}",
            gateway.account_id,
            float(acc.get("cash", 0) or 0),
            qmt_is_live,
        )

        for reg in self._strategy_regs:
            reg.strategy.ctx._live_sync_broker = lambda r=reg: self._live_sync_and_refresh(r)
            reg.strategy.on_init()
            reg.strategy.ctx._warmup = False
            reg.strategy.on_start()

        self._live_feed.set_callback(lambda bar: ee.put(Event(EventType.BAR, bar)))
        self._live_feed.start()

        def _emit_schedule(se: ScheduleEvent) -> None:
            ee.put(Event(EventType.SCHEDULE, se))

        self._schedule.start(_emit_schedule, skip_past_today=True)

        logger.info(
            "[Engine] Live mode started | qmt_is_live={} | symbols={}",
            qmt_is_live,
            len(all_symbols),
        )
        self._running = True
        try:
            while self._running:
                try:
                    event = ee.poll()
                    if event is not None:
                        ee.dispatch(event)
                    else:
                        import time
                        time.sleep(0.01)
                except Exception:
                    logger.exception("StrategyEngine live loop error")
        except KeyboardInterrupt:
            logger.info("Received exit signal")
        finally:
            for reg in self._strategy_regs:
                reg.strategy.on_stop()
            if self._schedule is not None:
                self._schedule.stop()
            if self._live_feed is not None:
                self._live_feed.stop()
            if gateway is not None:
                gateway.disconnect()
            ee.stop()

    def _on_live_bar(self, event: Event) -> None:
        self._sync_live_broker_state()
        bar: BarData = event.data
        for reg in self._strategy_regs:
            if bar.symbol in reg.symbols:
                self._process_bar(reg, bar)

    def _on_live_schedule(self, event: Event) -> None:
        self._sync_live_broker_state()
        se: ScheduleEvent = event.data
        for reg in self._strategy_regs:
            if reg.schedule:
                for time_str, method_name in reg.schedule.items():
                    if method_name == se.handler_name and time_str == se.time:
                        strategy = reg.strategy
                        self._refresh_ctx(strategy.ctx, datetime.now())
                        getattr(strategy, method_name)()
                        if reg.pipeline is not None:
                            reg.pipeline.drain()
                        break

    def _on_live_trade(self, event: Event) -> None:
        trade: TradeData = event.data
        trade_day = (
            trade.event_time.strftime("%Y-%m-%d")
            if hasattr(trade.event_time, "strftime")
            else str(trade.event_time)[:10]
        )
        side = "买入" if trade.direction == Direction.LONG else "卖出"
        logger.info(
            f"[成交] {trade_day} {side} {trade.symbol} "
            f"{trade.volume:.0f}股@{trade.price:.3f}"
        )
        self._position_svc.apply_trade(trade)
        for reg in self._strategy_regs:
            reg.strategy.on_trade(trade)

    def _on_live_order(self, event: Event) -> None:
        order: OrderData = event.data
        for reg in self._strategy_regs:
            reg.strategy.on_order(order)

    # ── Run ──

    def run(
        self,
        mode: str,
        *,
        start: Optional[str] = None,
        end: Optional[str] = None,
        initial_cash: float = 1_000_000,
        commission_rate: float = 0.0003,
        slippage: float = 0.0,
        hist_source: Optional[str] = None,
        qmt_is_live: bool = False,
    ):
        if mode == "backtest":
            if not start or not end:
                raise ValueError("backtest requires start/end parameters")
            return self._run_backtest(
                start=start, end=end, initial_cash=initial_cash,
                commission_rate=commission_rate, slippage=slippage,
                hist_source=hist_source,
            )
        elif mode == "live":
            return self._run_live(qmt_is_live=qmt_is_live, slippage=slippage)
        else:
            raise ValueError(f"Unknown mode: {mode}")
