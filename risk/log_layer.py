"""
日志监控层（Layer 6 / 6）

全量风控事件落库（Skill 3.6）：
  - 触发时间
  - 指标快照
  - 阈值
  - 动作
  - 执行结果
  - 场景标记

所有记录可查询、可统计、可复盘。
"""
from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from .policies import RiskEvent
from .calc_layer import IndicatorSnapshot, CalcLayer


class LogLayer:
    """日志监控层：全量风控事件落库，支持复盘追溯。

    事件记录包含完整的触发上下文，满足可观测性要求。
    """

    def __init__(self, log_dir: Optional[str] = None):
        self._records: List[Dict[str, Any]] = []
        self._log_dir = Path(log_dir) if log_dir else None
        if self._log_dir:
            self._log_dir.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        event: RiskEvent,
        indicator: Optional[IndicatorSnapshot] = None,
        limits: Optional[Dict[str, Any]] = None,
        execution_result: Optional[str] = None,
        scene: str = "live",
    ):
        """记录一条风控事件（含完整上下文）。

        Args:
            event: 风控事件
            indicator: 触发时的指标快照（可选）
            limits: 相关阈值配置（可选）
            execution_result: 执行结果 'success' / 'failed' / 'skipped'
            scene: 场景标记 'live' / 'backtest'
        """
        record = {
            "timestamp": event.timestamp.isoformat(),
            "scene": scene,
            "rule_id": event.rule_id,
            "priority": event.priority,
            "action": event.action,
            "reason": event.reason,
            "symbol": event.symbol,
            "strategy_id": event.strategy_id,
            "close_ratio": event.close_ratio,
            "execution_result": execution_result or "executed",
        }

        if indicator is not None:
            record["indicator_snapshot"] = {
                "unrealized_pnl": indicator.unrealized_pnl,
                "unrealized_pnl_pct": indicator.unrealized_pnl_pct,
                "realized_pnl_daily": indicator.realized_pnl_daily,
                "max_drawdown": indicator.max_drawdown,
                "daily_drawdown": indicator.daily_drawdown,
                "total_position_ratio": indicator.total_position_ratio,
                "holding_periods": indicator.holding_periods,
                "holding_duration": indicator.holding_duration,
                "slippage_bp": indicator.slippage_bp,
                "consecutive_losses": indicator.consecutive_losses,
                "price_change_pct": indicator.price_change_pct,
                "is_limit_up": indicator.is_limit_up,
                "is_limit_down": indicator.is_limit_down,
            }

        if limits:
            # 只记录与事件相关的关键阈值，不全量存储
            related_keys = self._related_limit_keys(event.rule_id)
            record["thresholds"] = {
                k: limits.get(k) for k in related_keys if k in limits
            }

        self._records.append(record)

        # 结构化日志输出
        bar_time = self._format_bar_time(event.timestamp)
        log_msg = (
            f"[风控事件] {bar_time} | L{event.priority} | {event.action} | "
            f"{event.symbol} | {event.rule_id} | {event.reason[:120]}"
        )
        if event.priority <= 2:
            logger.error(log_msg)
        elif event.priority == 3:
            logger.warning(log_msg)
        else:
            logger.info(log_msg)

        # 文件持久化
        self._maybe_flush_to_file(record)

    def record_state_transition(
        self,
        from_state: str,
        to_state: str,
        trigger_rule_id: str,
        reason: str,
        timestamp: datetime,
        scene: str = "live",
    ):
        """记录状态机变更"""
        record = {
            "type": "state_transition",
            "timestamp": timestamp.isoformat(),
            "scene": scene,
            "from_state": from_state,
            "to_state": to_state,
            "trigger_rule_id": trigger_rule_id,
            "reason": reason,
        }
        self._records.append(record)
        bar_time = self._format_bar_time(timestamp)
        logger.warning(
            f"[风控状态] {bar_time} | {from_state} → {to_state} | "
            f"规则={trigger_rule_id} | {reason[:100]}"
        )
        self._maybe_flush_to_file(record)

    def record_rejection(
        self,
        symbol: str,
        strategy_id: str,
        reason: str,
        timestamp: datetime,
        scene: str = "live",
    ):
        """记录信号被拒"""
        record = {
            "type": "signal_rejected",
            "timestamp": timestamp.isoformat(),
            "scene": scene,
            "symbol": symbol,
            "strategy_id": strategy_id,
            "reason": reason,
        }
        self._records.append(record)
        bar_time = self._format_bar_time(timestamp)
        logger.warning(
            f"[风控拦截] {bar_time} | {symbol} | {strategy_id} | {reason[:120]}"
        )
        self._maybe_flush_to_file(record)

    # ---- 查询 ----

    def query(
        self,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        rule_id: Optional[str] = None,
        priority: Optional[int] = None,
        symbol: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """按条件检索风控事件"""
        results = self._records
        if start_time:
            results = [
                r for r in results
                if datetime.fromisoformat(r.get("timestamp", "")) >= start_time
            ]
        if end_time:
            results = [
                r for r in results
                if datetime.fromisoformat(r.get("timestamp", "")) <= end_time
            ]
        if rule_id:
            results = [r for r in results if r.get("rule_id", "").startswith(rule_id)]
        if priority is not None:
            results = [r for r in results if r.get("priority") == priority]
        if symbol:
            results = [r for r in results if r.get("symbol") == symbol]
        return results

    def statistics(self) -> Dict[str, Any]:
        """风控事件统计摘要"""
        if not self._records:
            return {"total": 0}

        event_records = [r for r in self._records if "rule_id" in r]
        actions = {}
        priorities = {}
        for r in event_records:
            actions[r.get("action", "?")] = actions.get(r.get("action", "?"), 0) + 1
            p = r.get("priority", 0)
            priorities[f"L{p}"] = priorities.get(f"L{p}", 0) + 1

        return {
            "total_events": len(event_records),
            "total_records": len(self._records),
            "by_action": actions,
            "by_priority": priorities,
        }

    def clear(self):
        """清空记录（回测重置时调用）"""
        self._records.clear()

    def export_json(self, filepath: str):
        """导出全部记录为 JSON"""
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self._records, f, indent=2, ensure_ascii=False, default=str)
        logger.info(f"风控日志已导出: {filepath} ({len(self._records)} 条)")

    # ---- 内部 ----

    @staticmethod
    def _format_bar_time(ts: datetime) -> str:
        """格式化 bar 时间；日线回测常为 00:00:00，仅显示日期。"""
        if ts.hour == 0 and ts.minute == 0 and ts.second == 0:
            return ts.strftime("%Y-%m-%d")
        return ts.strftime("%Y-%m-%d %H:%M:%S")

    def _maybe_flush_to_file(self, record: Dict[str, Any]):
        """如果配置了 log_dir，实时写入文件"""
        if not self._log_dir:
            return
        try:
            date_str = datetime.now().strftime("%Y%m%d")
            log_file = self._log_dir / f"risk_events_{date_str}.jsonl"
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except Exception as e:
            logger.warning(f"风控日志落盘失败: {e}")

    @staticmethod
    def _related_limit_keys(rule_id: str) -> List[str]:
        """根据 rule_id 返回相关的阈值 key"""
        mapping = {
            "price_stop": ["take_profit_pct", "stop_loss_pct", "trailing_tp_activation",
                           "trailing_tp_callback", "step_tp_levels", "step_sl_levels"],
            "drawdown_stop": ["max_drawdown", "daily_max_loss", "daily_max_loss_pct",
                              "consecutive_loss_limit", "consecutive_loss_periods_limit"],
            "position_limit": ["max_position_size", "max_concentration", "per_symbol_max_qty",
                               "min_order_notional", "max_order_notional", "max_trades_per_day"],
            "abnormal_stop": ["max_slippage_bp", "gap_interval_pct", "abnormal_price_move_pct",
                              "ban_limit_up_down", "max_cancel_orders_per_minute"],
            "time_risk": ["max_holding_periods", "max_holding_seconds", "trade_time_windows"],
        }
        for prefix, keys in mapping.items():
            if rule_id.startswith(prefix):
                return keys
        return []
