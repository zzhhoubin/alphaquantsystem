# 技术指标模块 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `alphaQuantSystem/indicator/` 下新增独立技术指标模块，提供双模式（增量 push 式 + 函数式）的均线类与振荡势能类指标，全链路可用。

**Architecture:** 分五层：`primitive.py`（无状态序列函数）作为最底层计算核心，`base.py` 定义增量指标抽象接口，`ma.py`/`momentum.py` 实现具体增量对象，`functional.py` 提供 DataFrame 批量接口并适配 talib。所有层通过 `__init__.py` 统一导出。

**Tech Stack:** Python 3.8~3.10, numpy, pandas, talib（可选）

---

## 文件清单

| 文件 | 状态 | 职责 |
|------|------|------|
| `indicator/__init__.py` | 新增 | 统一导出所有公共 API |
| `indicator/base.py` | 新增 | `BaseIndicator` 抽象基类 |
| `indicator/primitive.py` | 新增 | 底层无状态序列函数（移植自参考实现） |
| `indicator/ma.py` | 新增 | 均线增量对象（SMA/EMA/WMA） |
| `indicator/momentum.py` | 新增 | 振荡势能增量对象（RSI/MACD/KDJ/CCI/ATR） |
| `indicator/functional.py` | 新增 | 函数式批量接口 + talib 适配 |
| `tests/indicator/test_primitive.py` | 新增 | primitive 函数单元测试 |
| `tests/indicator/test_ma.py` | 新增 | 均线增量对象测试 |
| `tests/indicator/test_momentum.py` | 新增 | 振荡势能增量对象测试 |
| `tests/indicator/test_functional.py` | 新增 | 函数式接口测试 |

---

## Task 1: 创建模块目录骨架

**Files:**
- Create: `indicator/__init__.py`
- Create: `tests/indicator/__init__.py`

- [ ] **Step 1: 创建目录和空文件**

```bash
cd d:/quant/alphaQuant/alphaQuantSystem
mkdir -p alphaQuantSystem/indicator
mkdir -p tests/indicator
touch alphaQuantSystem/indicator/__init__.py
touch tests/indicator/__init__.py
```

- [ ] **Step 2: 验证目录存在**

```bash
ls alphaQuantSystem/indicator/
ls tests/indicator/
```

Expected: 两个目录均存在，各含 `__init__.py`。

- [ ] **Step 3: Commit**

```bash
git add alphaQuantSystem/indicator/ tests/indicator/
git commit -m "feat(indicator): 初始化 indicator 模块目录骨架"
```

---

## Task 2: `base.py` — 抽象基类

**Files:**
- Create: `alphaQuantSystem/indicator/base.py`
- Test: `tests/indicator/test_base.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/indicator/test_base.py`：

```python
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
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
cd d:/quant/alphaQuant/alphaQuantSystem
python -m pytest tests/indicator/test_base.py -v
```

Expected: `ImportError: cannot import name 'BaseIndicator'`

- [ ] **Step 3: 实现 `base.py`**

新建 `alphaQuantSystem/indicator/base.py`：

```python
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
    def value(self):
        """当前指标值；数据不足时返回 None。"""

    @property
    @abstractmethod
    def is_ready(self) -> bool:
        """已积累足够数据可以计算，返回 True。"""

    def reset(self) -> None:
        """清空内部状态。子类若有额外状态需覆盖此方法并调用 super().reset()。"""
        # 遍历实例的 __dict__，将 list/deque 类型清空
        import collections
        for attr, val in self.__dict__.items():
            if isinstance(val, list):
                val.clear()
            elif isinstance(val, collections.deque):
                val.clear()
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
python -m pytest tests/indicator/test_base.py -v
```

Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add alphaQuantSystem/indicator/base.py tests/indicator/test_base.py
git commit -m "feat(indicator): 新增 BaseIndicator 抽象基类"
```

---

## Task 3: `primitive.py` — 底层序列函数

**Files:**
- Create: `alphaQuantSystem/indicator/primitive.py`
- Test: `tests/indicator/test_primitive.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/indicator/test_primitive.py`：

```python
"""primitive 底层序列函数单元测试"""
import numpy as np
import pytest
from alphaQuantSystem.indicator.primitive import (
    MA, EMA, SMA, WMA, STD,
    REF, DIFF, HHV, LLV, SUM,
    MAX, MIN, ABS, IF,
    CROSS, CROSS_UP, CROSS_DOWN,
    AVEDEV,
)


def test_MA_basic():
    s = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    result = MA(s, 3)
    # 前两个为 NaN，第三个起为滚动均值
    assert np.isnan(result[0])
    assert np.isnan(result[1])
    assert result[2] == pytest.approx(2.0)
    assert result[3] == pytest.approx(3.0)
    assert result[4] == pytest.approx(4.0)


def test_EMA_length():
    s = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    result = EMA(s, 3)
    assert len(result) == len(s)


def test_SMA_chinese_style():
    # SMA(S, N, M=1): alpha = M/N
    s = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    result = SMA(s, 3, 1)
    assert len(result) == len(s)
    # SMA 第一个值等于 s[0] 本身（ewm adjust=False）
    assert result[0] == pytest.approx(1.0)


def test_WMA_basic():
    s = np.array([1.0, 2.0, 3.0])
    result = WMA(s, 3)
    # weights: 1,2,3 → (1*1 + 2*2 + 3*3)/(1+2+3) = 14/6 ≈ 2.333
    assert result[2] == pytest.approx(14.0 / 6.0, rel=1e-5)


def test_STD_basic():
    s = np.array([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0])
    result = STD(s, 4)
    assert not np.isnan(result[-1])


def test_REF_shift():
    s = np.array([1.0, 2.0, 3.0, 4.0])
    result = REF(s, 1)
    assert np.isnan(result[0])
    assert result[1] == pytest.approx(1.0)
    assert result[3] == pytest.approx(3.0)


def test_DIFF_basic():
    s = np.array([1.0, 3.0, 6.0, 10.0])
    result = DIFF(s, 1)
    assert np.isnan(result[0])
    assert result[1] == pytest.approx(2.0)
    assert result[3] == pytest.approx(4.0)


def test_HHV_LLV():
    s = np.array([3.0, 1.0, 4.0, 1.0, 5.0])
    assert HHV(s, 3)[4] == pytest.approx(5.0)
    assert LLV(s, 3)[4] == pytest.approx(1.0)


def test_SUM_rolling():
    s = np.array([1.0, 2.0, 3.0, 4.0])
    result = SUM(s, 2)
    assert result[1] == pytest.approx(3.0)
    assert result[3] == pytest.approx(7.0)


