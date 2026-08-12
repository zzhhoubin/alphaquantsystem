"""
风控计算层（Layer 2 / 6）

纯计算层，无业务分支、无执行逻辑，只输出指标准照供规则引擎调用。

必须实现指标（Skill 3.2）：
  - 单笔持仓盈亏、浮动盈亏比例
  - 策略当日总盈亏、累计盈亏
  - 策略当前最大回撤、当日回撤
  - 总仓位占比、单标的仓位占比
  - 持仓时长、距开仓周期数
  - 成交滑点偏差值、价格波动幅度
  - 连续亏损笔数、连续亏损周期数

约束：所有指标带时间戳快照，支持回放、复盘追溯。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np

from .data_layer import RiskSnapshot
from .risk_limits import RiskLimits


@dataclass
class IndicatorSnapshot:
    """风控指标快照 —— 纯计算结果，供规则引擎判定

    约束：所有字段带确定的时间戳，可被日志层全量记录、复盘追溯。
    """
    symbol: str
    strategy_id: str
    timestamp: datetime

    # ---- P&L 指标 ----
    unrealized_pnl: float = 0.0  # 单笔持仓浮动盈亏（绝对值）
    unrealized_pnl_pct: float = 0.0  # 单笔浮动盈亏比例
    realized_pnl_daily: float = 0.0  # 策略当日已实现盈亏
    realized_pnl_cumulative: float = 0.0  # 策略累计已实现盈亏

    # ---- 回撤指标 ----
    max_drawdown: float = 0.0  # 策略当前最大回撤（比例）
    daily_drawdown: float = 0.0  # 当日回撤（比例）

    # ---- 仓位指标 ----
    total_position_ratio: float = 0.0  # 总仓位占比（持仓市值/总权益）
    symbol_position_ratio: float = 0.0  # 单标的仓位占比

    # ---- 时序指标 ----
    holding_duration: float = 0.0  # 持仓时长（秒）
    holding_periods: int = 0  # 距开仓K线周期数

    # ---- 滑点/波动 ----
    slippage_bp: float = 0.0  # 成交滑点偏差(bp)
    price_volatility: float = 0.0  # 价格波动幅度（比例）
    price_change_pct: float = 0.0  # 当期涨跌幅
    high_low_range_pct: float = 0.0  # 当日振幅

    # ---- 连续亏损 ----
    consecutive_losses: int = 0  # 连续亏损笔数
    consecutive_loss_periods: int = 0  # 连续亏损周期数

    # ---- 市场状态标记 ----
    is_limit_up: bool = False
    is_limit_down: bool = False
    price_gap_pct: float = 0.0  # 跳空幅度
    is_trading_hours: bool = True


class CalcLayer:
    """风控计算层：基于 RiskSnapshot 做纯指标计算，无任何业务分支。

    内部维护少量状态以支持连续亏损、回撤峰值等跨周期指标。
    所有输出带时间戳，确保回测复盘可追溯。
    """

    def __init__(self, risk_limits: RiskLimits):
        self.limits = risk_limits

        # ---- 策略全局状态 ----
        self._cumulative_pnl: float = 0.0
        self._peak_equity: float = 0.0  # 历史最高权益（用于回撤计算）
        self._daily_start_equity: float = 0.0
        self._daily_pnl: float = 0.0
        self._daily_peak_equity: float = 0.0
        self._daily_date: Optional[datetime] = None

        # ---- 单笔持仓状态 ----
        self._entry_prices: Dict[str, float] = {}  # symbol → entry_price
        self._entry_times: Dict[str, datetime] = {}  # symbol → entry_time
        self._entry_periods: Dict[str, int] = {}  # symbol → entry_period_count
        self._position_highs: Dict[str, float] = {}  # symbol → highest price since entry

        # ---- 连续亏损追踪 ----
        self._trade_pnl_history: List[float] = []  # 最近N笔交易的盈亏
        self._period_pnl_history: List[float] = []  # 最近N周期的盈亏
        self._price_history: Dict[str, List[float]] = {}  # symbol → recent prices (for vol calc)

    # ---- 数据更新 ----

    def update_trade_result(self, symbol: str, pnl: float, timestamp: Optional[datetime] = None):
        """记录一笔已平仓交易的盈亏，用于追踪连续亏损"""
        self._trade_pnl_history.append(pnl)
        self._cumulative_pnl += pnl
        if timestamp:
            self._update_daily(timestamp, pnl)

    def update_period_pnl(self, symbol: str, period_pnl: float):
        """记录一个周期的盈亏（用于连续亏损周期）"""
        self._period_pnl_history.append(period_pnl)

    def update_entry(
            self,
            symbol: str,
            entry_price: float,
            entry_time: Optional[datetime] = None,
    ):
        """记录开仓信息"""
        self._entry_prices[symbol] = entry_price
        self._entry_times[symbol] = entry_time or datetime.now()
        self._entry_periods[symbol] = 0
        self._position_highs[symbol] = entry_price

    def clear_entry(self, symbol: str):
        """清除开仓记录"""
        self._entry_prices.pop(symbol, None)
        self._entry_times.pop(symbol, None)
        self._entry_periods.pop(symbol, None)
        self._position_highs.pop(symbol, None)

    def increment_period(self, symbol: str):
        """增加持仓周期计数"""
        if symbol in self._entry_periods:
            self._entry_periods[symbol] += 1

    def update_price_history(self, symbol: str, price: float, max_len: int = 252):
        """维护价格历史用于波动率计算"""
        if symbol not in self._price_history:
            self._price_history[symbol] = []
        self._price_history[symbol].append(price)
        if len(self._price_history[symbol]) > max_len:
            self._price_history[symbol] = self._price_history[symbol][-max_len:]

    def _update_daily(self, timestamp: datetime, pnl_delta: float):
        """更新日级盈亏追踪"""
        today = timestamp.date() if hasattr(timestamp, 'date') else timestamp
        if self._daily_date != today:
            self._daily_date = today
            self._daily_pnl = 0.0
            self._daily_start_equity = self._cumulative_pnl - pnl_delta
        self._daily_pnl += pnl_delta

    def set_equity_baseline(self, equity: float):
        """设置权益基线（用于回撤计算初始化）"""
        if equity > self._peak_equity:
            self._peak_equity = equity

    def reset_peak_equity(self, equity: float) -> None:
        """将权益峰值重置为当前权益（最大回撤清仓后调用，开启新一轮回撤计量）。"""
        self._peak_equity = max(float(equity), 0.0)

    # ---- 指标计算 ----

    def compute(self, snapshot: RiskSnapshot) -> IndicatorSnapshot:
        """基于标准化快照计算全部风控指标

        纯计算，无副作用（除内部状态更新外）。
        所有指标带时间戳，供日志层全量落库。
        """
        ts = snapshot.timestamp  # 当前快照时间
        symbol = snapshot.symbol

        # 更新价格历史
        if snapshot.last_price > 0:
            self.update_price_history(symbol, snapshot.last_price)

        # --- 单笔持仓盈亏 ---
        # 优先用 DataLayer 同步的持仓均价（与 PositionService 一致）；
        # _entry_prices 仅作兜底，避免旧仓位残留导致盈亏比例算错。
        if snapshot.has_position and snapshot.position_price > 0:
            entry_price = snapshot.position_price
            if self._entry_prices.get(symbol) != entry_price:
                self._entry_prices[symbol] = entry_price
        else:
            entry_price = self._entry_prices.get(symbol, 0.0)
        if snapshot.has_position and entry_price > 0:
            if snapshot.position_direction == "long":
                unrealized_pnl_pct = (snapshot.last_price - entry_price) / entry_price
                unrealized_pnl = snapshot.position_volume * (snapshot.last_price - entry_price)
            elif snapshot.position_direction == "short":
                unrealized_pnl_pct = (entry_price - snapshot.last_price) / entry_price
                unrealized_pnl = snapshot.position_volume * (entry_price - snapshot.last_price)
            else:
                unrealized_pnl_pct = 0.0
                unrealized_pnl = snapshot.position_pnl
        else:
            unrealized_pnl_pct = 0.0
            unrealized_pnl = snapshot.position_pnl

        # --- 追踪持仓最高价 ---
        if symbol in self._position_highs and snapshot.last_price > self._position_highs[symbol]:
            self._position_highs[symbol] = snapshot.last_price

        # --- 策略当日/累计盈亏 ---
        realized_pnl_daily = self._daily_pnl
        realized_pnl_cumulative = self._cumulative_pnl

        # --- 最大回撤 ---
        total_equity = snapshot.total_equity
        if self._peak_equity == 0:
            self._peak_equity = total_equity
        elif total_equity > self._peak_equity:
            self._peak_equity = total_equity
        max_drawdown = (
            (self._peak_equity - total_equity) / self._peak_equity
            if self._peak_equity > 0
            else 0.0
        )

        # --- 当日回撤（日内峰值追踪） ---
        today = ts.date() if hasattr(ts, 'date') else ts
        if self._daily_date != today:
            self._daily_date = today
            self._daily_peak_equity = total_equity
        if total_equity > self._daily_peak_equity:
            self._daily_peak_equity = total_equity
        if self._daily_peak_equity > 0:
            daily_drawdown = max(
                0.0,
                (self._daily_peak_equity - total_equity) / self._daily_peak_equity,
            )
        else:
            daily_drawdown = 0.0

        # --- 仓位占比 ---
        total_equity_for_ratio = snapshot.total_equity if snapshot.total_equity > 0 else 1.0
        total_position_ratio = abs(snapshot.position_value) / total_equity_for_ratio
        symbol_position_ratio = abs(snapshot.position_value) / total_equity_for_ratio

        # --- 持仓时长/周期 ---
        entry_time = self._entry_times.get(symbol)
        if entry_time and snapshot.has_position:
            holding_duration = (ts - entry_time).total_seconds()
        else:
            holding_duration = 0.0
        holding_periods = self._entry_periods.get(symbol, 0)

        # --- 滑点偏差（需订单成交回报，现价偏差不是滑点） ---
        slippage_bp = 0.0

        # --- 价格波动 ---
        prices = self._price_history.get(symbol, [])
        if len(prices) >= 2:
            returns = np.diff(prices) / np.array(prices[:-1])
            price_volatility = float(np.std(returns)) if len(returns) > 0 else 0.0
        else:
            price_volatility = 0.0

        # --- 当期涨跌幅 ---
        if snapshot.pre_close > 0:
            price_change_pct = (snapshot.last_price - snapshot.pre_close) / snapshot.pre_close
        else:
            price_change_pct = 0.0

        # --- 振幅 ---
        if snapshot.pre_close > 0:
            high_low_range_pct = (
                (snapshot.high_price - snapshot.low_price) / snapshot.pre_close
                if snapshot.high_price > 0 and snapshot.low_price > 0
                else 0.0
            )
        else:
            high_low_range_pct = 0.0

        # --- 连续亏损 ---
        consecutive_losses = self._count_consecutive(self._trade_pnl_history)
        consecutive_loss_periods = self._count_consecutive(self._period_pnl_history)

        # --- 市场状态 ---
        is_limit_up = (
                snapshot.limit_up > 0
                and abs(snapshot.last_price - snapshot.limit_up) < 0.001
        )
        is_limit_down = (
                snapshot.limit_down > 0
                and abs(snapshot.last_price - snapshot.limit_down) < 0.001
        )
        price_gap_pct = (
            abs(snapshot.open_price - snapshot.pre_close) / snapshot.pre_close
            if snapshot.pre_close > 0 and snapshot.open_price > 0
            else 0.0
        )

        return IndicatorSnapshot(
            symbol=symbol,
            strategy_id=snapshot.strategy_id,
            timestamp=ts,
            unrealized_pnl=unrealized_pnl,
            unrealized_pnl_pct=unrealized_pnl_pct,
            realized_pnl_daily=realized_pnl_daily,
            realized_pnl_cumulative=realized_pnl_cumulative,
            max_drawdown=max_drawdown,
            daily_drawdown=daily_drawdown,
            total_position_ratio=total_position_ratio,
            symbol_position_ratio=symbol_position_ratio,
            holding_duration=holding_duration,
            holding_periods=holding_periods,
            slippage_bp=slippage_bp,
            price_volatility=price_volatility,
            price_change_pct=price_change_pct,
            high_low_range_pct=high_low_range_pct,
            consecutive_losses=consecutive_losses,
            consecutive_loss_periods=consecutive_loss_periods,
            is_limit_up=is_limit_up,
            is_limit_down=is_limit_down,
            price_gap_pct=price_gap_pct,
            is_trading_hours=True,
        )

    @staticmethod
    def _count_consecutive(pnl_list: List[float]) -> int:
        """从尾部计连续亏损次数"""
        count = 0
        for pnl in reversed(pnl_list):
            if pnl < 0:
                count += 1
            else:
                break
        return count

    def reset_daily(self):
        """重置日级状态（换日时调用）"""
        self._daily_pnl = 0.0
        self._daily_date = None
        self._daily_peak_equity = 0.0
        self._daily_start_equity = 0.0

    def snapshot_dict(self, indicator: IndicatorSnapshot) -> Dict[str, Any]:
        """将指标快照序列化为可持久化的字典"""
        return {
            "symbol": indicator.symbol,
            "strategy_id": indicator.strategy_id,
            "timestamp": indicator.timestamp.isoformat(),
            "unrealized_pnl": indicator.unrealized_pnl,
            "unrealized_pnl_pct": indicator.unrealized_pnl_pct,
            "realized_pnl_daily": indicator.realized_pnl_daily,
            "realized_pnl_cumulative": indicator.realized_pnl_cumulative,
            "max_drawdown": indicator.max_drawdown,
            "daily_drawdown": indicator.daily_drawdown,
            "total_position_ratio": indicator.total_position_ratio,
            "symbol_position_ratio": indicator.symbol_position_ratio,
            "holding_duration": indicator.holding_duration,
            "holding_periods": indicator.holding_periods,
            "slippage_bp": indicator.slippage_bp,
            "price_volatility": indicator.price_volatility,
            "price_change_pct": indicator.price_change_pct,
            "high_low_range_pct": indicator.high_low_range_pct,
            "consecutive_losses": indicator.consecutive_losses,
            "consecutive_loss_periods": indicator.consecutive_loss_periods,
            "is_limit_up": indicator.is_limit_up,
            "is_limit_down": indicator.is_limit_down,
            "price_gap_pct": indicator.price_gap_pct,
            "is_trading_hours": indicator.is_trading_hours,
        }
