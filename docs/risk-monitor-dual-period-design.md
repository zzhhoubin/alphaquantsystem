# 风控监控双周期架构设计

> 状态：方案文档（待实施）  
> 日期：2026-06-10  
> 背景：`main.py` 中 DualEma 使用日 K 出信号，而 `max_drawdown` 等风控若仅按日收盘评估，无法反映盘中已触及阈值的情况。不同策略对监控频率需求不同，需在框架层做策略周期与风控周期的解耦，并保持向后兼容。

---

## 一、目标与约束

### 目标

| 目标 | 说明 |
|------|------|
| 策略周期 ≠ 风控周期 | 例：DualEma 日 K 金叉/死叉；`max_drawdown` 用 1m 盯市并在盘中触发 |
| 按策略可配置 | 策略 A 盘中 1m/tick；策略 B 纯日终日线；互不影响 |
| 规则可细分（可选） | 盘中止损 + 日终单日亏损等混合场景 |
| 回测 / 实盘语义一致 | 同一套 `risk.monitor` 配置，数据源按 `scene` 分支 |

### 约束

- **阈值向后兼容**：不配 `monitor` 时，风控周期 = 策略周期，规则阈值语义不变。回测执行顺序统一改为「先风控、后策略」（§6.0），与现网不同，属有意变更；回归对比时需注意成交时点差异。
- **不改策略逻辑**：`dual_ema.py` 等策略文件无需为双周期单独写逻辑（除非策略自己要额外规则）。
- **配置归属正式包**：`risk/presets.py`、`risk/risk_limits.py`，不依赖 `tests/`。

---

## 二、现状与问题

### 当前链路

```
每根 bar 结束
  → strategy.on_bar(bar)              # 策略
  → pipeline.drain()
  → _run_post_bar_risk_monitoring(bar) # 风控
  → pipeline.drain()
```

回测默认 `StrategyReg.period = "D"`，且 `App.add_strategy()` / `engine.register()` 未暴露 `period` 参数。

### 局限

1. 策略、风控、撮合共用**同一 K 线周期**。
2. 风控盯市与强平成交价均使用 **`bar.close`**（日线即日收盘）。
3. 无「日 K 策略 + 分钟级风控」嵌套循环。
4. `max_drawdown` 基于账户权益；权益由持仓×当前 bar 收盘价估算。

因此无法实现：**日线 DualEma + 盘中 1m 触发 max_drawdown 止损**。

---

## 三、核心原则

1. **策略周期与风控监控周期解耦**，分别配置。
2. **默认保持现状**：无 `monitor` → `monitor.period = strategy.period`。
3. **配置在策略级**：每个 `StrategyReg` 自带 `risk` + `monitor`。
4. **规则可覆盖（可选 Phase）**：策略级默认 + `rule_monitors` 单条覆盖。
5. **回测 / 实盘同一配置结构**，执行层按场景分支。
6. **单策略运行**：当前一次只运行一个策略，不考虑多策略并行下的多周期数据协调问题。
7. **先风控、后买卖（回测）**：每个处理单元内先跑风控监控并 `drain` 落实强平，再执行策略 `on_bar` / 信号 `drain`；避免策略新买单抢先于风控卖单。
8. **先卖后买**：同一 `drain` 周期内，**卖出信号（含风控强平）优先于买入**；若策略同日既触发死叉又触发金叉，先平后开。

---

## 四、配置模型

在现有 `risk=dict`（如 `isolated_risk(...)`）上扩展，不破坏旧字段。

### 4.1 策略级默认 `monitor`

```python
risk = {
    # === 现有：规则阈值 ===
    "max_drawdown": 0.05,
    "stop_loss_pct": 0.01,

    # === 新增：监控调度（整策略默认）===
    "monitor": {
        "period": "1m",           # 监控 K 线：D | 1m | 5m | tick
        "when": "each_bar",       # each_bar | day_end
        "price": "close",         # close | low | high | last（tick）
    },

    # === 可选：单条规则覆盖（Phase 3）===
    "rule_monitors": {
        "drawdown_stop.max_drawdown": {"period": "1m", "price": "close"},
        "price_stop.stop_loss":       {"period": "1m", "price": "low"},
        "drawdown_stop.daily_loss_pct": {"period": "D", "when": "day_end"},
    },
}
```