def test_MAX_MIN():
    a = np.array([1.0, 5.0, 3.0])
    b = np.array([2.0, 4.0, 3.0])
    assert list(MAX(a, b)) == [2.0, 5.0, 3.0]
    assert list(MIN(a, b)) == [1.0, 4.0, 3.0]


def test_ABS_IF():
    s = np.array([-1.0, 2.0, -3.0])
    assert list(ABS(s)) == [1.0, 2.0, 3.0]
    cond = np.array([True, False, True])
    result = IF(cond, np.array([10.0, 10.0, 10.0]), np.array([20.0, 20.0, 20.0]))
    assert list(result) == [10.0, 20.0, 10.0]


def test_CROSS_golden():
    # s1 从下穿上 s2
    s1 = np.array([1.0, 2.0, 4.0, 5.0])
    s2 = np.array([3.0, 3.0, 3.0, 3.0])
    cross = CROSS(s1, s2)
    assert cross[2] == True   # 第2根穿越
    assert cross[0] == False
    assert cross[3] == False  # 已在上方，不算再穿


def test_CROSS_DOWN():
    s1 = np.array([5.0, 4.0, 2.0, 1.0])
    s2 = np.array([3.0, 3.0, 3.0, 3.0])
    cross = CROSS_DOWN(s1, s2)
    assert cross[2] == True
    assert cross[0] == False
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
python -m pytest tests/indicator/test_primitive.py -v
```

Expected: `ImportError: cannot import name 'MA' from 'alphaQuantSystem.indicator.primitive'`

- [ ] **Step 3: 实现 `primitive.py`**

新建 `alphaQuantSystem/indicator/primitive.py`：

```python
"""
indicator.primitive
===================
底层无状态序列运算函数，基于 numpy / pandas 实现。
公式体系参考通达信公式规范，移植自项目历史代码 sun_tool/technical_analysis_indicator.py。

调用关系：
    - 被 indicator.ma / indicator.momentum 的增量对象内部调用（批量计算时）。
    - 被 indicator.functional 的函数式接口调用（talib 不可用时 fallback）。
    - 可直接在 analyze/ 或 Jupyter 中独立使用。
"""
from __future__ import annotations
import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# 基本向量运算
# ---------------------------------------------------------------------------

def ABS(S: np.ndarray) -> np.ndarray:
    """返回序列绝对值。"""
    return np.abs(S)


def MAX(S1: np.ndarray, S2) -> np.ndarray:
    """逐元素最大值。"""
    return np.maximum(S1, S2)


def MIN(S1: np.ndarray, S2) -> np.ndarray:
    """逐元素最小值。"""
    return np.minimum(S1, S2)


def IF(S: np.ndarray, A, B) -> np.ndarray:
    """条件选择：S 为 True 取 A，否则取 B。"""
    return np.where(S, A, B)


# ---------------------------------------------------------------------------
# 序列移位与差分
# ---------------------------------------------------------------------------

def REF(S, N: int = 1) -> np.ndarray:
    """序列整体下移 N（shift），首 N 个元素为 NaN。"""
    return pd.Series(S).shift(N).values


def DIFF(S, N: int = 1) -> np.ndarray:
    """序列差分（前 N 个为 NaN）。"""
    return pd.Series(S).diff(N).values


# ---------------------------------------------------------------------------
# 统计函数
# ---------------------------------------------------------------------------

def STD(S, N: int) -> np.ndarray:
    """N 日滚动标准差（ddof=0，总体标准差）。"""
    return pd.Series(S).rolling(N).std(ddof=0).values


def SUM(S, N: int) -> np.ndarray:
    """N 日滚动累和；N=0 时返回历史累和。"""
    if N > 0:
        return pd.Series(S).rolling(N).sum().values
    return pd.Series(S).cumsum().values


def COUNT(S, N: int) -> np.ndarray:
    """N 日内满足布尔条件 S 的天数。"""
    return SUM(np.asarray(S, dtype=float), N)


def HHV(S, N: int) -> np.ndarray:
    """N 日滚动最高值。"""
    return pd.Series(S).rolling(N).max().values


def LLV(S, N: int) -> np.ndarray:
    """N 日滚动最低值。"""
    return pd.Series(S).rolling(N).min().values


def AVEDEV(S, N: int) -> np.ndarray:
    """平均绝对偏差：序列与其均值的绝对差的均值。"""
    return pd.Series(S).rolling(N).apply(
        lambda x: np.abs(x - x.mean()).mean(), raw=True
    ).values


# ---------------------------------------------------------------------------
# 均线函数
# ---------------------------------------------------------------------------

def MA(S, N: int) -> np.ndarray:
    """N 日简单移动平均（SMA，前 N-1 个为 NaN）。"""
    return pd.Series(S).rolling(N).mean().values


def EMA(S, N: int) -> np.ndarray:
    """指数移动平均，alpha=2/(N+1)，adjust=False。

    精度要求：序列长度应 > 4*N，至少需要 120 周期才足够精确。
    """
    return pd.Series(S).ewm(span=N, adjust=False).mean().values


def SMA(S, N: int, M: int = 1) -> np.ndarray:
    """中国式 SMA（通达信），alpha=M/N，adjust=False。

    与标准 EMA 的区别：平滑系数 alpha=M/N，而非 2/(N+1)。
    精度要求：序列长度 > 120 周期。
    """
    return pd.Series(S).ewm(alpha=M / N, adjust=False).mean().values


def WMA(S, N: int) -> np.ndarray:
    """线性加权移动平均：Yn = (1*X1+2*X2+...+N*XN) / (1+2+...+N)。"""
    return pd.Series(S).rolling(N).apply(
        lambda x: x[::-1].cumsum().sum() * 2 / N / (N + 1), raw=True
    ).values


def DMA(S, A) -> np.ndarray:
    """动态移动平均，A 为平滑因子（0 < A < 1）。"""
    return pd.Series(S).ewm(alpha=A, adjust=True).mean().values


# ---------------------------------------------------------------------------
# 交叉判断
# ---------------------------------------------------------------------------

def CROSS(S1, S2) -> np.ndarray:
    """S1 上穿 S2（金叉）：上一根 S1<=S2，本根 S1>S2。"""
    s1, s2 = np.asarray(S1), np.asarray(S2)
    return np.concatenate(([False], ~(s1 > s2)[:-1] & (s1 > s2)[1:]))


