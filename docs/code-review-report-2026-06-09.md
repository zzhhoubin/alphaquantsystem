# alphaQuantSystem 项目代码审查报告

> 审查日期：2026-06-09
> 审查范围：全项目代码，按需求文档 11 个主题逐一分析

---

## 1. 仓位管理模块 + 实盘/回测路由一致性

**结论：基本一致，但有一条路径差异。**

- 仓位管理：[services/position/service.py](../../services/position/service.py) — `PositionService`，实盘和回测共用同一个 `apply_trade()` 方法。
- [engine/engine.py:152](../../engine/engine.py#L152) — 回测模式中 `AccountService` 被重新创建，但 `PositionService` 实例（line 43）被回测和实盘共用。回测模式下这个共享实例可能携带上一次回测的残留状态，建议回测前调用 `PositionService` 的清仓方法（目前不存在）。

---

## 2. 绩效评测模块

**结论：架构统一，有一个取值 bug。**

- 统一绩效计算：[analyze/metrics.py](../../analyze/metrics.py) — `compute_performance_metrics()`，通过 `PerformanceContext` 同时服务回测和实盘，设计合理。
- **Bug**: [analyze/metrics.py:53](../../analyze/metrics.py#L53) — `getattr(result.account_snapshots[-1], 'commission', 0)`。`AccountSnapshot`（[core/object.py:165-176](../../core/object.py#L165-L176)）没有 `commission` 属性，这个值永远为 0。此处应取 `AccountService.total_commission`。

---

## 3. core / engine / gateway 等模块的 bug

### 3a. 回测日终结算严重 bug

[engine/engine.py:200-208](../../engine/engine.py#L200-L208)

```python
for bar in self._backtest_feed.iter_bars():
    for reg in self._strategy_regs:
        if bar.symbol in reg.symbols:
            self._process_bar(reg, bar)
    if self._is_day_end(bar):
        self._position_svc.update_price(bar.symbol, bar.close)
        mv = self._position_svc.total_market_value()
        reporter.set_daily_state(self._account_svc.cash, mv)
        reporter.on_day_end(bar.event_time.date())
```

两个问题：

1. **日终结算被多次调用**：日线数据中每个 bar 都是 "day end"，多标的回测时同一天会有多个 bar，`reporter.on_day_end()` 被调用 N 次（N = 标的总数），导致权益曲线中出现重复日期条目。
2. **只更新最后一个标的的价格**：`update_price()` 只更新当前 bar 对应标的的价格，其他标的的持仓市值使用旧价格参与 `total_market_value()` 计算。

**修复建议**：bar 循环中先累积更新所有 bar，等所有标的当天的 bar 都处理完后统一做一次日终结算。

### 3b. 实盘模式事件引擎双重消费（竞争条件）

[engine/engine.py:225-226](../../engine/engine.py#L225-L226) + [engine/engine.py:260-268](../../engine/engine.py#L260-L268)

`_run_live()` 先调用 `self._event_engine.start()` 启动了后台消费线程，然后主循环中又主动 `poll()` + `dispatch()`。两个线程同时消费同一个 Queue，事件被随机分到不同线程处理，虽然不会重复消费（Queue.get 是原子的），但：

- 后台线程在无意义地与主循环竞争
- 定时器推送的 TIMER 事件可能被后台线程消费，主循环永远收不到
- 行为不确定

**修复建议**：要么设为 `sync_mode=True` 只走主循环 poll，要么完全不启动后台线程。

### 3c. SignalPipeline 中 RiskContext 为空

[engine/signal_pipeline.py:43-45](../../engine/signal_pipeline.py#L43-L45)

```python
ctx = RC()  # RiskContext() — 所有字段为 0
result = self._risk.evaluate(signal, ctx)
```

`RiskContext` 的 `available_cash`、`total_value`、`position_volume` 等全部为 0，导致 `max_drawdown` 规则计算的回撤分母为 0（永远不会触发），`max_positions` 规则不知道真实持仓数。风控规则**形同虚设**。

需要在 drain 之前从 `PositionService` / `AccountService` 填充真实的 RiskContext。

### 3d. EventEngine.stop() 未停止定时器线程

[core/event_engine.py:129-132](../../core/event_engine.py#L129-L132)

`stop()` 只 join 了主分发线程 `_thread`，没有停止 `_timer_thread`。`_timer_thread` 持续运行并在 `time.sleep(self._interval)` 后向 Queue put 事件。虽然应用退出时 daemon 线程会被强制终止，但它可能在 stop 后继续 put 1-2 个 TIMER 事件，造成资源泄漏。

---

## 4. 风控模块调用机制

**结论：存在两套独立风控系统，引擎只用了简单的那套。**

| 风控系统 | 文件 | 是否被引擎使用 |
|---|---|---|
| 简单风控 `RiskService` | [services/risk/service.py](../../services/risk/service.py) | 是（通过 SignalPipeline） |
| 六层风控 `RiskGateway` | [risk/risk_gateway.py](../../risk/risk_gateway.py) | **否** |

`RiskGateway` 拥有完整的六层架构（DataLayer → CalcLayer → RuleEngine → ExecLayer → SceneAdapter → LogLayer），以及 L1-L4 分级风控策略（异常行情、回撤、价格、日内跌幅、仓位），但 `StrategyEngine` 完全没有使用它。

**调度周期**：当前简单风控在 `SignalPipeline.drain()` 中同步触发（每笔信号入队后）。若接入 `RiskGateway`，它通过事件订阅可做到 BAR/TICK/POSITION 级别的逐帧持仓监控。

**建议**：决定保留哪套系统，删除另一套。如果保留 `RiskGateway`，需要将其集成到 `StrategyEngine` 中。

---

## 5. 多时间粒度数据处理

**结论：目前只支持单一周期，多周期混合策略需要自行处理。**

- [core/context.py:65](../../core/context.py#L65) — `hist()` 方法的 `start = current_date - timedelta(days=count * 3)` 是粗略估算，假设日线数据，对分钟级数据不适用。
- `BacktestDataFeed` 只订阅单一 period（[engine/backtest_data_feed.py:38](../../engine/backtest_data_feed.py#L38)）。
- 策略如 `DoubleMA` 只能在 `on_bar` 中使用 `hist()` 手动拉取日线数据，无法订阅实时分钟 bar 与日线 bar 的混合事件流。

**建议**：`BacktestDataFeed` 支持多周期订阅，引擎按时间顺序交叉 yield 不同周期的 bar，策略通过 bar.interval 区分处理。

---

## 6. 模块冗余

以下模块存在功能重叠或代码冗余：

1. **风控双系统**（见第 4 点）— 最大的冗余。
2. [backtest/matching_engine.py](../../backtest/matching_engine.py) — 同时保留了事件驱动模式（`on_signal`/`on_bar`/`match_pending_orders`）和纯函数模式（`match()`）。引擎只用 `match()`，事件驱动部分（line 65-153）是死代码。
3. [core/context.py:61-74](../../core/context.py#L61-L74) — `StrategyContext.hist()` 自己做了日期估算和数据拉取逻辑，与 `BacktestDataFeed.subscribe()` 功能重叠。
4. `matching_engine.py` 和 `trade_report.py` 各自维护了一套 FIFO 买入队列逻辑，买入成本配对计算有重复。

---

## 7. 软件质量评估

### 优点
- 事件驱动架构清晰，借鉴 vn.py 设计成熟
- 类型注解覆盖率较高
- dataclass 数据模型统一
- Loguru 日志完善
- 数据引擎多数据源降级链路设计合理

### 不足
- 缺少集成测试，只有 indicator 有单元测试
- 函数内 late import 较多（如 [signal_pipeline.py:43](../../engine/signal_pipeline.py#L43)、[engine.py:148-149](../../engine/engine.py#L148-L149)），影响启动性能和代码可读性
- 大量硬编码路径：
  - [gateway/qmt_gateway.py:10-13](../../gateway/qmt_gateway.py#L10-L13) — QMT 路径/账号
  - [data/data_engine.py:1357](../../data/data_engine.py#L1357) — 集思录 Cookie 泄露风险
- `requirements.txt` 和 `main.py` 在 CLAUDE.md 目录树中标明但实际缺失

---

## 8. 复权/除权/停牌/涨跌停

**结论：基本未处理。**

- QMT 数据获取使用 `dividend_type='front'`（前复权），部分解决了复权问题，但其他数据源（东财、akshare）未统一复权方式。
- [core/object.py:40-41](../../core/object.py#L40-L41) — `TickData` 有 `limit_up`/`limit_down` 字段，但成交/下单逻辑中没有涨跌停校验。
- 没有停牌检查机制。如果某标的在回测期间停牌，`BacktestDataFeed` 在该日无数据会直接跳过，策略无法感知。
- 没有除权除息日的特殊处理（如送转股导致的持仓数量变化）。

---

## 9. 回测逻辑核验

除了 3a 中的日终结算 bug 外：

- **限价单成交逻辑**：[backtest/matching_engine.py:320-334](../../backtest/matching_engine.py#L320-L334) — `_can_fill_limit_order` 逻辑正确：买入限价 >= 最低价即可成交，卖出限价 <= 最高价即可成交。
- **买入资金裁剪**：[backtest/matching_engine.py:259-301](../../backtest/matching_engine.py#L259-L301) — `_clip_buy_volume` 含佣金预估，整手下取，逻辑正确。
- **FIFO 盈亏计算**：[backtest/trade_report.py](../../backtest/trade_report.py) — buy/sell FIFO 配对正确，无配对时不记净盈亏的设计合理。
- **问题**：回测不模拟 T+1 约束（A 股当日买入不可卖出），`PositionService.apply_trade` 买入后立即增加了可卖数量。
- `print_summary()` 中 [backtest/result.py:265](../../backtest/result.py#L265) — `metrics["total_pnl"]` 可能不存在（`compute_performance_metrics` 返回的字典中没有 `total_pnl` 键），会抛出 KeyError。

---

## 10. 交易指令逻辑

**结论：仅支持基本的限价/市价单，缺少关键约束。**

- [core/object.py:17-18](../../core/object.py#L17-L18) — `OrderType` 只有 LIMIT 和 MARKET。
- **缺失**：止盈止损单、条件单、OCO 订单。
- **缺失 T+1 约束**：A 股当日买入的股票次日才能卖出，回测和实盘均未实现此限制。
- **缺失涨跌停校验**：超出涨跌停价格的限价单应被拒绝或按涨跌停价成交。
- `PositionService` 没有 `frozen` 字段被冻结的逻辑（卖出挂单后应冻结对应持仓）。

---

## 11. 因子与指标运算

- [indicator/base.py](../../indicator/base.py) — `BaseIndicator` 增量 push 设计清晰，适用于实盘 on_bar 和回测。
- 已实现指标：MA、momentum、primitive、functional、EXPMA-KDJ、SixPulse。
- 存在测试：[tests/indicator/](../../tests/indicator/)
- **不足**：指标与策略的集成方式不明确。`DoubleMA` 策略直接手算均线（[strategies/double_ma.py:15-18](../../strategies/double_ma.py#L15-L18)），没有使用 `indicator/` 模块中的 MA 类。策略层和指标层存在脱节。

---

## 总结：严重性排序

| 优先级 | 问题 | 影响 |
|---|---|---|
| **P0** | 回测日终结算重复 + 价格更新不完整 | 多标的回测结果完全错误 |
| **P0** | SignalPipeline RiskContext 为空 | 风控规则不生效 |
| **P1** | 实盘事件引擎双重消费 | 实盘行为不确定 |
| **P1** | 两套风控系统并存 | 六层风控白写了，维护负担 |
| **P2** | 缺少 T+1 / 涨跌停 | 回测结果与实盘不符 |
| **P2** | EventEngine 定时器线程未停止 | 资源泄漏 |
| **P2** | 集思录 Cookie 硬编码泄露 | 安全风险 |
| **P3** | 指标模块与策略脱节 | 代码复用差 |
| **P3** | BacktestResult.print_summary KeyError | 打印摘要时报错 |