`RiskLimits.DEFAULT_LIMITS` 中增加 `"monitor": None`：`None` 表示风控周期跟随策略周期（与当前行为一致），非 `None` 时须为包含 `period` 等字段的完整字典。

### 4.2 字段说明

| 字段 | 含义 | 默认 |
|------|------|------|
| `monitor.period` | 风控评估用的行情周期 | 等于 `strategy.period` |
| `monitor.when` | 触发时机 | `each_bar` |
| `monitor.price` | 盯市/成交价字段 | `close` |

**`monitor.when` 取值说明：**

| 值 | 含义 |
|----|------|
| `each_bar` | 每根监控 bar 结束时触发风控评估 |
| `day_end` | 当日最后一根监控 bar 触发；等价于 `when="each_bar"` + 策略周期日的最后时刻 |

**D 周期特殊处理**：当 `monitor.period="D"` 时，每根日 K 天然是当日最后一根 bar，因此传入 `RuleEngine.evaluate()` 的 `monitor_when` 应固定为 `"day_end"`——确保 `daily_loss_pct`、`consecutive_*` 等仅日终评估的规则不会被跳过。

1m/5m 等分钟周期正常区分 `each_bar`（盘中）与 `day_end`（14:59 或最后可交易分钟）。

不再保留 `session_end`（语义模糊，实盘阶段如需区分上下午收盘再引入）。

### 4.3 预设封装示例（`risk/presets.py`）

```python
# 日策略 + 分钟风控（DualEma + max_drawdown）
risk = production_risk(
    max_drawdown=0.05,
    monitor={"period": "1m", "price": "close"},
)

# 纯日终（与现在一致）
risk = production_risk(max_drawdown=0.05)

# 盘中止损偏保守（用 low）
risk = production_risk(
    stop_loss_pct=0.01,
    monitor={"period": "1m", "price": "low"},
)
```

### 4.4 兼容规则

- 无 `monitor` 键 → 风控周期 = 策略周期（阈值语义不变）。
- 现有 `isolated_risk(max_drawdown=0.05)` 配置可继续使用。
- **执行顺序**：Phase 1 起回测统一为 **先风控、后策略**（§6.0），与现网 `_process_bar` 不同，属有意变更；回测成交时点可能略有差异，需回归对比。

---

## 五、架构总览

```
┌─────────────────────────────────────────────────────────┐
│  StrategyReg                                            │
│    period: "D"              ← 策略 on_bar               │
│    risk.monitor.period: "1m" ← 风控监控                 │
└─────────────────────────────────────────────────────────┘
                          │
          ┌───────────────┴───────────────┐
          ▼                               ▼
   StrategyDataFeed                 RiskDataFeed
   (日 K)                            (1m / tick / 日 K)
          │                               │
          ▼                               ▼
   on_bar → 金叉/死叉              RiskMonitorRunner
                                   → check_monitoring_risk
                                   → FULL_CLOSE @ 触发 bar 价格
```

### 5.1 注册层扩展

`StrategyReg` / `engine.register()` / `App.add_strategy()` 需支持：

| 字段 | 含义 |
|------|------|
| `period` | 策略 K 线周期（已有字段，需对外暴露：`register()` 增加 `period` 参数） |
| `risk["monitor"]` | 风控监控周期与定价方式；`None` / 不配时等于策略周期 |

### 5.2 新增核心组件（拟）

| 组件 | 职责 |
|------|------|
| `RiskMonitorSpec` | 解析/校验 `risk.monitor` 与 `rule_monitors`；若 `monitor=None` 则回退到策略周期 |
| `RiskMonitorRunner` | 从 `engine._run_post_bar_risk_monitoring` 抽离；同步行情、调用 `check_monitoring_risk`、下发强平 |
| `RiskBarProvider` | 回测按交易日懒加载监控周期 bar（`BacktestDataFeed.iter_intraday_bars()`） |
| `MonitorContext` | 根据当前 bar 的 `monitor.when` + `policy.allowed_when` 过滤应执行的 policy |

