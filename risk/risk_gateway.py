"""
风控网关 —— 六层架构统一入口

流程（严格按 Skill 六层架构）：
  SIGNAL → DataLayer.snapshot() → CalcLayer.compute() → RuleEngine.evaluate()
         → ExecLayer.dispatch() → ORDER_REQUEST（通过）/ RISK_BLOCK（拦截）
         → LogLayer.record()（全链路落库）

Tick/Bar → DataLayer.update_*() → snapshot → calc → rule → exec（逐帧持仓监控）

回测/实盘逻辑完全对齐：计算层 + 规则引擎共用同一套判定代码，
仅 DataLayer / SceneAdapter / ExecLayer 按场景分支（Skill 第九节）。
"""
from __future__ import annotations
from datetime import datetime
from typing import Dict, Optional

from loguru import logger

from alphaQuantSystem.core import (
    Direction, Event, EventEngine, EventType, OrderData, OrderType, SignalData,
    TickData, BarData, PositionData, AccountData,
)

from .risk_limits import RiskLimits
from .state_machine import RiskStateMachine
from .data_layer import DataLayer
from .calc_layer import CalcLayer
from .rule_engine import RuleEngine
from .exec_layer import ExecLayer
from .scene_layer import SceneAdapter
from .log_layer import LogLayer
from .policies import RiskAction


