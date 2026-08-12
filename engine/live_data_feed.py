# alphaQuantSystem/engine/live_data_feed.py
"""Live data feed — QMT push (xtdata) + poll fallback for BAR events."""
from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Callable, Dict, List, Optional

from loguru import logger

from alphaQuantSystem.core import BarData
from alphaQuantSystem.utils.helpers import adjust_symbol

try:
    from xtquant import xtdata as _xtdata
    _HAS_XTQ = True
except ImportError:
    _HAS_XTQ = False


class LiveDataFeed:
    """Live-only data feed — QMT push or poll; callbacks enqueue only."""

    def __init__(self, mode: str = "push_with_poll_fallback"):
        self.mode = mode
        self._symbols: List[str] = []
        self._period: str = "1m"
        self._callback: Optional[Callable[[BarData], None]] = None
        self._running = False
        self._poll_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._last_emit: Dict[str, str] = {}

    def subscribe(self, symbols: List[str], period: str = "1m") -> None:
        self._symbols = [adjust_symbol(s) for s in symbols]
        self._period = period

    def set_callback(self, cb: Callable[[BarData], None]) -> None:
        """Engine registers: cb = lambda bar: event_engine.put(Event(BAR, bar))"""
        self._callback = cb

    def start(self) -> None:
        self._running = True
        self._stop.clear()
        if _HAS_XTQ and self.mode != "poll_only":
            self._subscribe_xtdata()
        if self.mode != "push_only":
            self._poll_thread = threading.Thread(target=self._poll_loop, name="LiveDataPoll", daemon=True)
            self._poll_thread.start()
        logger.info(
            "[LiveDataFeed] Started mode={} period={} symbols={} xtquant={}",
            self.mode, self._period, len(self._symbols), _HAS_XTQ,
        )

    def stop(self) -> None:
        self._running = False
        self._stop.set()
        if self._poll_thread is not None:
            self._poll_thread.join(timeout=3.0)
            self._poll_thread = None
        logger.info("[LiveDataFeed] Stopped")

    def _emit_bar(self, bar: BarData) -> None:
        if not self._running or self._callback is None:
            return
        minute_key = f"{bar.event_time.strftime('%Y%m%d%H%M')}"
        if self._last_emit.get(bar.symbol) == minute_key:
            return
        self._last_emit[bar.symbol] = minute_key
        self._callback(bar)

    def _subscribe_xtdata(self) -> None:
        period = self._period if self._period != "D" else "1d"
        for symbol in self._symbols:
            try:
                _xtdata.subscribe_quote(symbol, period=period, callback=self._on_xt_callback)
                logger.debug("[LiveDataFeed] subscribe_quote {} period={}", symbol, period)
            except Exception as e:
                logger.warning("[LiveDataFeed] subscribe {} failed: {}", symbol, e)

    def _on_xt_callback(self, data) -> None:
        if not isinstance(data, dict):
            return
        for code, payload in data.items():
            bar = self._parse_xt_payload(code, payload)
            if bar is not None:
                self._emit_bar(bar)

    @staticmethod
    def _parse_xt_payload(code: str, payload) -> Optional[BarData]:
        """兼容 xtdata 回调：dict 单条 / list 末条 / 字段向量。"""
        symbol = adjust_symbol(str(code))
        row: Optional[dict] = None
        if isinstance(payload, dict):
            if "close" in payload or "lastPrice" in payload:
                row = payload
            elif payload:
                first_val = next(iter(payload.values()))
                if isinstance(first_val, (list, tuple)) and first_val:
                    row = {
                        "open": _last_of(payload.get("open")),
                        "high": _last_of(payload.get("high")),
                        "low": _last_of(payload.get("low")),
                        "close": _last_of(payload.get("close")),
                        "volume": _last_of(payload.get("volume")),
                        "amount": _last_of(payload.get("amount")),
                        "time": _last_of(payload.get("time")),
                    }
        elif isinstance(payload, list) and payload:
            item = payload[-1]
            if isinstance(item, dict):
                row = item

        if row is None:
            return None

        close = float(row.get("close") or row.get("lastPrice") or 0.0)
        if close <= 0:
            return None
        open_ = float(row.get("open") or close)
        high = float(row.get("high") or close)
        low = float(row.get("low") or close)
        volume = float(row.get("volume") or 0.0)
        amount = float(row.get("amount") or 0.0)
        event_time = _parse_event_time(row.get("time"))
        return BarData(
            symbol=symbol,
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=volume,
            amount=amount,
            event_time=event_time,
            interval="1m",
        )

    def _poll_loop(self) -> None:
        """无推送或推送失败时，轮询 full_tick 生成合成 bar。"""
        while self._running and not self._stop.is_set():
            if _HAS_XTQ and self._symbols:
                try:
                    ticks = _xtdata.get_full_tick(self._symbols)
                    if isinstance(ticks, dict):
                        for code, tick in ticks.items():
                            if not isinstance(tick, dict):
                                continue
                            close = float(tick.get("lastPrice") or tick.get("lastClose") or 0.0)
                            if close <= 0:
                                continue
                            bar = BarData(
                                symbol=adjust_symbol(str(code)),
                                open=float(tick.get("open") or close),
                                high=float(tick.get("high") or close),
                                low=float(tick.get("low") or close),
                                close=close,
                                volume=float(tick.get("volume") or 0.0),
                                amount=float(tick.get("amount") or 0.0),
                                event_time=datetime.now(),
                                interval="1m",
                            )
                            self._emit_bar(bar)
                except Exception as e:
                    logger.debug("[LiveDataFeed] poll tick failed: {}", e)
            self._stop.wait(3.0)


def _last_of(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (list, tuple)):
        if not value:
            return 0.0
        return float(value[-1])
    return float(value)


def _parse_event_time(raw) -> datetime:
    if raw is None:
        return datetime.now()
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, (int, float)):
        ts = float(raw)
        if ts > 1e12:
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts)
        except (OSError, ValueError):
            return datetime.now()
    return datetime.now()