---

## 六、回测主循环（双周期）

### 6.0 统一执行顺序（回测）

所有回测路径遵循：

```text
1. 风控监控（RiskMonitorRunner）→ pipeline.drain()   # 强平卖出优先落实
2. 策略信号（strategy.on_bar 入队）→ pipeline.drain() # 队列内先卖后买
```

与现网 `_process_bar`（先策略后风控）**相反**，属 Phase 1 有意调整，需在 `engine.py` 与单周期路径一并改造。

`SignalPipeline.drain()` 实施时需保证：**同一批 pending 中 `SHORT`（卖）先于 `LONG`（买）撮合**；风控 `sell` 在步骤 1 已 drain，不会与步骤 2 买单抢序。

### 6.1 当 `strategy.period=D` 且 `monitor.period=1m`

```text
对每个交易日:
  # ── 阶段一：盘中风控（先于当日一切策略买卖）──
  懒加载当日 1m 数据
  对当日每根 1m bar（时间序）:
      _sync_risk_gateway(reg, bar_1m)
      RiskMonitorRunner.run(reg, bar_1m)    # 仅评估前日及以前形成的持仓
      pipeline.drain()                       # 先落实风控卖单

  # ── 阶段二：日 K 策略买卖（在当日分钟风控全部完成之后）──
  strategy.on_bar(bar_D)                   # 收盘金叉买 / 死叉卖
  pipeline.drain()                         # 队列内先卖后买；买入后 on_strategy_open(reset_peak)

  # ── 阶段三（可选）：日终类规则 ──
  若存在 monitor.when=day_end 的规则（如 daily_loss_pct）:
      在当日最后一根 1m 或日 K 上再跑一次 RiskMonitorRunner(monitor_when=day_end)
      pipeline.drain()
      # 注意：阶段二刚买入的新仓在此处会被 daily_loss_pct 评估，
      # 但 entry_price ≈ bar.close，P&L ≈ 0，不会误触发。

  日终结算
```

> **顺序要点（先风控、后买卖）**
>
> - 当日 **1m 风控只作用于昨日及之前已持有的仓位**；当日收盘 `on_bar` 新买入的仓位 **不参与当日分钟监控**，自 **下一交易日** 的 1m 循环起纳入（与 `on_strategy_open` → `reset_peak_equity` 一致）。
> - 这样避免「收盘刚买入、又用旧 peak 在当日下午分钟 K 上误平」；也符合 **先卖后买**：盘中该卖的先卖完，收盘再考虑是否开仓。
> - 若前日持仓在当日盘中触及 `max_drawdown`，在阶段一即 `risk_close_drawdown`，阶段二 DualEma 可能再金叉买入——买卖天然分离。

要点：

- **1m 循环不跑 DualEma**，策略语义不变。
- **`increment_period` 仅策略周期递增**：分钟监控 bar 上不调用 `calc_layer.increment_period()`，`holding_periods` 仍按策略周期（日 K）计数。分钟 bar 上仅更新行情、做盯市评估。
- 强平使用**触发那根 1m** 的价格，而非日 K 收盘价。
- `max_drawdown` 权益盯市按 1m 频率更新。
- **Phase 1**：监控与撮合统一用当前 bar 的 **`close`**（`monitor.price` 仅配置为 `close`）。
- **Phase 2**：`monitor.price=low/high` 同时用于盯市判断与强平撮合价（与第九章一致）。
- 所有成交均在**触发当根 bar** 上执行，不做跨 bar 延迟。
- **分钟数据按需加载**：仅加载当前交易日的分钟数据，避免全量预加载导致内存膨胀。
- **风控不需要预热数据**，分钟线从当日第一根 bar 开始即可正常评估。

### 6.2 当两者均为 `D`

