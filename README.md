# alphaQuantSystem

> 事件驱动量化交易框架 | Event-Driven Quantitative Trading Framework

[![Python](https://img.shields.io/badge/Python-3.8%20%7C%203.9%20%7C%203.10-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

## 简介

alphaQuantSystem 是一个基于事件驱动架构的量化交易框架，支持**回测**与**实盘交易**双模式运行。框架整合 QMT（极速交易）、Backtrader 回测引擎、多数据源（QMT / 东方财富 / AkShare / 通达信），提供从策略开发、风控审核到绩效分析的一站式量化交易解决方案。

### 核心特性

- **事件驱动架构** — 参考 vnpy 设计，EventEngine 驱动整个系统
- **统一策略模板** — `BaseStrategy` + `StrategyContext` 模式，回测/实盘代码无需改动
- **风控优先** — 所有订单必经 `RiskGateway` 审核，支持多维度风控规则
- **App Builder** — 流畅的链式调用 API，快速组装量化应用
- **多数据源环形降级** — QMT → 东方财富 → AkShare → 通达信 自动切换
- **绩效分析** — 内置 metrics 模块 + QuantStats 可视化报告
- **实盘/回测双模式** — 一套代码，`mode="live"` / `mode="backtest"` 无缝切换

## 架构概览

```
alphaQuantSystem/
├── core/                # 【核心层】事件引擎、数据模型、配置管理
├── engine/              # 【引擎层】策略引擎、信号管线、调度、执行
├── gateway/             # 【接入层】QMT 行情 + 交易网关
├── data/                # 【数据层】多数据源引擎（QMT/东财/AkShare/TDX）
├── strategy/            # 【策略层】BaseStrategy 模板、工具函数
├── backtest/            # 【回测层】撮合引擎、报告生成、佣金模型
├── analyze/             # 【绩效层】metrics + QuantStats 绩效分析
├── trader/              # 【实盘层】QMT 实盘交易、持仓管理
├── risk/                # 【风控层】风险网关、规则引擎、风控策略
├── services/            # 【服务层】持仓/账户/风控服务
├── indicator/           # 【指标层】EMA/KDJ/Momentum 等技术指标
├── monitor/             # 【监控层】Loguru 日志、追踪调试
├── examples/            # 【示例】策略示例与回测用例
├── tests/               # 【测试】pytest 单元测试
├── utils/               # 【工具层】通用辅助函数
└── main.py              # 项目启动入口
```

## 快速开始

### 环境要求

- Python 3.8 ~ 3.10
- [QMT 客户端](https://www.xuntou.net/)（仅实盘交易需要）
- Windows 10+（QMT 依赖）

### 安装

```bash
# 1. 克隆仓库
git clone https://github.com/yourusername/alphaquantsystem.git
cd alphaquantsystem

# 2. 创建虚拟环境（推荐）
python -m venv venv
venv\Scripts\activate   # Windows
# source venv/bin/activate  # Linux/macOS

# 3. 安装依赖
pip install -r requirements.txt

# 4. 开发模式安装（可选，使 alphaQuantSystem 作为包可用）
pip install -e .
```

### QMT 配置（实盘）

实盘交易需要在 QMT 客户端中安装 xtquant SDK。将 QMT 安装目录下的 `xtquant/` 复制到 Python `site-packages` 或项目根目录。

### 5 分钟上手

**1. 编写策略** — 继承 `BaseStrategy`，重写 `on_bar()`：

```python
# my_strategy.py
from alphaQuantSystem import BaseStrategy
from alphaQuantSystem.core import BarData
from alphaQuantSystem.indicator.ma import EMA_Indicator

class DualEmaStrategy(BaseStrategy):
    fast_period: int = 5
    slow_period: int = 10

    def on_init(self):
        self.fast_ema = EMA_Indicator(self.fast_period)
        self.slow_ema = EMA_Indicator(self.slow_period)

    def on_bar(self, bar: BarData):
        self.fast_ema.push(bar.close)
        self.slow_ema.push(bar.close)
        if not (self.fast_ema.is_ready and self.slow_ema.is_ready):
            return

        if self.fast_ema.value > self.slow_ema.value:
            self.buy(bar.symbol, 1000, bar.close, reason="EMA交叉买入")
        else:
            self.sell(bar.symbol, 1000, bar.close, reason="EMA交叉卖出")
```

**2. 启动回测** — 使用 App Builder 运行：

```python
from alphaQuantSystem import App
from my_strategy import DualEmaStrategy

app = (App()
    .with_data(sources=["akshare"])
    .with_risk(max_order_notional=300000)
    .add_strategy(DualEmaStrategy, symbols=["510050.SH"], warmup_bars=20)
    .run(mode="backtest", start="20240101", end="20241231", initial_cash=1_000_000)
)
```

**3. 启动实盘**：

```python
app = (App()
    .use_qmt(is_live=True)
    .with_data(sources=["qmt"])
    .add_strategy(DualEmaStrategy, symbols=["510050.SH"])
    .run(mode="live")
)
```

## 策略生命周期

```
on_init()           # 初始化指标、参数
    ↓
on_warmup_bar()     # 预热阶段，填充指标历史值
    ↓
on_start()          # 策略开始运行
    ↓
on_bar() / on_tick()      # 收到行情数据
    ↓
buy() / sell()             # 生成交易信号
    ↓  (SignalPipeline)
    ↓  (RiskGateway 审核)
    ↓  (ExecutionHandler)
    ↓
on_trade() / on_order()   # 收到成交/订单回报
    ↓
on_stop()           # 策略停止
```

## 风控体系

框架提供多层风控，可在 `App` 层或策略层配置：

```python
app.with_risk(
    max_order_notional=300000,    # 单笔最大金额
    max_position_ratio=0.3,       # 单一标的持仓上限
    max_daily_loss=0.05,          # 日内最大亏损比例
    max_drawdown=0.15,            # 最大回撤
    stop_loss_pct=0.03,           # 固定止损
    forbidden_periods="09:30-09:35",  # 开盘禁买
)
```

可用风控规则见 `risk/policies/` 目录，支持自定义扩展。

## 数据源

| 数据源 | 用途 | 说明 |
|--------|------|------|
| QMT | 行情+交易 | 实盘首选，低延迟 |
| 东方财富 | A股行情 | 免费，日K + 实时快照 |
| AkShare | 多源数据 | 覆盖基金/债券/指数 |
| 通达信 | 本地数据 | 离线数据源 |

数据引擎支持**环形降级**：优先使用配置的主数据源，失败时自动切换备选。

## 项目配置

通过 `pyproject.toml` 管理包信息，`requirements.txt` 管理依赖。`CLAUDE.md` 定义了框架开发规范与核心协作准则。

## 开发

```bash
# 运行测试
pytest tests/ -v

# 运行回测
python main.py backtest

# 运行回测（调试模式）
python main.py backtest --debug-trades-only
```

## 路线图

- [x] 事件驱动引擎（EventEngine）
- [x] 统一策略模板 + StrategyContext
- [x] App Builder 链式 API
- [x] QMT 网关（行情 + 交易）
- [x] 多数据源环形降级
- [x] 风控网关 + 规则引擎
- [x] 撮合引擎（回测）
- [x] 绩效分析（Metrics + QuantStats）
- [ ] Trading Agent 智能分析
- [ ] Web 监控面板（Streamlit）
- [ ] FastAPI 策略管理接口

## 技术栈

| 组件 | 技术 |
|------|------|
| 回测引擎 | Backtrader |
| 实盘交易 | QMT xtquant |
| 事件引擎 | Threading + Queue |
| 数据来源 | QMT / 东方财富 / AkShare / 通达信 |
| 绩效分析 | QuantStats |
| 日志 | Loguru |
| API 框架 | FastAPI (预留) |
| Web 面板 | Streamlit (预留) |
| 语言 | Python 3.8~3.10 |

## 许可证

MIT License

## 致谢

- [vnpy](https://github.com/vnpy/vnpy) — 事件驱动架构设计参考
- [Backtrader](https://github.com/mementum/backtrader) — 回测引擎
- [QuantStats](https://github.com/ranaroussi/quantstats) — 绩效分析
