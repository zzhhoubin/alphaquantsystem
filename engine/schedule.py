# alphaQuantSystem/engine/schedule.py
"""Live trading schedule — wall-clock timer emitting SCHEDULE events (live only)."""
from __future__ import annotations

import re
import threading
from datetime import date, datetime
from typing import Callable, Dict, List, Tuple

from loguru import logger

from alphaQuantSystem.core import ScheduleEvent

_HHMM_RE = re.compile(r"^\d{2}:\d{2}$")


class Schedule:
    """Live wall-clock timer. Stores (time: str, handler_name: str) pairs."""

    def __init__(self):
        self._entries: List[Tuple[str, str]] = []
        self._emit: Callable[[ScheduleEvent], None] | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._fired_keys: set[tuple[str, str, str]] = set()
        self._last_date: date | None = None

    def at(self, time: str, handler_name: str) -> None:
        if not _HHMM_RE.match(time):
            raise ValueError(f"Invalid time format: {time!r}, must be HH:MM")
        self._entries.append((time, handler_name))

    @staticmethod
    def validate_handler(strategy_cls: type, schedule: Dict[str, str]) -> None:
        """Validate at registration time that handler_name exists and is callable on strategy_cls."""
        for time_str, method_name in schedule.items():
            if not _HHMM_RE.match(time_str):
                raise ValueError(f"Invalid time format: {time_str!r}")
            handler = getattr(strategy_cls, method_name, None)
            if handler is None or not callable(handler):
                raise ValueError(
                    f"{strategy_cls.__name__} has no schedule method {method_name!r}"
                )

    def start(
        self,
        emit: Callable[[ScheduleEvent], None],
        *,
        skip_past_today: bool = False,
    ) -> None:
        """Start background thread; at each HH:MM emit once per calendar day.

        If skip_past_today is True, tasks at or before the current HH:MM are marked
        as already fired so a mid-day restart does not immediately re-trigger them.
        """
        if self._thread is not None and self._thread.is_alive():
            return
        self._emit = emit
        self._stop.clear()
        if skip_past_today:
            now = datetime.now()
            today = now.date()
            self._last_date = today
            hhmm = now.strftime("%H:%M")
            for time_str, handler_name in self._entries:
                if time_str > hhmm:
                    continue
                key = (today.isoformat(), time_str, handler_name)
                self._fired_keys.add(key)
                logger.info(
                    "[Schedule] Skip past task {} at {} (now {})",
                    handler_name,
                    time_str,
                    hhmm,
                )
        self._thread = threading.Thread(target=self._loop, name="ScheduleTimer", daemon=True)
        self._thread.start()
        times = ", ".join(f"{t}→{h}" for t, h in self._entries)
        logger.info("[Schedule] Started {} task(s): {}", len(self._entries), times)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            now = datetime.now()
            today = now.date()
            if self._last_date != today:
                self._fired_keys.clear()
                self._last_date = today
            hhmm = now.strftime("%H:%M")
            emit = self._emit
            if emit is not None:
                for time_str, handler_name in self._entries:
                    if time_str != hhmm:
                        continue
                    key = (today.isoformat(), time_str, handler_name)
                    if key in self._fired_keys:
                        continue
                    self._fired_keys.add(key)
                    logger.info("[Schedule] Trigger {} at {}", handler_name, hhmm)
                    emit(ScheduleEvent(time=time_str, handler_name=handler_name, event_time=now))
            self._stop.wait(1.0)
