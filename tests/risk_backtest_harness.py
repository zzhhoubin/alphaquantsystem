"""DualEma 合成行情回测脚手架 —— 逐条验证风控条件。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Sequence

from alphaQuantSystem.backtest.commission import ETFCommission
from alphaQuantSystem.backtest.matching_engine import MatchingEngine
from alphaQuantSystem.backtest.report import BacktestReporter
from alphaQuantSystem.core import BarData, Direction, TickData, TradeData
from alphaQuantSystem.engine.engine import StrategyEngine
from alphaQuantSystem.engine.execution import BacktestExecution
from alphaQuantSystem.engine.signal_pipeline import SignalPipeline
from alphaQuantSystem.examples.dual_ema import DualEmaStrategy
from alphaQuantSystem.risk.risk_gateway import RiskGateway
from alphaQuantSystem.services.account import AccountService
from alphaQuantSystem.tests.risk_configs import isolated_risk


SYMBOL = "159509.SZ"
DEFAULT_START = datetime(2024, 1, 2)


@dataclass
class ScenarioResult:
    trades: List[TradeData] = field(default_factory=list)
    risk_blocks: List[str] = field(default_factory=list)
    final_holding: float = 0.0
    gateway: Optional[RiskGateway] = None
    reg: Any = None
    engine: Optional[StrategyEngine] = None


class RecordingDualEma(DualEmaStrategy):
    """记录成交与风控拦截，便于断言。"""

    def on_init(self) -> None:
        super().on_init()
        self.recorded_trades: List[TradeData] = []
        self.risk_blocks: List[str] = []

    def on_trade(self, trade: TradeData) -> None:
        self.recorded_trades.append(trade)
        super().on_trade(trade)

    def on_risk_block(self, reason: str, tag: Optional[str] = None, signal=None) -> None:
        self.risk_blocks.append(reason)
        super().on_risk_block(reason, tag=tag, signal=signal)


def make_bar(
    symbol: str,
    day_index: int,
    close: float,
    *,
    open_: Optional[float] = None,
    high: Optional[float] = None,
    low: Optional[float] = None,
    start: datetime = DEFAULT_START,
) -> BarData:
    o = open_ if open_ is not None else close
    h = high if high is not None else max(o, close) * 1.001
    l = low if low is not None else min(o, close) * 0.999
    return BarData(
        symbol=symbol,
        open=o,
        high=h,
        low=l,
        close=close,
        volume=1_000_000,
        event_time=start + timedelta(days=day_index),
        interval="D",
    )


def bars_from_closes(
    closes: Sequence[float],
    *,
    symbol: str = SYMBOL,
    start: datetime = DEFAULT_START,
    opens: Optional[Sequence[Optional[float]]] = None,
    same_day_from: Optional[int] = None,
) -> List[BarData]:
    """same_day_from: 从该索引起，后续 K 线使用同一交易日（便于测日级风控）。"""
    bars: List[BarData] = []
    for i, close in enumerate(closes):
        o = opens[i] if opens is not None and i < len(opens) and opens[i] is not None else (
            closes[i - 1] if i > 0 else close
        )
        day_index = i
        if same_day_from is not None and i >= same_day_from:
            day_index = same_day_from
        bars.append(make_bar(symbol, day_index, close, open_=o, start=start))
    return bars


def run_dual_ema_scenario(
    closes: Sequence[float],
    risk_overrides: Dict[str, Any],
    *,
    symbol: str = SYMBOL,
    warmup_bars: int = 12,
    initial_cash: float = 1_000_000,
    strategy_params: Optional[Dict[str, Any]] = None,
    opens: Optional[Sequence[Optional[float]]] = None,
    same_day_from: Optional[int] = None,
    on_before_monitor: Optional[Callable[[StrategyEngine, Any, BarData, RiskGateway], None]] = None,
) -> ScenarioResult:
    """用合成 K 线跑 DualEma + 单条风控，不依赖外部数据源。"""
    bars = bars_from_closes(
        closes, symbol=symbol, opens=opens, same_day_from=same_day_from,
    )
    if len(bars) <= warmup_bars:
        raise ValueError("bars 数量须大于 warmup_bars")

    engine = StrategyEngine()
    risk_cfg = isolated_risk(**risk_overrides)
    params = {"fast_period": 3, "slow_period": 5, "full_position": True}
    if strategy_params:
        params.update(strategy_params)

    engine.register(
        RecordingDualEma,
        symbols=[symbol],
        warmup_bars=warmup_bars,
        risk=risk_cfg,
        params=params,
        strategy_id="DualEmaRiskTest",
    )
    engine._create_strategy_instances("backtest")
    engine._position_svc.reset()
    engine._account_svc = AccountService(initial_cash)

    comm = ETFCommission(commission_rate=0.00005, min_commission=0.0)
    reporter = BacktestReporter(commission_model=comm, initial_cash=initial_cash)
    matcher = MatchingEngine(commission_model=comm, slippage=0.0)
    backtest_exec = BacktestExecution(
        matcher=matcher,
        position_svc=engine._position_svc,
        account_svc=engine._account_svc,
        reporter=reporter,
    )

    reg = engine._strategy_regs[0]
    risk = engine._create_pipeline_risk(reg, scene="backtest")
    pipeline = SignalPipeline(execution=backtest_exec, risk=risk)
    pipeline.set_strategy(reg.strategy)
    pipeline.set_trade_callback(lambda t, b, r=reg: engine._on_pipeline_trade(r, t, b))
    reg.strategy.set_pipeline(pipeline)
    reg.pipeline = pipeline
    reg.strategy.ctx._reporter = reporter
    reg.strategy.ctx._mode = "backtest"
    if reg.risk_gateway is not None:
        reg.risk_gateway.set_equity_baseline(initial_cash)

    strategy: RecordingDualEma = reg.strategy
    strategy.on_init()

    for bar in bars[:warmup_bars]:
        engine._process_warmup_bar(reg, bar)

    strategy.ctx._warmup = False
    strategy.on_start()

    for bar in bars[warmup_bars:]:
        if on_before_monitor is not None and reg.risk_gateway is not None:
            on_before_monitor(engine, reg, bar, reg.risk_gateway)
        engine._process_bar(reg, bar)

    pos = engine._position_svc.get(symbol)
    holding = pos.volume if pos else 0.0
    return ScenarioResult(
        trades=list(strategy.recorded_trades),
        risk_blocks=list(strategy.risk_blocks),
        final_holding=holding,
        gateway=reg.risk_gateway,
        reg=reg,
        engine=engine,
    )


def has_risk_close(trades: Sequence[TradeData]) -> bool:
    """是否存在风控触发的卖出成交。"""
    for t in trades:
        if t.direction != Direction.SHORT:
            continue
        if t.tag in ("risk_close", "risk_close_drawdown", "risk_partial"):
            return True
    return False


def has_buy(trades: Sequence[TradeData]) -> bool:
    return any(t.direction == Direction.LONG for t in trades)


def inject_limit_down_tick(gateway: RiskGateway, bar: BarData, limit_price: float) -> None:
    """注入跌停 Tick，供 L1 涨跌停测试。"""
    tick = TickData(
        symbol=bar.symbol,
        last_price=limit_price,
        volume=bar.volume,
        amount=bar.amount,
        open_price=bar.open,
        high_price=bar.high,
        low_price=bar.low,
        pre_close=bar.open,
        event_time=bar.event_time,
        limit_up=limit_price * 1.1,
        limit_down=limit_price,
    )
    gateway.data_layer.update_tick(tick)