def CROSS_UP(S1, S2) -> np.ndarray:
    """同 CROSS，S1 上穿 S2。"""
    return CROSS(S1, S2)


def CROSS_DOWN(S1, S2) -> np.ndarray:
    """S1 下穿 S2（死叉）：上一根 S1>=S2，本根 S1<S2。"""
    s1, s2 = np.asarray(S1), np.asarray(S2)
    return np.concatenate(([False], ~(s1 < s2)[:-1] & (s1 < s2)[1:]))


def BARSLAST(S) -> np.ndarray:
    """上一次条件成立到当前的周期数。"""
    m = np.concatenate(([0], np.where(S, 1, 0)))
    for i in range(1, len(m)):
        m[i] = 0 if m[i] else m[i - 1] + 1
    return m[1:]
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
python -m pytest tests/indicator/test_primitive.py -v
```

Expected: 全部通过（约 12 个用例）

- [ ] **Step 5: Commit**

```bash
git add alphaQuantSystem/indicator/primitive.py tests/indicator/test_primitive.py
git commit -m "feat(indicator): 新增 primitive 底层序列函数层"
```

---

## Task 4: `ma.py` — 均线增量对象

**Files:**
- Create: `alphaQuantSystem/indicator/ma.py`
- Test: `tests/indicator/test_ma.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/indicator/test_ma.py`：

```python
"""均线增量对象测试"""
import pytest
from alphaQuantSystem.indicator.ma import SMA_Indicator, EMA_Indicator, WMA_Indicator


# ---------- SMA_Indicator ----------

def test_sma_not_ready_before_period():
    ind = SMA_Indicator(3)
    ind.push(close=1.0)
    ind.push(close=2.0)
    assert not ind.is_ready
    assert ind.value is None


def test_sma_value_correct():
    ind = SMA_Indicator(3)
    for v in [1.0, 2.0, 3.0]:
        ind.push(close=v)
    assert ind.is_ready
    assert ind.value == pytest.approx(2.0)


def test_sma_sliding_window():
    ind = SMA_Indicator(3)
    for v in [1.0, 2.0, 3.0, 4.0]:
        ind.push(close=v)
    # 窗口滑动：(2+3+4)/3
    assert ind.value == pytest.approx(3.0)


def test_sma_reset():
    ind = SMA_Indicator(3)
    for v in [1.0, 2.0, 3.0]:
        ind.push(close=v)
    ind.reset()
    assert not ind.is_ready
    assert ind.value is None


# ---------- EMA_Indicator ----------

def test_ema_not_ready_before_period():
    ind = EMA_Indicator(5)
    for _ in range(4):
        ind.push(close=10.0)
    assert not ind.is_ready


def test_ema_ready_at_period():
    ind = EMA_Indicator(5)
    for i in range(1, 6):
        ind.push(close=float(i))
    assert ind.is_ready
    assert ind.value is not None


def test_ema_value_approaches_price():
    # 价格恒定时，EMA 最终收敛到该价格
    ind = EMA_Indicator(3)
    for _ in range(20):
        ind.push(close=100.0)
    assert ind.value == pytest.approx(100.0, rel=1e-4)


def test_ema_reset():
    ind = EMA_Indicator(3)
    for v in [1.0, 2.0, 3.0]:
        ind.push(close=v)
    ind.reset()
    assert not ind.is_ready


# ---------- WMA_Indicator ----------

def test_wma_not_ready_before_period():
    ind = WMA_Indicator(3)
    ind.push(close=1.0)
    assert not ind.is_ready


def test_wma_value_correct():
    ind = WMA_Indicator(3)
    for v in [1.0, 2.0, 3.0]:
        ind.push(close=v)
    # (1*1 + 2*2 + 3*3) / (1+2+3) = 14/6
    assert ind.value == pytest.approx(14.0 / 6.0, rel=1e-5)


def test_wma_reset():
    ind = WMA_Indicator(3)
    for v in [1.0, 2.0, 3.0]:
        ind.push(close=v)
    ind.reset()
    assert not ind.is_ready
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
python -m pytest tests/indicator/test_ma.py -v
```

Expected: `ImportError`

- [ ] **Step 3: 实现 `ma.py`**

新建 `alphaQuantSystem/indicator/ma.py`：

```python
"""
indicator.ma
============
均线类增量指标对象（实盘 push 式）。

调用关系：
    - 继承 indicator.base.BaseIndicator。
    - 内部使用 collections.deque 积累数据，满 period 后调用 indicator.primitive 完成计算。
    - 由策略层（strategy.template.BaseStrategy 子类）的 on_init/on_bar 调用。
"""
from __future__ import annotations
from collections import deque
from typing import Optional

from alphaQuantSystem.indicator.base import BaseIndicator
from alphaQuantSystem.indicator import primitive as _p


class SMA_Indicator(BaseIndicator):
    """简单移动平均（Simple Moving Average）增量对象。

    Args:
        period: 计算周期（根数）。
    """

    def __init__(self, period: int) -> None:
        if period < 1:
            raise ValueError(f"period 必须 >= 1，got {period}")
        self._period = period
        self._buf: deque = deque(maxlen=period)

    def push(self, close: float) -> None:
        """喂入一根 bar 的收盘价。"""
        self._buf.append(float(close))

    @property
    def is_ready(self) -> bool:
        return len(self._buf) >= self._period

    @property
    def value(self) -> Optional[float]:
        if not self.is_ready:
            return None
        return sum(self._buf) / self._period

    def reset(self) -> None:
        self._buf.clear()


class EMA_Indicator(BaseIndicator):
    """指数移动平均（Exponential Moving Average）增量对象。

    alpha = 2 / (period + 1)，使用在线递推方式计算，避免每次重算全序列。

    Args:
        period: 计算周期。
    """

    def __init__(self, period: int) -> None:
        if period < 1:
            raise ValueError(f"period 必须 >= 1，got {period}")
        self._period = period
        self._alpha = 2.0 / (period + 1)
        self._count: int = 0
        self._ema: Optional[float] = None

    def push(self, close: float) -> None:
        """喂入一根 bar 的收盘价，在线递推 EMA。"""
        close = float(close)
        self._count += 1
        if self._ema is None:
            self._ema = close
        else:
            self._ema = self._alpha * close + (1 - self._alpha) * self._ema

    @property
    def is_ready(self) -> bool:
        return self._count >= self._period

    @property
    def value(self) -> Optional[float]:
        if not self.is_ready:
            return None
        return self._ema

    def reset(self) -> None:
        self._count = 0
        self._ema = None