class RiskGateway:
    """风控网关：策略信号与持仓变更的统一风控入口。

    保持与旧版兼容的构造器签名：RiskGateway(event_engine, risk_limits)
    通过 risk_limits 的 scene 字段决定回测/实盘分支。

    六层数据流：
      Layer1 DataLayer    → 数据归一化
      Layer2 CalcLayer    → 指标计算
      Layer3 RuleEngine   → 规则判定 + 优先级仲裁
      Layer4 ExecLayer    → 动作执行
      Layer5 SceneAdapter → 回测/实盘差异
      Layer6 LogLayer     → 全量事件落库
    """

    def __init__(
        self,
        event_engine: EventEngine,
        risk_limits: Optional[RiskLimits] = None,
    ):
        """
        Args:
            event_engine: 全局事件总线
            risk_limits: 风控配置参数容器；为空时使用默认配置
        """
        # 配置
        if risk_limits is None:
            risk_limits = RiskLimits()
        self.event_engine = event_engine
        self.risk_limits = risk_limits
        self.scene = risk_limits.scene

        # ---- 六层架构初始化 ----
        # Layer 1: 数据接入
        self.data_layer = DataLayer(scene=self.scene)

        # Layer 2: 风控计算
        self.calc_layer = CalcLayer(risk_limits)

        # 状态机
        self.state_machine = RiskStateMachine()

        # Layer 3: 规则引擎
        self.rule_engine = RuleEngine(self.state_machine, risk_limits)
        self.rule_engine.register_default_policies()

        # Layer 4: 执行调度
        self.exec_layer = ExecLayer(event_engine, scene=self.scene)

        # Layer 5: 场景适配
        self.scene_adapter = SceneAdapter(scene=self.scene)

        # Layer 6: 日志监控
        self.log_layer = LogLayer()

        # ---- 订阅事件 ----
        # 主链路：策略信号 → 风控 → 放行/拦截
        self.event_engine.subscribe(EventType.SIGNAL, self._on_signal)
        # 持仓监控链路：逐Tick/Bar 做止盈止损回撤检测
        self.event_engine.subscribe(EventType.TICK, self._on_tick)
        self.event_engine.subscribe(EventType.BAR, self._on_bar)
        self.event_engine.subscribe(EventType.POSITION, self._on_position)
        self.event_engine.subscribe(EventType.ACCOUNT, self._on_account)

        logger.info(
            f"[RiskGateway] 六层风控架构初始化完成 | scene={self.scene} | "
            f"规则数={len(self.rule_engine._policies)}"
        )
        logger.info(self.risk_limits.describe())

    # ==================================================================
    # 事件处理
    # ==================================================================

    def evaluate_signal_sync(
        self,
        signal: SignalData,
        *,
        dispatch_exec: bool = False,
    ) -> tuple[bool, str]:
        """同步评估策略信号（供 SignalPipeline 调用）。

        Args:
            signal: 策略信号
            dispatch_exec: 为 True 时通过 ExecLayer 下发风控动作（事件驱动旧链路）

        Returns:
            (passed, reason) — passed 为 False 时 reason 为拦截原因
        """
        ts = signal.event_time if isinstance(signal.event_time, datetime) else datetime.now()

        pre_events = self.rule_engine.check_signal(
            symbol=signal.symbol,
            strategy_id=signal.strategy_id,
            volume=signal.volume,
            price=signal.price,
            timestamp=ts,
            direction=signal.direction,
        )
        if pre_events:
            self.rule_engine.apply_state_transitions(pre_events)
            if dispatch_exec:
                self.exec_layer.dispatch(pre_events)
            for e in pre_events:
                self.log_layer.record(
                    e,
                    limits=self.risk_limits.limits,
                    execution_result="rejected",
                    scene=self.scene,
                )
            return False, pre_events[0].reason

        # L2/L3 持仓监控仅在 check_monitoring_risk / BAR 链路执行；
        # 开仓信号只做 check_signal（L4 + 状态机），避免与监控重复评估、重复记日志。

        pos_policy = self.rule_engine.get_position_risk_policy()
        if pos_policy:
            pos_policy.record_trade(ts)
        return True, ""

    def _on_signal(self, event: Event):
        """处理策略信号（主链路）。

        SIGNAL → 风控六层流水线 → ORDER_REQUEST / RISK_BLOCK
        """
        signal: SignalData = event.data
        passed, _reason = self.evaluate_signal_sync(signal, dispatch_exec=True)
        if passed:
            self._forward_signal(signal)

    def _on_tick(self, event: Event):
        """逐Tick持仓监控：刷新行情数据 → 走完整风控流水线"""
        tick: TickData = event.data
        self.data_layer.update_tick(tick)

        # 仅在有持仓时执行风控评估
        snapshot = self.data_layer.snapshot(tick.symbol)
        if not snapshot.has_position:
            return

        self._run_monitoring_pipeline(snapshot)

    def _on_bar(self, event: Event):
        """逐Bar持仓监控：刷新行情 → 增加持仓周期 → 风控评估"""
        bar: BarData = event.data
        self.data_layer.update_bar(bar)
        self.calc_layer.increment_period(bar.symbol)

        snapshot = self.data_layer.snapshot(bar.symbol)
        if not snapshot.has_position:
            return

        self._run_monitoring_pipeline(snapshot)

    def _on_position(self, event: Event):
        """持仓更新"""
        pos: PositionData = event.data
        self.data_layer.update_position(pos)

    def _on_account(self, event: Event):
        """账户更新"""
        acc: AccountData = event.data
        self.data_layer.update_account(acc)

    # ==================================================================
    # 核心流水线
    # ==================================================================

    def _run_monitoring_pipeline(self, snapshot, monitor_when: str = "each_bar"):
        """持仓监控流水线：计算 → 评估 → 执行 → 记录"""
        indicator = self.calc_layer.compute(snapshot)
        risk_events = self.rule_engine.evaluate(indicator, monitor_when=monitor_when)

        if not risk_events:
            return

        self.rule_engine.apply_state_transitions(risk_events)
        self.exec_layer.dispatch(risk_events)

        for e in risk_events:
            self.log_layer.record(
                e,
                indicator=indicator,
                limits=self.risk_limits.limits,
                execution_result="executed",
                scene=self.scene,
            )

    def _forward_signal(self, signal: SignalData):
        """将通过风控的信号转发为 ORDER_REQUEST"""
        meta = signal.meta or {}
        trade_reason = str(meta.get("reason", "")).strip()
        if trade_reason:
            order_remark = f"{signal.strategy_id}|{trade_reason}"[:200]
        else:
            order_remark = signal.strategy_id

        # 场景适配：计算滑点调整后价格
        direction = "long" if signal.direction == Direction.LONG else "short"
        adjusted_price = self.scene_adapter.determine_fill_price(
            signal_price=signal.price,
            current_price=self.data_layer.get_cached_snapshot(
                signal.symbol
            ).last_price if self.data_layer.get_cached_snapshot(signal.symbol) else signal.price,
            direction=direction,
        )

        order = OrderData(
            order_id="",
            strategy_id=signal.strategy_id,
            symbol=signal.symbol,
            direction=signal.direction,
            order_type=OrderType.LIMIT if signal.price > 0 else OrderType.MARKET,
            volume=signal.volume,
            price=adjusted_price if adjusted_price > 0 else signal.price,
            order_remark=order_remark,
        )
        self.event_engine.put(Event(EventType.ORDER_REQUEST, order))
        extra = f" 理由={trade_reason}" if trade_reason else ""
        logger.info(
            f"[RiskGateway] 信号通过 {signal.symbol} "
            f"{signal.direction.value} vol={signal.volume} px={adjusted_price}{extra}"
        )

    # ==================================================================
    # 回测专用接口：策略主动调用风控流水线，自行处理返回事件
    # ==================================================================

    def check_monitoring_risk(self, symbol: str, strategy_id: str = "", monitor_when: str = "each_bar"):
        """回测友好接口：运行持仓监控流水线，返回风控事件列表（不自动执行）。

        策略在 on_bar / on_tick 中调用此方法：
          1. 先 update_bar/tick + update_position 同步状态到 data_layer
          2. 调用本方法获取风控事件
          3. 遍历事件，FULL_CLOSE/PARTIAL_CLOSE 时调用 self.sell()

        实盘场景请走事件驱动链路（BAR/TICK 自动触发 _on_bar/_on_tick），
        无需调用本方法。

        Args:
            symbol: 标的代码
            strategy_id: 策略 ID
            monitor_when: 监控时机 — "each_bar" | "day_end"

        Returns:
            List[RiskEvent]: 优先级排序后的风控事件列表
        """
        snapshot = self.data_layer.snapshot(symbol, strategy_id)
        indicator = self.calc_layer.compute(snapshot)
        risk_events = self.rule_engine.evaluate(indicator, monitor_when=monitor_when)

        if not snapshot.has_position:
            risk_events = [
                e for e in risk_events
                if e.action not in (RiskAction.FULL_CLOSE, RiskAction.PARTIAL_CLOSE)
            ]

        if risk_events:
            self.rule_engine.apply_state_transitions(risk_events)
            for e in risk_events:
                self.log_layer.record(
                    e,
                    indicator=indicator,
                    limits=self.risk_limits.limits,
                    execution_result="pending",
                    scene=self.scene,
                )

        return risk_events

    def check_signal_risk(self, symbol: str, strategy_id: str, volume: float,
                          price: float, timestamp: datetime):
        """回测友好接口：开仓前信号预检，返回拦截事件列表。

        策略在 buy() 之前调用，如果返回非空列表则不应开仓。

        Returns:
            List[RiskEvent]: 拦截事件列表（空列表表示通过）
        """
        events = self.rule_engine.check_signal(
            symbol=symbol,
            strategy_id=strategy_id,
            volume=volume,
            price=price,
            timestamp=timestamp,
        )
        if events:
            for e in events:
                self.log_layer.record(
                    e,
                    limits=self.risk_limits.limits,
                    execution_result="rejected",
                    scene=self.scene,
                )
        return events

    def clear_position(self, symbol: str) -> None:
        """清除 DataLayer 中指定标的的持仓缓存（空仓同步）。"""
        self.data_layer.clear_symbol_positions(symbol)

    def sync_position(self, symbol: str, direction, volume: float,
                      avg_price: float, pnl: float = 0.0):
        """同步回测持仓到 data_layer（回测策略在每个 bar 调用）。

        Args:
            symbol: 标的代码
            direction: Direction.LONG / Direction.SHORT
            volume: 持仓数量
            avg_price: 持仓均价
            pnl: 浮动盈亏
        """
        from alphaQuantSystem.core import PositionData
        pos = PositionData(
            symbol=symbol,
            direction=direction,
            volume=volume,
            price=avg_price,
            pnl=pnl,
        )
        self.data_layer.update_position(pos)

    def sync_account(self, balance: float, available: float, frozen: float = 0.0):
        """同步回测账户到 data_layer（回测策略在每个 bar 调用）"""
        from alphaQuantSystem.core import AccountData
        acc = AccountData(
            account_id="backtest",
            balance=balance,
            available=available,
            frozen=frozen,
        )
        self.data_layer.update_account(acc)

    # ==================================================================
    # 运维接口
    # ==================================================================

    def manual_reset(self, operator: str = "admin") -> bool:
        """人工解锁风控状态（LOCKED/PAUSE_TRADE → NORMAL）"""
        result = self.state_machine.manual_reset(operator)
        if result:
            self.rule_engine.clear_idempotency_cache()
            self.log_layer.record_state_transition(
                from_state="LOCKED",
                to_state="NORMAL",
                trigger_rule_id="MANUAL_RESET",
                reason=f"人工解锁，操作者: {operator}",
                timestamp=datetime.now(),
                scene=self.scene,
            )
        return result

    def unlock_open(self, operator: str = "admin") -> bool:
        """仅解除开仓限制（LIMIT_OPEN → NORMAL）"""
        result = self.state_machine.unlock_open(operator)
        if result:
            self.log_layer.record_state_transition(
                from_state="LIMIT_OPEN",
                to_state="NORMAL",
                trigger_rule_id="MANUAL_UNLOCK_OPEN",
                reason=f"人工解除开仓限制，操作者: {operator}",
                timestamp=datetime.now(),
                scene=self.scene,
            )
        return result

    def get_status(self) -> dict:
        """获取当前风控状态摘要"""
        return {
            "state": self.state_machine.snapshot(),
            "statistics": self.log_layer.statistics(),
            "scene": self.scene,
        }

    def export_risk_log(self, filepath: str):
        """导出风控日志"""
        self.log_layer.export_json(filepath)

    def update_limits(self, new_limits: Dict):
        """运行时更新风控参数"""
        self.risk_limits.update_limits(new_limits)
        logger.info(f"[RiskGateway] 风控参数已更新: {list(new_limits.keys())}")

    # ==================================================================
    # 策略接入（由策略在开仓时调用，记录开仓信息到计算层）
    # ==================================================================

    def record_trade(self, timestamp: datetime):
        """记录一笔通过风控的交易（递增日交易计数、更新冷却时间）。

        回测策略在 buy() 成功后调用，确保 L4 频控规则（max_trades_per_day、cooldown）
        在回测模式下也能正确生效。
        """
        pos_policy = self.rule_engine.get_position_risk_policy()
        if pos_policy:
            pos_policy.record_trade(timestamp)

    def on_strategy_open(
        self,
        symbol: str,
        entry_price: float,
        entry_time: Optional[datetime] = None,
        *,
        equity: Optional[float] = None,
    ):
        """策略开仓时调用，向计算层注册开仓信息。

        equity: 开仓后账户总权益；用于重置回撤峰值，使最大回撤仅计量本笔持仓周期。
        """
        self.rule_engine.clear_symbol_cache(symbol)
        self.calc_layer.update_entry(symbol, entry_price, entry_time)
        if equity is not None and equity > 0:
            self.calc_layer.reset_peak_equity(equity)

    def on_strategy_close(self, symbol: str, pnl: float, timestamp: Optional[datetime] = None):
        """策略平仓时调用，向计算层记录盈亏"""
        self.calc_layer.update_trade_result(symbol, pnl, timestamp)
        self.calc_layer.clear_entry(symbol)
        self.rule_engine.clear_symbol_cache(symbol)
        self.clear_position(symbol)

    def on_max_drawdown_close(self, symbol: str, equity: float) -> None:
        """最大回撤清仓后：重置权益峰值，不保留 LIMIT_OPEN，后续可正常开仓。"""
        self.calc_layer.reset_peak_equity(equity)
        self.rule_engine.clear_symbol_cache(symbol)
        logger.info(
            f"[RiskGateway] 最大回撤清仓完成 {symbol}，权益峰值已重置为 {equity:.2f}"
        )

    def set_equity_baseline(self, equity: float):
        """设置权益基线（回测初始化时调用）"""
        self.calc_layer.set_equity_baseline(equity)

    def reset_daily(self):
        """换日时重置日级状态"""
        self.calc_layer.reset_daily()
