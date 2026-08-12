"""Schedule 定时任务单元测试。"""
from __future__ import annotations

import threading
from datetime import datetime

from alphaQuantSystem.core import ScheduleEvent
from alphaQuantSystem.engine.schedule import Schedule


def test_schedule_fires_once_per_day(monkeypatch):
    fired: list[ScheduleEvent] = []

    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 6, 12, 11, 13, 0)

    import alphaQuantSystem.engine.schedule as sched_mod
    monkeypatch.setattr(sched_mod, "datetime", FakeDatetime)

    sch = Schedule()
    sch.at("11:13", "morning_routine")
    sch.start(fired.append)

    threading.Event().wait(1.5)
    sch.stop()

    assert len(fired) == 1
    assert fired[0].handler_name == "morning_routine"
    assert fired[0].time == "11:13"


def test_schedule_skip_past_today_on_startup(monkeypatch):
    fired: list[ScheduleEvent] = []

    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 6, 12, 13, 54, 30)

    import alphaQuantSystem.engine.schedule as sched_mod
    monkeypatch.setattr(sched_mod, "datetime", FakeDatetime)

    sch = Schedule()
    sch.at("11:29", "morning_routine")
    sch.at("13:41", "afternoon_routine")
    sch.at("15:10", "reset_daily_flags")
    sch.start(fired.append, skip_past_today=True)

    threading.Event().wait(1.5)
    sch.stop()

    assert fired == []
