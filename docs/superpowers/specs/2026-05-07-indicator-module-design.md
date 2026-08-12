# 技术指标模块设计文档

**日期：** 2026-05-07  
**项目：** alphaQuantSystem  
**模块：** `indicator/`

---

## 1. 背景与目标

现有策略（如 `DoubleMaStrategy`）手动用 `deque` + `statistics.mean` 实现均线，缺乏统一的技术指标层。本模块目标：

- 为全链路（实盘策略、回测、绩效分析）提供统一的技术指标计算入口
- 支持 TA-Lib 三方库（可选依赖，无则自动 fallback）
- 支持用户自定义扩展指标
- 双模式：增量 push 式（适合实盘 on_bar 逐步驱动）+ 函数式（适合批量回测/分析）

---

## 2. 模块结构

```
alphaQuantSystem/
└── indicator/
    ├── __init__.py        # 统一导出所有公共 API
    ├── base.py            # BaseIndicator 抽象基类（增量 push 接口）
    ├── primitive.py       # 底层序列工具函数（无状态，numpy/pandas）
    ├── ma.py              # 均线指标增量对象：SMA_Indicator / EMA_Indicator / WMA_Indicator
    ├── momentum.py        # 振荡势能增量对象：RSI / MACD / KDJ / CCI / ATR
    └── functional.py      # 函数式批量接口（DataFrame-in），含 talib 适配层
```

---

## 3. 各文件职责

### 3.1 `base.py` — 抽象基类

定义增量指标的统一接口，所有增量指标类必须继承此基类。

```python
class BaseIndicator(ABC):
    @abstractmethod
    def push(self, **kwargs) -> None:
        """逐 bar 喂入数据"""

    @property
    @abstractmethod
    def value(self):
        """当前指标值；未就绪时返回 None"""

    @property
    @abstractmethod
    def is_ready(self) -> bool:
        """数据是否足够计算（已积累满 period 根 bar）"""

    def reset(self) -> None:
        """清空内部状态，供策略重启时调用"""
```

策略层固定调用模式：
```python
ind.push(close=bar.close)
if ind.is_ready:
    val = ind.value
```

---

### 3.2 `primitive.py` — 底层序列函数

无状态纯函数，输入 numpy array 或 list，返回 numpy array。  
移植自已验证的通达信公式实现（基于 `numpy` + `pandas`）。

**均线核心函数：**

| 函数 | 说明 |
|------|------|
| `MA(S, N)` | 简单移动平均 |
| `EMA(S, N)` | 指数移动平均（alpha=2/(N+1)） |
| `SMA(S, N, M=1)` | 中国式 SMA（通达信，alpha=M/N） |
| `WMA(S, N)` | 线性加权移动平均 |
| `STD(S, N)` | N 日滚动标准差（ddof=0） |

**序列操作函数：**

| 函数 | 说明 |
|------|------|
| `REF(S, N)` | 序列整体下移 N（shift） |
| `DIFF(S, N)` | 差分 |
| `HHV(S, N)` | N 日滚动最高值 |
| `LLV(S, N)` | N 日滚动最低值 |
| `SUM(S, N)` | N 日滚动累和（N=0 则历史累和） |
| `COUNT(S, N)` | N 日内满足布尔条件的天数 |
| `AVEDEV(S, N)` | 平均绝对偏差 |

**向量基本运算：**

| 函数 | 说明 |
|------|------|
| `MAX(S1, S2)` | 逐元素最大值 |
| `MIN(S1, S2)` | 逐元素最小值 |
| `ABS(S)` | 绝对值 |
| `IF(S, A, B)` | 条件选择（`np.where`） |

**交叉判断：**

| 函数 | 说明 |
|------|------|
| `CROSS(S1, S2)` | S1 上穿 S2（金叉） |
| `CROSS_UP(S1, S2)` | 同 CROSS |
| `CROSS_DOWN(S1, S2)` | S1 下穿 S2（死叉） |

---

### 3.3 `ma.py` — 均线增量对象

| 类 | 构造参数 | `push` 参数 | `value` 返回 |
|----|----------|-------------|--------------|
| `SMA_Indicator` | `period: int` | `close: float` | `float` |
| `EMA_Indicator` | `period: int` | `close: float` | `float` |
| `WMA_Indicator` | `period: int` | `close: float` | `float` |

内部使用 `collections.deque(maxlen=period)` 积累数据，`is_ready` 在积累满 `period` 根后为 `True`。

---

### 3.4 `momentum.py` — 振荡势能增量对象