class WMA_Indicator(BaseIndicator):
    """线性加权移动平均（Weighted Moving Average）增量对象。

    权重：最近一根权重最大（= period），最早一根权重最小（= 1）。
    Yn = (1*X_oldest + ... + N*X_newest) / (1+2+...+N)

    Args:
        period: 计算周期。
    """

    def __init__(self, period: int) -> None:
        if period < 1:
            raise ValueError(f"period 必须 >= 1，got {period}")
        self._period = period
        self._buf: deque = deque(maxlen=period)
        # 预计算权重分母
        self._denom = period * (period + 1) / 2

    def push(self, close: float) -> None:
        """喂入一根 bar 的收盘价。"""
        self._buf.append(float(close))

    @property
    def is_ready(self) -> bool:
        return len(self._buf) >= self._period

    @property
    def value(self) -> Optional[float]:
        if not self.is_ready:
            return None
        # 最旧的元素权重为 1，最新的为 period
        total = sum(w * v for w, v in enumerate(self._buf, start=1))
        return total / self._denom

    def reset(self) -> None:
        self._buf.clear()
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
python -m pytest tests/indicator/test_ma.py -v
```

Expected: 全部通过（约 12 个用例）

- [ ] **Step 5: Commit**

```bash
git add alphaQuantSystem/indicator/ma.py tests/indicator/test_ma.py
git commit -m "feat(indicator): 新增均线增量对象 SMA/EMA/WMA_Indicator"
```

---

## Task 5: `momentum.py` — 振荡势能增量对象

**Files:**
- Create: `alphaQuantSystem/indicator/momentum.py`
- Test: `tests/indicator/test_momentum.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/indicator/test_momentum.py`：

```python
"""振荡势能增量对象测试"""
import pytest
from alphaQuantSystem.indicator.momentum import (
    RSI_Indicator, MACD_Indicator, KDJ_Indicator,
    CCI_Indicator, ATR_Indicator,
)

# 用一段真实感价格序列辅助测试
_CLOSES = [
    10.0, 10.5, 10.2, 10.8, 11.0, 10.7, 10.9, 11.2,
    11.5, 11.3, 11.6, 11.8, 12.0, 11.9, 12.1, 12.3,
    12.0, 12.4, 12.2, 12.5, 12.7, 12.6, 12.8, 13.0,
    12.9, 13.2, 13.1, 13.4, 13.6, 13.5,
]
_HIGHS = [c + 0.3 for c in _CLOSES]
_LOWS  = [c - 0.3 for c in _CLOSES]


# ---------- RSI_Indicator ----------

def test_rsi_not_ready_before_enough_bars():
    ind = RSI_Indicator(n1=6, n2=12, n3=24)
    for c in _CLOSES[:23]:
        ind.push(close=c)
    assert not ind.is_ready


def test_rsi_ready_after_n3_bars():
    ind = RSI_Indicator(n1=6, n2=12, n3=24)
    for c in _CLOSES[:24]:
        ind.push(close=c)
    assert ind.is_ready
    r1, r2, r3 = ind.value
    # RSI 值域 [0, 100]
    assert 0 <= r1 <= 100
    assert 0 <= r2 <= 100
    assert 0 <= r3 <= 100


def test_rsi_reset():
    ind = RSI_Indicator(n1=6, n2=12, n3=24)
    for c in _CLOSES:
        ind.push(close=c)
    ind.reset()
    assert not ind.is_ready


# ---------- MACD_Indicator ----------

def test_macd_not_ready_before_enough_bars():
    ind = MACD_Indicator(short=12, long=26, mid=9)
    for c in _CLOSES[:26]:
        ind.push(close=c)
    assert not ind.is_ready


def test_macd_ready_after_long_plus_mid_bars():
    ind = MACD_Indicator(short=12, long=26, mid=9)
    # 至少需要 long + mid = 35 根，此处用 30 根再补充
    closes = _CLOSES * 2  # 60 根
    for c in closes:
        ind.push(close=c)
    assert ind.is_ready
    dif, dea, hist = ind.value
    assert isinstance(dif, float)
    assert isinstance(dea, float)
    assert hist == pytest.approx((dif - dea) * 2, rel=1e-6)


def test_macd_reset():
    ind = MACD_Indicator()
    for c in _CLOSES * 2:
        ind.push(close=c)
    ind.reset()
    assert not ind.is_ready


# ---------- KDJ_Indicator ----------

def test_kdj_not_ready_before_n_bars():
    ind = KDJ_Indicator(n=9)
    for i in range(8):
        ind.push(close=_CLOSES[i], high=_HIGHS[i], low=_LOWS[i])
    assert not ind.is_ready


def test_kdj_ready_after_n_bars():
    ind = KDJ_Indicator(n=9)
    for i in range(9):
        ind.push(close=_CLOSES[i], high=_HIGHS[i], low=_LOWS[i])
    assert ind.is_ready
    k, d, j = ind.value
    assert isinstance(k, float)
    assert isinstance(d, float)
    assert isinstance(j, float)


def test_kdj_reset():
    ind = KDJ_Indicator(n=9)
    for i in range(9):
        ind.push(close=_CLOSES[i], high=_HIGHS[i], low=_LOWS[i])
    ind.reset()
    assert not ind.is_ready


# ---------- CCI_Indicator ----------

def test_cci_not_ready_before_n_bars():
    ind = CCI_Indicator(n=14)
    for i in range(13):
        ind.push(close=_CLOSES[i], high=_HIGHS[i], low=_LOWS[i])
    assert not ind.is_ready


def test_cci_ready_after_n_bars():
    ind = CCI_Indicator(n=14)
    for i in range(14):
        ind.push(close=_CLOSES[i], high=_HIGHS[i], low=_LOWS[i])
    assert ind.is_ready
    val = ind.value
    assert isinstance(val, float)


def test_cci_reset():
    ind = CCI_Indicator(n=14)
    for i in range(14):
        ind.push(close=_CLOSES[i], high=_HIGHS[i], low=_LOWS[i])
    ind.reset()
    assert not ind.is_ready


# ---------- ATR_Indicator ----------

def test_atr_not_ready_before_n_bars():
    ind = ATR_Indicator(n=14)
    for i in range(13):
        ind.push(close=_CLOSES[i], high=_HIGHS[i], low=_LOWS[i])
    assert not ind.is_ready


def test_atr_ready_after_n_bars():
    ind = ATR_Indicator(n=14)
    for i in range(14):
        ind.push(close=_CLOSES[i], high=_HIGHS[i], low=_LOWS[i])
    assert ind.is_ready
    val = ind.value
    assert isinstance(val, float)
    assert val >= 0