单链路，但调整 `_process_bar` 内顺序为 **先风控、后策略**（与现网相反，属有意变更）。D 周期下每根日 K 即为最后一根 bar，`monitor_when` 固定为 `"day_end"`（见 4.2）：

```text
每根日 K:
  RiskMonitorRunner.run(reg, bar_D, monitor_when="day_end") → pipeline.drain()
  strategy.on_bar(bar_D)                                     → pipeline.drain()
```

逻辑与 6.0 一致；`daily_loss_pct` 等日终规则正常参与评估。

### 6.3 当 `strategy.period=1m`

策略与风控同频，每根 1m bar 仍遵循 6.0；**当日最后一根 1m bar 使用 `monitor_when="day_end"`** 以触发日终规则：

```text
对每根 1m:
  monitor_when = "day_end" if is_last_bar_of_day else "each_bar"
  RiskMonitorRunner.run(reg, bar_1m, monitor_when) → pipeline.drain()
  strategy.on_bar(bar_1m)                            → pipeline.drain()
```

### 6.4 数据层

- `DataEngine.get_hist_data(period="1m")` 已支持（QMT 等源），数据获取链路无需新增。
- **周期取值**：`1m` / `5m` 等由 `DataEngine._norm_period` 与数据源决定；QMT 对 ETF/股票通常支持 `1m`，部分源可能仅 `5m`，实施时按标的实测，不宜写死「A 股最短 5m」。
- **按日懒加载**：每个交易日开始时，通过 `DataEngine.get_hist_data(symbol, period=monitor.period, start_date=trade_date, end_date=trade_date)` 获取当日分钟数据。避免全量预加载。
- **`_normalize` 适配**：日线场景 `.normalize()` 去掉时分秒，保留到日期；分钟线场景保留到分钟，不做 `.normalize()`。
- **内存**：单日 1m 数据约 240 行/标的，即时释放，无累积内存压力。
- **交易日对齐**：分钟数据的时间范围天然落在日线数据的同一个交易日内，两者共用交易日历。
- **风控无需预热**：分钟线从当日第一根 bar 开始即可正常评估风控指标（回撤、止损等），不需要历史 warmup。

---

## 七、实盘

| monitor.period | 数据源 | 触发 |
|----------------|--------|------|
| `tick` | QMT Tick 推送 | 每笔或节流（如 3s） |
| `1m` | 1m bar 推送/轮询 | 每分钟 bar 完成 |
| `D` | 日 K 或定时 | 日 K 收盘 |

`RiskGateway` 已有 `_on_tick` / `_on_bar` 事件路径；实盘应统一走 `RiskMonitorRunner`，由调度器按 `monitor` 配置订阅，而非仅绑在 `_process_bar` 末尾。

**Phase 4 调度依赖**：当前 `Schedule` 类为占位实现（仅日志输出），Phase 4 实盘风控依赖一个真正的定时调度器或 QMT 的 bar 推送回调来驱动分钟/tick 级风控。实施前需确认 QMT 的 `1m`/`tick` bar 推送机制的可用性。

---

## 八、规则级兼容

### 8.1 策略级（Phase 1–2，覆盖多数场景）

整包风控统一 `monitor.period`：

- DualEma + 盘中 `max_drawdown` → `monitor.period=1m`
- 纯日终策略 → 省略 `monitor` 或 `period=D`

#### 8.1.1 规则过滤机制（`allowed_when`）

Phase 1 即引入最简过滤：给每条 policy 标注 `allowed_when`，在 `RuleEngine.evaluate()` 执行前过滤掉不匹配当前监控时机的策略。

**注意**：Phase 1 的过滤粒度是 **policy 级别**（整个类），不是单条规则级别。如果同一个 policy 内既有盘中规则又有日终规则，policy 的 `allowed_when` 设为 `None`（始终运行），由 policy 内部根据 `monitor_when` 参数决定哪些子规则生效。单条规则级别的覆盖由 Phase 3 的 `rule_monitors` 实现。

