# 五福策略 v2 设计文档

## 背景

将 `wufu_strategy_local.py`（依赖聚宽兼容层 `wufu_jq_compat.py`）重写为原生 alphaQuantSystem 框架策略，消除对兼容层的依赖，同时保持与 `wufu_strategy.py` 完全一致的策略逻辑。

## 目标

1. 策略逻辑 100% 复制 `wufu_strategy.py`，禁止自我发挥
2. 历史行情使用 `DataEngine.get_hist_data` 接口
3. 溢价率使用 `DataEngine.get_etf_premium_rate` 接口（逐个调用，保留开关）
4. 不依赖 `wufu_jq_compat.py`
5. 同一套代码支持回测和实盘

## 新文件

`alphaQuantSystem/examples/strategies/wufu_strategy/wufu_v2.py`

## 内联数据结构

替代兼容层的同名类，内联在策略文件中：

```python
@dataclass
class _PositionView:
    total_amount: float
    closeable_amount: float
    avg_cost: float
    price: float

@dataclass
class _PortfolioView:
    positions: Dict[str, _PositionView]
    available_cash: float
    total_value: float

@dataclass
class _Context:
    current_dt: datetime
    previous_date: Optional[date]
    portfolio: _PortfolioView

@dataclass
class _MarketData:
    last_price: float
    high_limit: float
    low_limit: float
    paused: bool
```

## 数据接口替换

| 原兼容层 | 新接口 |
|---|---|
| `_jq_get_price(syms, count, end_date, daily)` | `_get_hist_batch(syms, count, end_date)` → 逐 symbol 调 `data_engine.get_hist_data`，拼成 `DataFrame(time,code,close,volume,money)` |
| `_jq_get_price(sym, start, end, '1m')` | `data_engine.get_hist_data(sym, period='1m', start_date, end_date)` |
| `_jq_get_all_securities(['etf'])` | `data_engine.get_etf_spot()` |
| `_jq_get_current_data()` | `_build_market_data(symbols)` → 逐 sym 调 `get_realtime`，组装 `Dict[str, _MarketData]` |
| `_jq_get_trade_days(start, end, count)` | `_get_trade_days(start, end, count)` → 从缓存 `_trade_calendar` 过滤 |
| `_jq_get_extras('unit_net_value', etf, d, d)` | `data_engine.get_etf_premium_rate(sym, trade_date)` |
| `_jq_attribute_history(sec, 1, '1d', ['close'])` | `data_engine.get_hist_data(sym, period='D', end_date=..., count=2)` |
| `_jq_place_order(sym, diff)` | `self.buy` / `self.sell` |
| `build_context()` | `_build_context()` |

## 交易日历

`_ensure_trade_calendar()` — 用基准 ETF `510300.SH` 拉全量日K，提取 date 列，存入 `_trade_calendar: List[date]`，一次性缓存。

## 批量历史数据

`_get_hist_batch(symbols, count, end_date, fields=['close','volume'])` — 逐 symbol 调 `get_hist_data`，按 count 截尾，concat 成统一 DataFrame 格式供动量计算使用。

## 回测 vs 实盘

**回测**（`backtest_mode=True`）：`on_bar` 接收基准日K bar，顺序执行 `morning_routine → afternoon_routine → reset_daily_flags`，止损在日K bar 内模拟执行。

**实盘**：`WufuV2Live(TimerHistPollMixin, WufuV2Strategy)` — Timer 轮询触发，`on_bar` 按当前时间路由到对应流水线。

## 符号格式

全部使用 alphaQuant 格式（`510300.SH` / `159915.SZ`），固定池的 `.XSHG/.XSHE` 在 `on_init` 一次性转换，内部不再做动态转换。转换规则：`.XSHG` → `.SH`，`.XSHE` → `.SZ`。

## 溢价率处理

`enable_premium_filter=False` 时完全跳过（默认）。开启时在 `calculate_premium_rate(etf, context)` 中调用 `data_engine.get_etf_premium_rate(etf, trade_date)`，返回 dict 取 `close` 和 `日终净值` 字段计算溢价率。

## 配置文件

新建 `wufu_v2_runtime.yaml`，结构复用 `wufu_strategy_local_runtime.yaml`。

## 不变内容

以下函数的算法逻辑与 `wufu_strategy.py` 完全一致，仅做类方法化：
- `calculate_momentum_score`
- `gaussian_filter_last_two`
- `laplace_filter`
- `calculate_rsi`
- `apply_filters`
- `get_final_ranked_etfs`
- 所有流水线方法（`morning_routine`, `afternoon_routine`, etc.）