def test_atr_reset():
    ind = ATR_Indicator(n=14)
    for i in range(14):
        ind.push(close=_CLOSES[i], high=_HIGHS[i], low=_LOWS[i])
    ind.reset()
    assert not ind.is_ready
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
python -m pytest tests/indicator/test_momentum.py -v
```

Expected: `ImportError`

- [ ] **Step 3: 实现 `momentum.py`**

新建 `alphaQuantSystem/indicator/momentum.py`：

```python
"""
indicator.momentum
==================
振荡势能类增量指标对象（实盘 push 式）。

调用关系：
    - 继承 indicator.base.BaseIndicator。
    - 内部使用 deque 积累 close/high/low 序列，就绪后调用 indicator.primitive 完成批量计算，
      取最后一个值作为当前值返回。
    - 由策略层（strategy.template.BaseStrategy 子类）的 on_init/on_bar 调用。
"""
from __future__ import annotations
from collections import deque
from typing import Optional, Tuple

import numpy as np

from alphaQuantSystem.indicator.base import BaseIndicator
from alphaQuantSystem.indicator import primitive as _p


class RSI_Indicator(BaseIndicator):
    """相对强弱指标（RSI）增量对象，同时计算三个周期。

    Args:
        n1: 短周期，默认 6。
        n2: 中周期，默认 12。
        n3: 长周期，默认 24。
    """

    def __init__(self, n1: int = 6, n2: int = 12, n3: int = 24) -> None:
        self._n1, self._n2, self._n3 = n1, n2, n3
        # 需要积累足够多数据让 SMA 稳定，取 n3 * 4 作为 buf 大小
        self._maxlen = max(n3 * 4, 120)
        self._buf: deque = deque(maxlen=self._maxlen)
        self._count: int = 0

    def push(self, close: float) -> None:
        self._buf.append(float(close))
        self._count += 1

    @property
    def is_ready(self) -> bool:
        return self._count >= self._n3

    @property
    def value(self) -> Optional[Tuple[float, float, float]]:
        if not self.is_ready:
            return None
        arr = np.array(self._buf)
        lc = _p.REF(arr, 1)
        rsi1 = _p.SMA(_p.MAX(arr - lc, 0), self._n1, 1) / \
               _p.SMA(_p.ABS(arr - lc), self._n1, 1) * 100
        rsi2 = _p.SMA(_p.MAX(arr - lc, 0), self._n2, 1) / \
               _p.SMA(_p.ABS(arr - lc), self._n2, 1) * 100
        rsi3 = _p.SMA(_p.MAX(arr - lc, 0), self._n3, 1) / \
               _p.SMA(_p.ABS(arr - lc), self._n3, 1) * 100
        return float(rsi1[-1]), float(rsi2[-1]), float(rsi3[-1])

    def reset(self) -> None:
        self._buf.clear()
        self._count = 0


class MACD_Indicator(BaseIndicator):
    """平滑异同平均线（MACD）增量对象。

    输出 DIF（快慢线差）、DEA（信号线）、MACD 柱（histogram = (DIF-DEA)*2）。

    Args:
        short: 短期 EMA 周期，默认 12。
        long:  长期 EMA 周期，默认 26。
        mid:   DEA 平滑周期，默认 9。
    """

    def __init__(self, short: int = 12, long: int = 26, mid: int = 9) -> None:
        self._short, self._long, self._mid = short, long, mid
        self._maxlen = (long + mid) * 4
        self._buf: deque = deque(maxlen=self._maxlen)
        self._count: int = 0

    def push(self, close: float) -> None:
        self._buf.append(float(close))
        self._count += 1

    @property
    def is_ready(self) -> bool:
        return self._count >= self._long + self._mid

    @property
    def value(self) -> Optional[Tuple[float, float, float]]:
        if not self.is_ready:
            return None
        arr = np.array(self._buf)
        dif_arr = _p.EMA(arr, self._short) - _p.EMA(arr, self._long)
        dea_arr = _p.EMA(dif_arr, self._mid)
        macd_arr = (dif_arr - dea_arr) * 2
        return float(dif_arr[-1]), float(dea_arr[-1]), float(macd_arr[-1])

    def reset(self) -> None:
        self._buf.clear()
        self._count = 0


class KDJ_Indicator(BaseIndicator):
    """随机指标 KDJ 增量对象。

    Args:
        n:  RSV 计算周期，默认 9。
        m1: K 值平滑周期，默认 3。
        m2: D 值平滑周期，默认 3。
    """

    def __init__(self, n: int = 9, m1: int = 3, m2: int = 3) -> None:
        self._n, self._m1, self._m2 = n, m1, m2
        self._maxlen = max(n * 4, 60)
        self._close_buf: deque = deque(maxlen=self._maxlen)
        self._high_buf: deque = deque(maxlen=self._maxlen)
        self._low_buf: deque = deque(maxlen=self._maxlen)
        self._count: int = 0

    def push(self, close: float, high: float, low: float) -> None:
        self._close_buf.append(float(close))
        self._high_buf.append(float(high))
        self._low_buf.append(float(low))
        self._count += 1

    @property
    def is_ready(self) -> bool:
        return self._count >= self._n

    @property
    def value(self) -> Optional[Tuple[float, float, float]]:
        if not self.is_ready:
            return None
        close = np.array(self._close_buf)
        high = np.array(self._high_buf)
        low = np.array(self._low_buf)
        llv = _p.LLV(low, self._n)
        hhv = _p.HHV(high, self._n)
        denom = hhv - llv
        # 分母为 0 时（极端行情）用 1 避免除零
        denom = np.where(denom == 0, 1.0, denom)
        rsv = (close - llv) / denom * 100
        k = _p.SMA(rsv, self._m1, 1)
        d = _p.SMA(k, self._m2, 1)
        j = 3 * k - 2 * d
        return float(k[-1]), float(d[-1]), float(j[-1])

    def reset(self) -> None:
        self._close_buf.clear()
        self._high_buf.clear()
        self._low_buf.clear()
        self._count = 0