```python
class BaseRiskPolicy:
    # 该规则被允许的监控时机；None 表示不限（始终参与 evaluate）
    # 非 None 时，仅在 monitor_when 属于该集合时才调用 evaluate()
    allowed_when: Optional[set[str]] = None

    def evaluate(self, indicator, limits, monitor_when: str):
        """子类可重写，内部根据 monitor_when 决定启用哪些子规则"""
        raise NotImplementedError
```

**各 policy 的默认 `allowed_when` 及内部行为：**

| Policy 类 | `allowed_when` | 内部 gating |
|-----------|---------------|-------------|
| `AbnormalRiskPolicy` (L1) | `None`（始终运行） | 无内部过滤 |
| `DrawdownRiskPolicy` (L2) | `None`（始终运行） | 内部：`monitor_when != "day_end"` 时跳过 `daily_loss_pct`、`daily_max_loss`、`consecutive_*`；`max_drawdown` 在任何时机都评估 |
| `PriceRiskPolicy` (L3) | `None`（始终运行） | 无内部过滤 |
| `DailyDropRiskPolicy` (L3) | `{"day_end"}` | 整个 policy 仅日终运行 |
| `TimeRiskPolicy` (L2) | `None`（始终运行） | 内部：持仓超时检查在任何时机；交易时段检查在任何时机 |
| `PositionRiskPolicy` (L4) | N/A | 仅参与 `check_signal`，不参与监控 |

**过滤逻辑**（`MonitorContext`，在 `RuleEngine.evaluate` 开头）：

```python
def evaluate(self, indicator, monitor_when: str):
    for policy in self._policies:
        if policy.allowed_when is not None and monitor_when not in policy.allowed_when:
            continue  # 整个 policy 不参与此次评估
        # 正常评估——policy 内部可进一步根据 monitor_when 决定子规则
        events = policy.evaluate(indicator, self.limits, monitor_when=monitor_when)
        ...
```

**举例**：`monitor.when="each_bar"` 且 `monitor.period="1m"` 时：
- `DailyDropRiskPolicy`（`allowed_when={"day_end"}`）整类跳过；
- `DrawdownRiskPolicy` 仍参与 evaluate，但内部在 `monitor_when != "day_end"` 时跳过 `daily_loss_pct` / `daily_max_loss` / `consecutive_*`，仅保留 `max_drawdown` 等盘中规则；
- `PriceRiskPolicy` 的 `stop_loss` 等每分钟评估。

### 8.2 规则级（Phase 3，复杂场景）

`rule_monitors` 覆盖单条规则；`RuleEngine.evaluate` 前由 `MonitorContext` 过滤当前 bar 下应执行的 policy。此时 `rule_monitors` 中的单条覆盖优先级高于 policy 的 `allowed_when` 默认值。

### 8.3 规则默认监控频率建议

| 规则 | 建议默认 `allowed_when` | 监控周期 |
|------|------------------------|---------|
| `price_stop.stop_loss` | `None`（不限） | 跟随 `monitor.period` |
| `drawdown_stop.max_drawdown` | `None`（不限） | 跟随 `monitor.period` |
| `drawdown_stop.daily_loss_pct` | `{"day_end"}` | D |
| `drawdown_stop.daily_max_loss` | `{"day_end"}` | D |
| `drawdown_stop.consecutive_*` | `{"day_end"}` | D |
| `daily_drop_stop` | `{"day_end"}` | D |
| `time_risk.max_holding_periods` | `None`（不限） | 跟随 `monitor.period` |
| `position_limit.*` | N/A（信号时 `check_signal`） | N/A |

> **Phase 1 实现说明**：以上 `allowed_when` 在 Phase 1 按 policy 类粒度实现（见 8.1.1），Phase 3 通过 `rule_monitors` 实现单条规则级覆盖。

**L4 开仓预检**仍挂在 `SignalPipeline.evaluate`，与监控周期无关。

---

## 九、定价与触发语义

| `monitor.price` | 含义 | 典型用途 |
|-----------------|------|----------|
| `close` | 当前监控 bar 收盘价 | 1m 收盘止损 |
| `low` | 当前 bar 最低价（偏保守） | 止损类 |
| `high` | 当前 bar 最高价 | 止盈类（若需要） |
| `last` | Tick 最新价 | 实盘 tick 监控 |

