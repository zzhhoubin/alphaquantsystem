# AlphaQuant 示例与包路径说明

## 应使用的 Python 包

开发与运行策略时，请只 import 仓库根目录下的 **`alphaQuantSystem`** 包（与 `pyproject.toml` / 安装路径一致）。

以下目录为历史快照或并行副本，**不要**作为新代码的依赖目标：

- `alphaQuantSystem_1.0.1` / `alphaQuantSystem_1.0.2` / `alphaQuantSystem_1.0.3`
- 其他根目录下的旧版副本

若发现示例仍引用旧路径，请以 `alphaQuantSystem` 为准并迁移 import。

## 策略运行时（统一入口）

模块：`alphaQuantSystem.strategy.runtime`

- **`run_strategy(strategy_cls, mode, ...)`**：按 `live` / `backtest` 合并策略 YAML 与可选全局 `settings.yaml`，并注入 QMT 或回测所需回调。
- 策略 YAML 四段键：`strategy`（公共）、`live`、`backtest`、`app`（见 `strategy/strategy_yaml.py`）。

示例 YAML：[examples/strategies/runtime_ma5_example.yaml](strategies/runtime_ma5_example.yaml)。

最小调用示例（在项目根目录、已安装或可导入 `alphaQuantSystem` 时）：

```python
from alphaQuantSystem.examples.strategies.ma5_cross_live import Ma5CrossLiveStrategy
from alphaQuantSystem.strategy.runtime import run_strategy

run_strategy(
    Ma5CrossLiveStrategy,
    "backtest",
    strategy_yaml="alphaQuantSystem/examples/strategies/runtime_ma5_example.yaml",
)
```

实盘将 `mode` 改为 `"live"`，并按需传入 `settings_yaml=`、`is_real=True` 等参数。

## 更多示例脚本

见 [examples/strategies/](strategies/) 目录下各 `*_live.py`、回测脚本及 `ema_cross_full.yaml`。

- **双均线 + 统一入口**：包 [`strategies/double_ma/`](strategies/double_ma/)（`DoubleMaStrategy`、`double_ma_159509_runtime.yaml`、回测输出 JSON/HTML 同目录）；运行 ``python -m alphaQuantSystem.examples.strategies.double_ma``。兼容脚本：[`tests/double_ma_159509_runtime.py`](../tests/double_ma_159509_runtime.py)。

## 实时行情数据源（调研）

东财 push2 不稳定时的替代源对比、浏览器可点 URL、价格精度说明见：[docs/realtime_data_sources.md](../docs/realtime_data_sources.md)。当前 `DataEngine.get_realtime` 仍为东财 → QMT 两源。