class CCI_Indicator(BaseIndicator):
    """商品路径指标（CCI）增量对象。

    TYP = (HIGH + LOW + CLOSE) / 3
    CCI = (TYP - MA(TYP, N)) * 1000 / (15 * AVEDEV(TYP, N))

    Args:
        n: 计算周期，默认 14。
    """

    def __init__(self, n: int = 14) -> None:
        self._n = n
        self._close_buf: deque = deque(maxlen=n * 4)
        self._high_buf: deque = deque(maxlen=n * 4)
        self._low_buf: deque = deque(maxlen=n * 4)
        self._count: int = 0

    def push(self, close: float, high: float, low: float) -> None:
        self._close_buf.append(float(close))
        self._high_buf.append(float(high))
        self._low_buf.append(float(low))
        self._count += 1

    @property
    def is_ready(self) -> bool:
        return self._count >= self._n

    @property
    def value(self) -> Optional[float]:
        if not self.is_ready:
            return None
        close = np.array(self._close_buf)
        high = np.array(self._high_buf)
        low = np.array(self._low_buf)
        typ = (high + low + close) / 3
        ma = _p.MA(typ, self._n)
        avedev = _p.AVEDEV(typ, self._n)
        # 避免除零
        avedev_safe = np.where(avedev == 0, 1e-10, avedev)
        cci = (typ - ma) * 1000 / (15 * avedev_safe)
        return float(cci[-1])

    def reset(self) -> None:
        self._close_buf.clear()
        self._high_buf.clear()
        self._low_buf.clear()
        self._count = 0


class ATR_Indicator(BaseIndicator):
    """真实波幅（ATR）增量对象。

    MTR = MAX(MAX(HIGH-LOW, |REF(CLOSE,1)-HIGH|), |REF(CLOSE,1)-LOW|)
    ATR = MA(MTR, N)

    Args:
        n: 计算周期，默认 14。
    """

    def __init__(self, n: int = 14) -> None:
        self._n = n
        self._close_buf: deque = deque(maxlen=n * 4)
        self._high_buf: deque = deque(maxlen=n * 4)
        self._low_buf: deque = deque(maxlen=n * 4)
        self._count: int = 0

    def push(self, close: float, high: float, low: float) -> None:
        self._close_buf.append(float(close))
        self._high_buf.append(float(high))
        self._low_buf.append(float(low))
        self._count += 1

    @property
    def is_ready(self) -> bool:
        return self._count >= self._n

    @property
    def value(self) -> Optional[float]:
        if not self.is_ready:
            return None
        close = np.array(self._close_buf)
        high = np.array(self._high_buf)
        low = np.array(self._low_buf)
        prev_close = _p.REF(close, 1)
        mtr = _p.MAX(_p.MAX(high - low, _p.ABS(prev_close - high)),
                     _p.ABS(prev_close - low))
        atr = _p.MA(mtr, self._n)
        return float(atr[-1])

    def reset(self) -> None:
        self._close_buf.clear()
        self._high_buf.clear()
        self._low_buf.clear()
        self._count = 0
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
python -m pytest tests/indicator/test_momentum.py -v
```

Expected: 全部通过（约 15 个用例）

- [ ] **Step 5: Commit**

```bash
git add alphaQuantSystem/indicator/momentum.py tests/indicator/test_momentum.py
git commit -m "feat(indicator): 新增振荡势能增量对象 RSI/MACD/KDJ/CCI/ATR_Indicator"
```

---

## Task 6: `functional.py` — 函数式批量接口

**Files:**
- Create: `alphaQuantSystem/indicator/functional.py`
- Test: `tests/indicator/test_functional.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/indicator/test_functional.py`：

```python
"""函数式批量接口测试"""
import numpy as np
import pandas as pd
import pytest
from alphaQuantSystem.indicator import functional as ind


def _make_df(n: int = 60) -> pd.DataFrame:
    """生成测试用 OHLCV DataFrame。"""
    np.random.seed(42)
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    return pd.DataFrame({
        'open':   close - np.abs(np.random.randn(n) * 0.2),
        'high':   close + np.abs(np.random.randn(n) * 0.3),
        'low':    close - np.abs(np.random.randn(n) * 0.3),
        'close':  close,
        'volume': np.random.randint(1000, 10000, n).astype(float),
    })


# ---------- 均线 ----------

def test_sma_returns_series():
    df = _make_df()
    result = ind.sma(df, 5)
    assert isinstance(result, pd.Series)
    assert len(result) == len(df)
    # 前 4 根为 NaN
    assert result.iloc[:4].isna().all()
    assert not pd.isna(result.iloc[-1])


def test_ema_returns_series():
    df = _make_df()
    result = ind.ema(df, 10)
    assert isinstance(result, pd.Series)
    assert not pd.isna(result.iloc[-1])


def test_wma_returns_series():
    df = _make_df()
    result = ind.wma(df, 5)
    assert isinstance(result, pd.Series)
    assert not pd.isna(result.iloc[-1])


# ---------- MACD ----------

def test_macd_returns_three_series():
    df = _make_df(60)
    dif, dea, hist = ind.macd(df)
    assert isinstance(dif, pd.Series)
    assert isinstance(dea, pd.Series)
    assert isinstance(hist, pd.Series)
    assert len(dif) == len(df)
    # hist = (dif - dea) * 2
    valid = ~(dif.isna() | dea.isna())
    np.testing.assert_allclose(
        hist[valid].values,
        ((dif - dea) * 2)[valid].values,
        rtol=1e-5,
    )


# ---------- RSI ----------

def test_rsi_returns_three_series():
    df = _make_df(60)
    r1, r2, r3 = ind.rsi(df)
    assert isinstance(r1, pd.Series)
    # RSI 值域 [0, 100]
    valid = ~r1.isna()
    assert (r1[valid] >= 0).all() and (r1[valid] <= 100).all()


# ---------- KDJ ----------

def test_kdj_returns_three_series():
    df = _make_df(30)
    k, d, j = ind.kdj(df)
    assert isinstance(k, pd.Series)
    assert isinstance(d, pd.Series)
    assert isinstance(j, pd.Series)
    assert len(k) == len(df)


# ---------- CCI ----------

def test_cci_returns_series():
    df = _make_df(30)
    result = ind.cci(df)
    assert isinstance(result, pd.Series)
    assert not pd.isna(result.iloc[-1])


# ---------- ATR ----------

def test_atr_returns_series():
    df = _make_df(30)
    result = ind.atr(df)
    assert isinstance(result, pd.Series)
    assert not pd.isna(result.iloc[-1])
    # ATR 必须非负
    valid = ~result.isna()
    assert (result[valid] >= 0).all()


# ---------- 缺失列异常 ----------