说明：

- `max_drawdown` 使用**账户总权益**（现金 + 持仓×监控价），非单标的涨跌幅。
- 强平撮合价 = 触发时刻监控 bar 的 `price` 字段对应价格。

---

## 十、典型配置示例

### 10.1 DualEma + 盘中 max_drawdown

```python
app.add_strategy(
    DualEmaStrategy,
    symbols=["159509.SZ"],
    period="D",
    warmup_bars=20,
    risk=isolated_risk(
        max_drawdown=0.05,
        monitor={"period": "1m", "price": "close"},
    ),
)
```

### 10.2 纯日终（与现在一致）

```python
risk=isolated_risk(max_drawdown=0.05)
```

### 10.3 日内策略 + 1m 止损

```python
app.add_strategy(
    SomeIntradayStrategy,
    period="1m",
    risk=production_risk(
        stop_loss_pct=0.01,
        monitor={"period": "1m", "price": "low"},
    ),
)
```

### 10.4 混合：盘中止损 + 日终单日亏损（Phase 3）

```python
risk=production_risk(
    stop_loss_pct=0.01,
    daily_max_loss_pct=0.02,
    monitor={"period": "1m"},
    rule_monitors={
        "drawdown_stop.daily_loss_pct": {"period": "D", "when": "day_end"},
    },
)
```

---

## 十一、模块改动清单（实施时参考）

| 模块 | 改动 |
|------|------|
| `StrategyReg` / `register` / `App.add_strategy` | `register()` 增加 `period` 参数；解析 `risk.monitor` |
| `risk/risk_limits.py` | `DEFAULT_LIMITS` 增加 `"monitor": None`；增加 `monitor` / `rule_monitors` 校验方法 |
| `risk/presets.py` | `production_risk(..., monitor=...)` |
| `risk/rule_engine.py` | `evaluate()` 增加 `monitor_when` 参数；在循环内按 `policy.allowed_when` 过滤 |
| `risk/policies/*.py` | 各 policy 增加 `allowed_when` 类属性 |
| `engine/backtest_data_feed.py` | 增加 `iter_intraday_bars(symbol, trade_date, period)` 方法——按交易日懒加载分钟数据并迭代 |
| `engine/engine.py` | `_process_bar` 改为**先风控后策略**；日/1m 双循环（6.1）；新增 `_process_intraday_risk`；`_sync_risk_gateway` 增加 `symbols` 参数 |
| `engine/signal_pipeline.py` | `drain()` 同一批 pending **先处理卖单、后买单** |
| `risk/data_layer.py` | 支持按 `price` 字段（close/low/high）更新 `last_price` |
| `engine/live_data_feed.py` | 按 monitor 订阅 tick/1m/D（Phase 4） |
| 测试 | 日终-only / 日策略+1m 风控 / 全 1m 三套用例 |

**拟不改**：Policy 判定公式主体、`SignalPipeline` 主链、策略模板（除非策略自定义）。

---

## 十二、兼容与迁移

1. **旧配置**：无 `monitor` → 规则阈值语义不变；**回测执行顺序**改为先风控后买卖（§6.0），与现网不同。
2. **旧测试**：合成日 K 用例继续通过；新增 `tests/test_risk_monitor_1m.py` 覆盖双周期。
3. **性能**：
   - 仅当 `monitor.period` 细于 `strategy.period` 时加载更细行情；纯日终策略无额外开销。
   - 分钟级 `_sync_risk_gateway` 仅同步当前 bar 对应标的的行情/持仓，其余标的复用上次 DataLayer 快照，避免全量同步。
   - 分钟数据按交易日懒加载、当日内即时释放，不累积内存。
4. **单策略约束**：当前一次只运行一个策略，暂不考虑多策略不同 `period` 的数据协调问题。
5. **A 股 T+1 注意**：
   - **卖出**：当日买入的份额实盘受 T+1 约束，**当日不可卖**；当前回测 `PositionService.closeable_amount` 未建模 T+1，盘中 `max_drawdown` 强平回测可能与实盘不一致。
   - **买入**：强平后当日通常可再次买入（与「禁开」不同）；若需「强平后当日不再开仓」，须在策略层自行标记。

