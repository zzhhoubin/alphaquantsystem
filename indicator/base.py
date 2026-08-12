"""
indicator.base
==============
BaseIndicator 抽象基类，定义所有增量指标的统一接口。

调用关系：
    - 被 indicator.ma / indicator.momentum 中各具体指标类继承。
    - 策略层（strategy.template.BaseStrategy 子类）通过此接口调用指标。
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any


class BaseIndicator(ABC):
    """增量式技术指标基类。

    使用模式（实盘 on_bar）::

        ind = SomeIndicator(period=20)
        ind.push(close=bar.close)
        if ind.is_ready:
            val = ind.value
    """

    @abstractmethod
    def push(self, **kwargs) -> None:
        """逐 bar 喂入数据。子类根据指标需要声明具体关键字参数。"""

    @property
    @abstractmethod
    def value(self) -> Any:
        """当前指标值；数据不足时返回 None。"""

    @property
    @abstractmethod
    def is_ready(self) -> bool:
        """已积累足够数据可以计算，返回 True。"""

    @abstractmethod
    def reset(self) -> None:
        """清空内部所有状态，使指标恢复到初始未就绪状态。子类必须实现。"""