def test_missing_close_column_raises():
    df = pd.DataFrame({'open': [1.0, 2.0]})
    with pytest.raises(KeyError):
        ind.sma(df, 5)
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
python -m pytest tests/indicator/test_functional.py -v
```

Expected: `ImportError` 或 `AttributeError`

- [ ] **Step 3: 实现 `functional.py`**

新建 `alphaQuantSystem/indicator/functional.py`：

```python
"""
indicator.functional
====================
函数式批量指标接口（DataFrame-in → pd.Series/tuple）。

优先使用 TA-Lib（若已安装），否则 fallback 到 indicator.primitive 实现。
调用方无需关心底层实现，接口完全一致。

调用关系：
    - 由回测层（backtest/）、绩效分析层（analyze/）以及策略 on_init 批量预处理时调用。
    - 内部调用 indicator.primitive 的序列函数（talib 不可用时）。
"""
from __future__ import annotations
from typing import Tuple

import numpy as np
import pandas as pd

from alphaQuantSystem.indicator import primitive as _p

# talib 可选依赖检测
try:
    import talib as _talib
    _HAS_TALIB = True
except ImportError:
    _HAS_TALIB = False


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _to_array(series) -> np.ndarray:
    """将 pd.Series 或 list/ndarray 转为 float64 ndarray。"""
    return np.asarray(series, dtype=np.float64)


def _wrap(arr: np.ndarray, index) -> pd.Series:
    """将 ndarray 封装为与原 DataFrame 同索引的 pd.Series。"""
    return pd.Series(arr, index=index)


# ---------------------------------------------------------------------------
# 均线
# ---------------------------------------------------------------------------

def sma(df: pd.DataFrame, period: int) -> pd.Series:
    """简单移动平均。

    Args:
        df:     含 ``close`` 列的 DataFrame。
        period: 计算周期。

    Returns:
        与 df 同长度的 pd.Series，前 period-1 根为 NaN。
    """
    close = _to_array(df['close'])
    if _HAS_TALIB:
        result = _talib.SMA(close, timeperiod=period)
    else:
        result = _p.MA(close, period)
    return _wrap(result, df.index)


def ema(df: pd.DataFrame, period: int) -> pd.Series:
    """指数移动平均。

    Args:
        df:     含 ``close`` 列的 DataFrame。
        period: 计算周期。

    Returns:
        与 df 同长度的 pd.Series。
    """
    close = _to_array(df['close'])
    if _HAS_TALIB:
        result = _talib.EMA(close, timeperiod=period)
    else:
        result = _p.EMA(close, period)
    return _wrap(result, df.index)


def wma(df: pd.DataFrame, period: int) -> pd.Series:
    """线性加权移动平均。

    Args:
        df:     含 ``close`` 列的 DataFrame。
        period: 计算周期。

    Returns:
        与 df 同长度的 pd.Series。
    """
    close = _to_array(df['close'])
    if _HAS_TALIB:
        result = _talib.WMA(close, timeperiod=period)
    else:
        result = _p.WMA(close, period)
    return _wrap(result, df.index)


# ---------------------------------------------------------------------------
# 振荡势能
# ---------------------------------------------------------------------------