---

## 十三、实施阶段

| 阶段 | 内容 | 交付 |
|------|------|------|
| **Phase 1** | `monitor` 配置 + `RiskMonitorRunner` + 回测「日策略 + 1m 风控」 | DualEma + max_drawdown 盘中触发 |
| **Phase 2** | `price=low/close`、强平价对齐触发 bar | 止损语义精确 |
| **Phase 3** | `rule_monitors` + `day_end` 日终规则 | 混合监控 |
| **Phase 4** | 实盘 tick/1m 与回测语义对齐 | live 盘中风控 |

建议从 **Phase 1** 开工：对 `main.py` 改动小、收益最明显。

---

## 十四、配置传递链路（实施后）

与现有链路一致，仅在 `RiskLimits` 中增加 `monitor` 段：

```
isolated_risk(..., monitor={...})
  → App.add_strategy(risk=dict)
  → StrategyReg.risk
  → RiskLimits.update_limits(dict)           # DEFAULT_LIMITS["monitor"] = None → 被覆盖为用户值
  → RiskMonitorSpec(limits)                   # 解析/校验 monitor、rule_monitors，回退策略周期
  → RiskGateway + RiskMonitorRunner           # 按 monitor.period / monitor.when 调度
  → MonitorContext.filter(monitor_when)       # 按 policy.allowed_when 过滤
  → RuleEngine.evaluate(indicator, monitor_when)
  → DrawdownRiskPolicy / PriceRiskPolicy ...
```

---

## 十五、相关文档与代码

| 路径 | 说明 |
|------|------|
| `alphaQuantSystem/main.py` | 入口，`risk=isolated_risk(...)` |
| `alphaQuantSystem/risk/presets.py` | `isolated_risk` / `RISK_DISABLED` |
| `alphaQuantSystem/engine/engine.py` | `_process_bar`、`_run_post_bar_risk_monitoring` |
| `alphaQuantSystem/risk/policies/drawdown_risk.py` | `max_drawdown` 规则 |
| `quant_risk_control_module_development_skill.md` | Skill 4.2 回撤风控定义 |

---

## 十六、已确认项与待确认项

### 已确认（2026-06-10 方案审查后）

- [x] `align_to` 字段移除——恒等于 `strategy.period`，冗余。
- [x] `session_end` 移除——语义模糊，后续如需再引入。
- [x] `monitor.when="day_end"` 定义——分钟线时 = 当日最后一根分钟 bar（如 14:59）。
- [x] `RiskLimits.DEFAULT_LIMITS` 增加 `"monitor": None`，`None` = 跟随策略周期。
- [x] Phase 1 引入 `policy.allowed_when` 规则过滤机制，日终规则（如 `daily_loss_pct`）不在盘中评估。
- [x] 分钟级 `_sync_risk_gateway` 仅同步当前 bar 标的，其余标的复用快照。
- [x] 单策略运行约束——暂不考虑多策略不同周期的数据协调。
- [x] 分钟数据按交易日懒加载（`DataEngine.get_hist_data` 单日），不当日累积。
- [x] 风控无需预热分钟数据。
- [x] `_normalize` 分钟线时保留到分钟，不做 `.normalize()`。
- [x] Phase 1 成交在当根 bar 的 `close`；Phase 2 再支持 `low/high`。
- [x] A 股 T+1：回测未建模「当日买入不可卖」，实盘需单独评估。
- [x] 回测执行顺序：**先风控后买卖**；同一 `drain` **先卖后买**（§6.0）。
- [x] 日 K + 1m：当日 1m 风控在日 K 策略之前；当日新仓次日才进分钟监控。

### 仍待确认