| 类 | 构造参数 | `push` 参数 | `value` 返回 |
|----|----------|-------------|--------------|
| `RSI_Indicator` | `n1=6, n2=12, n3=24` | `close` | `(rsi1, rsi2, rsi3): tuple[float,float,float]` |
| `MACD_Indicator` | `short=12, long=26, mid=9` | `close` | `(dif, dea, macd): tuple[float,float,float]` |
| `KDJ_Indicator` | `n=9, m1=3, m2=3` | `close, high, low` | `(k, d, j): tuple[float,float,float]` |
| `CCI_Indicator` | `n=14` | `close, high, low` | `float` |
| `ATR_Indicator` | `n=14` | `close, high, low` | `float` |

`is_ready` 条件：积累根数 ≥ 所需最大周期（如 MACD 需 `long + mid` 根）。

---

### 3.5 `functional.py` — 函数式批量接口

接收 `pd.DataFrame`（需含 `close` 列，部分指标需 `high`/`low`），返回 `pd.Series` 或 `tuple[pd.Series]`。

```python
# 均线
def sma(df: pd.DataFrame, period: int) -> pd.Series
def ema(df: pd.DataFrame, period: int) -> pd.Series
def wma(df: pd.DataFrame, period: int) -> pd.Series

# 振荡势能
def macd(df, short=12, long=26, mid=9) -> tuple[pd.Series, pd.Series, pd.Series]
def rsi(df, n1=6, n2=12, n3=24) -> tuple[pd.Series, pd.Series, pd.Series]
def kdj(df, n=9, m1=3, m2=3) -> tuple[pd.Series, pd.Series, pd.Series]
def cci(df, n=14) -> pd.Series
def atr(df, n=14) -> pd.Series
```

**talib 适配策略：**

```python
try:
    import talib as _talib
    _HAS_TALIB = True
except ImportError:
    _HAS_TALIB = False
```

每个函数内部：有 talib 则调用 `talib.XXX()`，无则 fallback 到 `primitive.py`。调用方无感知。

---

### 3.6 `__init__.py` — 统一导出

```python
from .base import BaseIndicator
from .ma import SMA_Indicator, EMA_Indicator, WMA_Indicator
from .momentum import RSI_Indicator, MACD_Indicator, KDJ_Indicator, CCI_Indicator, ATR_Indicator
from . import functional
from . import primitive
```

---

## 4. 调用示例

### 实盘策略（增量式）

```python
from alphaQuantSystem.indicator import MACD_Indicator, RSI_Indicator

class MyStrategy(BaseStrategy):
    def on_init(self):
        self.macd = MACD_Indicator(12, 26, 9)
        self.rsi = RSI_Indicator(6, 12, 24)

    def on_bar(self, bar: BarData):
        # 逐 bar 喂数据
        self.macd.push(close=bar.close)
        self.rsi.push(close=bar.close)

        if not self.macd.is_ready:
            return

        dif, dea, hist = self.macd.value
        rsi1, rsi2, rsi3 = self.rsi.value

        if dif > dea and rsi1 < 30:
            self.buy(bar.symbol, 100, bar.close, reason="MACD金叉+RSI超卖")
```

### 回测/分析（函数式）

```python
from alphaQuantSystem.indicator import functional as ind

df = strategy.get_hist_data("000001.SZ", period="D")
dif, dea, hist = ind.macd(df)
rsi1, rsi2, rsi3 = ind.rsi(df)
```

### 自定义指标（继承 BaseIndicator）

```python
from alphaQuantSystem.indicator import BaseIndicator
from collections import deque

class MyCustomIndicator(BaseIndicator):
    def __init__(self, period: int):
        self._period = period
        self._buf = deque(maxlen=period)

    def push(self, close: float) -> None:
        self._buf.append(close)

    @property
    def is_ready(self) -> bool:
        return len(self._buf) >= self._period

    @property
    def value(self):
        if not self.is_ready:
            return None
        return sum(self._buf) / self._period
```

---

## 5. 依赖说明

| 依赖 | 是否必须 | 说明 |
|------|----------|------|
| `numpy` | 是 | 项目已有 |
| `pandas` | 是 | 项目已有 |
| `ta-lib` | 否 | 可选，有则优先使用；无则 fallback |

---

## 6. 文件新增清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `indicator/__init__.py` | 新增 | 统一导出 |
| `indicator/base.py` | 新增 | 抽象基类 |
| `indicator/primitive.py` | 新增 | 底层序列函数 |
| `indicator/ma.py` | 新增 | 均线增量对象 |
| `indicator/momentum.py` | 新增 | 振荡势能增量对象 |
| `indicator/functional.py` | 新增 | 批量函数式接口 + talib 适配 |

**不改动任何现有文件。**

---

## 7. 设计约束

- Python 3.8~3.10 兼容
- 遵循 PEP8
- 模块间调用需写注释说明调用关系
- 所有新增文件放在 `alphaQuantSystem/indicator/` 下，与其他模块平级
- 不引入除 `numpy`/`pandas`/`talib(可选)` 以外的新依赖