def macd(
    df: pd.DataFrame,
    short: int = 12,
    long: int = 26,
    mid: int = 9,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """平滑异同平均线 MACD。

    Args:
        df:    含 ``close`` 列的 DataFrame。
        short: 快线 EMA 周期，默认 12。
        long:  慢线 EMA 周期，默认 26。
        mid:   信号线 EMA 周期，默认 9。

    Returns:
        (DIF, DEA, MACD柱)，均为与 df 同长度的 pd.Series。
        MACD柱 = (DIF - DEA) * 2。
    """
    close = _to_array(df['close'])
    if _HAS_TALIB:
        dif, dea, hist = _talib.MACD(close, fastperiod=short, slowperiod=long, signalperiod=mid)
    else:
        dif_arr = _p.EMA(close, short) - _p.EMA(close, long)
        dea_arr = _p.EMA(dif_arr, mid)
        hist_arr = (dif_arr - dea_arr) * 2
        dif, dea, hist = dif_arr, dea_arr, hist_arr
    return _wrap(dif, df.index), _wrap(dea, df.index), _wrap(hist, df.index)


def rsi(
    df: pd.DataFrame,
    n1: int = 6,
    n2: int = 12,
    n3: int = 24,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """相对强弱指标 RSI（三周期）。

    Args:
        df: 含 ``close`` 列的 DataFrame。
        n1: 短周期，默认 6。
        n2: 中周期，默认 12。
        n3: 长周期，默认 24。

    Returns:
        (RSI1, RSI2, RSI3)，均为与 df 同长度的 pd.Series，值域 [0, 100]。
    """
    close = _to_array(df['close'])
    if _HAS_TALIB:
        r1 = _talib.RSI(close, timeperiod=n1)
        r2 = _talib.RSI(close, timeperiod=n2)
        r3 = _talib.RSI(close, timeperiod=n3)
    else:
        lc = _p.REF(close, 1)
        r1 = _p.SMA(_p.MAX(close - lc, 0), n1, 1) / \
             _p.SMA(_p.ABS(close - lc), n1, 1) * 100
        r2 = _p.SMA(_p.MAX(close - lc, 0), n2, 1) / \
             _p.SMA(_p.ABS(close - lc), n2, 1) * 100
        r3 = _p.SMA(_p.MAX(close - lc, 0), n3, 1) / \
             _p.SMA(_p.ABS(close - lc), n3, 1) * 100
    return _wrap(r1, df.index), _wrap(r2, df.index), _wrap(r3, df.index)


def kdj(
    df: pd.DataFrame,
    n: int = 9,
    m1: int = 3,
    m2: int = 3,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """随机指标 KDJ。

    Args:
        df: 含 ``close`` / ``high`` / ``low`` 列的 DataFrame。
        n:  RSV 周期，默认 9。
        m1: K 值平滑，默认 3。
        m2: D 值平滑，默认 3。

    Returns:
        (K, D, J)，均为与 df 同长度的 pd.Series。
    """
    close = _to_array(df['close'])
    high  = _to_array(df['high'])
    low   = _to_array(df['low'])
    if _HAS_TALIB:
        # talib 的 STOCH 返回 slowk, slowd；J 需手动计算
        k_arr, d_arr = _talib.STOCH(
            high, low, close,
            fastk_period=n,
            slowk_period=m1, slowk_matype=1,
            slowd_period=m2, slowd_matype=1,
        )
        j_arr = 3 * k_arr - 2 * d_arr
    else:
        llv = _p.LLV(low, n)
        hhv = _p.HHV(high, n)
        denom = hhv - llv
        denom = np.where(denom == 0, 1e-10, denom)
        rsv = (close - llv) / denom * 100
        k_arr = _p.SMA(rsv, m1, 1)
        d_arr = _p.SMA(k_arr, m2, 1)
        j_arr = 3 * k_arr - 2 * d_arr
    return _wrap(k_arr, df.index), _wrap(d_arr, df.index), _wrap(j_arr, df.index)


def cci(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """商品路径指标 CCI。

    Args:
        df: 含 ``close`` / ``high`` / ``low`` 列的 DataFrame。
        n:  计算周期，默认 14。

    Returns:
        与 df 同长度的 pd.Series。
    """
    close = _to_array(df['close'])
    high  = _to_array(df['high'])
    low   = _to_array(df['low'])
    if _HAS_TALIB:
        result = _talib.CCI(high, low, close, timeperiod=n)
    else:
        typ = (high + low + close) / 3
        ma = _p.MA(typ, n)
        avedev = _p.AVEDEV(typ, n)
        avedev_safe = np.where(avedev == 0, 1e-10, avedev)
        result = (typ - ma) * 1000 / (15 * avedev_safe)
    return _wrap(result, df.index)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """真实波幅 ATR。

    Args:
        df: 含 ``close`` / ``high`` / ``low`` 列的 DataFrame。
        n:  计算周期，默认 14。

    Returns:
        与 df 同长度的 pd.Series，值非负。
    """
    close = _to_array(df['close'])
    high  = _to_array(df['high'])
    low   = _to_array(df['low'])
    if _HAS_TALIB:
        result = _talib.ATR(high, low, close, timeperiod=n)
    else:
        prev_close = _p.REF(close, 1)
        mtr = _p.MAX(_p.MAX(high - low, _p.ABS(prev_close - high)),
                     _p.ABS(prev_close - low))
        result = _p.MA(mtr, n)
    return _wrap(result, df.index)
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
python -m pytest tests/indicator/test_functional.py -v
```

Expected: 全部通过（约 10 个用例）

- [ ] **Step 5: Commit**

```bash
git add alphaQuantSystem/indicator/functional.py tests/indicator/test_functional.py
git commit -m "feat(indicator): 新增 functional 批量函数式接口（含 talib 适配）"
```

---

## Task 7: `__init__.py` — 统一导出

**Files:**
- Modify: `alphaQuantSystem/indicator/__init__.py`

- [ ] **Step 1: 写失败测试**

在 `tests/indicator/test_functional.py` 末尾追加（或新建 `tests/indicator/test_init.py`）：

```python
# tests/indicator/test_init.py
"""验证 indicator 模块统一导出"""


def test_public_api_importable():
    from alphaQuantSystem.indicator import (
        BaseIndicator,
        SMA_Indicator, EMA_Indicator, WMA_Indicator,
        RSI_Indicator, MACD_Indicator, KDJ_Indicator,
        CCI_Indicator, ATR_Indicator,
        functional,
        primitive,
    )
    assert BaseIndicator is not None
    assert SMA_Indicator is not None
    assert functional is not None
    assert primitive is not None
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
python -m pytest tests/indicator/test_init.py -v
```

Expected: `ImportError`

- [ ] **Step 3: 填充 `__init__.py`**

编辑 `alphaQuantSystem/indicator/__init__.py`：

```python
"""
indicator
=========
技术指标模块，提供双模式指标计算：

- **增量式（push）**：适合实盘 on_bar 逐 bar 驱动
  ``ind = MACD_Indicator(); ind.push(close=bar.close); ind.value``

- **函数式（functional）**：适合回测/分析，传入 DataFrame 批量计算
  ``from alphaQuantSystem.indicator import functional as ind; ind.macd(df)``

调用关系：
    - 策略层（strategy.template.BaseStrategy 子类）通过增量式对象使用。
    - 回测层（backtest/）、绩效层（analyze/）通过 functional 模块使用。
"""
from alphaQuantSystem.indicator.base import BaseIndicator
from alphaQuantSystem.indicator.ma import SMA_Indicator, EMA_Indicator, WMA_Indicator
from alphaQuantSystem.indicator.momentum import (
    RSI_Indicator,
    MACD_Indicator,
    KDJ_Indicator,
    CCI_Indicator,
    ATR_Indicator,
)
from alphaQuantSystem.indicator import functional
from alphaQuantSystem.indicator import primitive

__all__ = [
    'BaseIndicator',
    'SMA_Indicator', 'EMA_Indicator', 'WMA_Indicator',
    'RSI_Indicator', 'MACD_Indicator', 'KDJ_Indicator',
    'CCI_Indicator', 'ATR_Indicator',
    'functional',
    'primitive',
]
```

- [ ] **Step 4: 运行全部指标测试**

```bash
python -m pytest tests/indicator/ -v
```

Expected: 全部通过（约 40+ 个用例）

- [ ] **Step 5: Commit**

```bash
git add alphaQuantSystem/indicator/__init__.py tests/indicator/test_init.py
git commit -m "feat(indicator): 完成 indicator 模块统一导出，全部测试通过"
```

---

## 自检记录

**Spec coverage 检查：**
- ✅ 独立 `indicator/` 目录 → Task 1
- ✅ `BaseIndicator` 抽象基类 → Task 2
- ✅ `primitive.py` 底层序列函数（MA/EMA/SMA/WMA/STD/REF/DIFF/HHV/LLV/SUM/COUNT/AVEDEV/MAX/MIN/ABS/IF/CROSS） → Task 3
- ✅ 均线增量对象 SMA/EMA/WMA_Indicator → Task 4
- ✅ 振荡势能增量对象 RSI/MACD/KDJ/CCI/ATR_Indicator → Task 5
- ✅ 函数式接口 + talib 适配 → Task 6
- ✅ `__init__.py` 统一导出 → Task 7
- ✅ 自定义指标扩展（通过继承 `BaseIndicator`，已在 Task 2 测试中演示）

**Placeholder 扫描：** 无 TBD/TODO，所有 task 含完整代码。

**类型一致性：**
- `BaseIndicator.push(**kwargs)` → 各子类具化为 `push(close)` 或 `push(close, high, low)` ✅
- `BaseIndicator.value` 返回 `float | tuple | None` → 各子类文档与测试一致 ✅
- `functional` 各函数返回 `pd.Series` 或 `tuple[pd.Series]` → 测试验证 ✅
- `primitive` 函数均返回 `np.ndarray` → 测试验证 ✅
