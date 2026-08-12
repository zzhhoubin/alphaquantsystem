"""逐步执行追踪 — 通过 --debug / --debug-trades-only 开启。"""
from __future__ import annotations

from typing import Any

from loguru import logger

_step = 0
_enabled = False
_trades_only = False

# trades_only 模式下始终输出的 (component, message) 前缀
_ALWAYS = {
    ("Engine", "backtest start"),
    ("Engine", "warmup done, on_start"),
    ("Engine", "backtest loop done, on_stop"),
    ("Engine", "day settle"),
    ("Engine", "phase1 intraday risk"),
    ("Engine", "trade callback"),
    ("DualEma", "on_init"),
    ("DualEma", "golden_cross signal"),
    ("DualEma", "dead_cross signal"),
    ("Pipeline", "submit"),
    ("Pipeline", "drain start"),
    ("Pipeline", "evaluate"),
    ("Pipeline", "blocked"),
    ("Risk", "event"),
    ("Risk", "intraday event"),
    ("Risk", "intraday events"),
}


def enable(*, trades_only: bool = False) -> None:
    """开启逐步追踪（日志级别需为 DEBUG）。

    Args:
        trades_only: 仅输出成交/信号/风控触发等关键步骤，跳过每根 bar 的冗长日志。
    """
    global _enabled, _step, _trades_only
    _enabled = True
    _trades_only = trades_only
    _step = 0


def is_enabled() -> bool:
    return _enabled


def _should_emit(component: str, message: str, fields: dict[str, Any]) -> bool:
    if not _trades_only:
        return True
    key = (component, message)
    if key in _ALWAYS:
        return True
    if component == "Risk" and message == "monitor" and fields.get("events", 0) > 0:
        return True
    return False


def trace(component: str, message: str, **fields: Any) -> None:
    """输出一步执行记录：[STEP nnnn] [组件] 消息 key=val ..."""
    if not _enabled:
        return
    if not _should_emit(component, message, fields):
        return
    global _step
    _step += 1
    suffix = ""
    if fields:
        parts = [f"{k}={v}" for k, v in fields.items()]
        suffix = " | " + " ".join(parts)
    # 使用 INFO，确保 --debug-trades-only 在 INFO 级别下也可见
    logger.info(f"[STEP {_step:04d}] [{component}] {message}{suffix}")
