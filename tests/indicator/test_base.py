"""BaseIndicator 抽象基类测试"""
import pytest
from alphaQuantSystem.indicator.base import BaseIndicator


class _ConcreteIndicator(BaseIndicator):
    """最小化具体实现，用于测试基类接口。"""
    def __init__(self, period: int):
        self._period = period
        self._buf = []

    def push(self, close: float) -> None:
        self._buf.append(close)

    @property
    def value(self):
        if not self.is_ready:
            return None
        return sum(self._buf[-self._period:]) / self._period

    @property
    def is_ready(self) -> bool:
        return len(self._buf) >= self._period

    def reset(self) -> None:
        self._buf.clear()


def test_not_ready_before_enough_data():
    ind = _ConcreteIndicator(3)
    ind.push(1.0)
    ind.push(2.0)
    assert not ind.is_ready
    assert ind.value is None


def test_ready_after_enough_data():
    ind = _ConcreteIndicator(3)
    for v in [1.0, 2.0, 3.0]:
        ind.push(close=v)
    assert ind.is_ready
    assert ind.value == pytest.approx(2.0)


def test_reset_clears_state():
    ind = _ConcreteIndicator(2)
    ind.push(close=1.0)
    ind.push(close=2.0)
    assert ind.is_ready
    ind.reset()
    assert not ind.is_ready