- [ ] `1m` 回测数据默认源（QMT / akshare）与缺失日处理
- [ ] 触发 bar 的撮合规则（限价按 close / 市价模拟——当前按 close）
- [ ] 多标的策略下 1m 对齐方式（按 symbol 交错 vs 按时刻聚合）
- [x] `max_drawdown` 与「开仓后重置 peak」：**已在现网实现**（`on_strategy_open` / `on_max_drawdown_close` → `calc_layer.reset_peak_equity`）；1m 双周期实施后需补回归用例
- [x] `holding_periods` 在分钟监控下不膨胀——**仅在策略周期 bar 上 `increment_period`**（6.1 已明确）
- [ ] Phase 4：QMT 的 `1m`/`tick` bar 推送机制就绪度
- [ ] Phase 4：实盘定时调度器实现方案（替代当前 `Schedule` 占位）
- [ ] `price="low"` / `price="high"` 在回测中的撮合模拟精度验证

---

## 十七、方案审查记录（2026-06-10）

对照 `alphaQuantSystem` 现网代码核查，修改后文档**总体可实施**，下列为审查结论。

### 与代码一致的部分

| 文档描述 | 代码现状 |
|----------|----------|
| 单链路 `_process_bar` + `_run_post_bar_risk_monitoring` | `engine/engine.py` 属实 |
| `register()` / `add_strategy()` 未暴露 `period` | 属实，`StrategyReg.period` 默认 `"D"` |
| `DataEngine.get_hist_data` 支持 `1m` | `data_engine.py` `_PERIOD_ALIAS` / QMT 映射属实 |
| `BacktestDataFeed._normalize` 对索引 `.normalize()` 去时分秒 | 属实；分钟线需新路径（文档 6.4 正确） |
| `Schedule.start()` 仅打日志、不触发定时 | `engine/schedule.py` 属实，Phase 4 依赖描述正确 |
| `RiskGateway._on_tick` / `_on_bar` 存在，回测未走事件驱动监控 | 属实 |
| `max_drawdown` 仅 `FULL_CLOSE`、不 `LIMIT_OPEN` | `drawdown_risk.py` 与近期实现一致 |
| 开仓/清仓重置 peak | `risk_gateway.on_strategy_open` / `on_max_drawdown_close` 已实现 |

### 文档已修正的内部矛盾

1. **成交价**：6.1 原同时写「close 或 low」与「一律 close」→ 已改为 Phase 1 / Phase 2 分阶段说明。
2. **8.1 举例**：原把 `daily_loss_pct` 说成独立 `allowed_when` 过滤 → 已改为与 8.1.1「Drawdown 内部 gating」一致。
3. **T+1**：原「强平后当日禁开」方向不准 → 已改为「当日买入不可卖 + 回测未建模」。

### 实施前建议补充（尚未写入正文逻辑）

1. **`production_risk()`**：文档示例已用，但 `risk/presets.py` **尚未实现**，Phase 1 需与 `isolated_risk` 一并添加或示例改回 `isolated_risk(..., monitor=...)`。
2. **`increment_period`**：已明确仅在策略周期 bar 上调用（§6.1）；实施时注意 `_process_intraday_risk` 与 `_process_bar` 中的 `increment_period` 调用点区分。
3. **`daily_drawdown` / `realized_pnl_daily` 在 1m 上**：CalcLayer 日级字段在分钟 `each_bar` 评估中仍会计算（`compute()` 无副作用），但仅 `DrawdownRiskPolicy` 在 `monitor_when="day_end"` 时才评估 `daily_loss_pct` 等日级规则——分钟 `each_bar` 调用时这些子规则被内部 gating 跳过，不影响结果。
4. **当日新仓与分钟监控**：已按 **先风控、后买卖** 定格——当日收盘买入不参与当日 1m 监控，自次日起监控；与 `reset_peak` 在 `on_strategy_open` 生效一致（见 §6.1）。

### 审查结论

- 修改后的文档比初版更可落地；**日内顺序以先风控后买卖、先卖后买为准**（§6.0 / §6.1）。
- **可进入 Phase 1 实施**；与现网 `_process_bar` 顺序不同，实施时须整体切换并回归 DualEma + `max_drawdown` 用例。
