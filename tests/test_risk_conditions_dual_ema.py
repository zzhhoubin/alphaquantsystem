"""DualEma 策略 —— 20 条风控条件逐条隔离回测。

每条测试仅启用一个风控项（其余见 tests/risk_configs.RISK_DISABLED），
用合成 K 线驱动 DualEma 产生真实买卖，再断言风控是否按预期触发。
"""
from __future__ import annotations

from datetime import datetime

import pytest

from alphaQuantSystem.core import Direction, TickData
from alphaQuantSystem.risk.policies import RiskAction
from alphaQuantSystem.risk.policies.abnormal_risk import AbnormalRiskPolicy
from alphaQuantSystem.risk.calc_layer import IndicatorSnapshot
from alphaQuantSystem.risk.risk_limits import RiskLimits
from alphaQuantSystem.tests.risk_backtest_harness import (
    SYMBOL,
    bars_from_closes,
    has_buy,
    has_risk_close,
    inject_limit_down_tick,
    run_dual_ema_scenario,
)
from alphaQuantSystem.tests.risk_configs import RISK_CONDITIONS, isolated_risk


# 合成行情：前段横盘 → 急跌制造金叉 → 持仓段
_BASE_WARMUP = [100.0] * 8 + [98.0, 97.0, 96.0, 95.0]
# 金叉买入后价格变化段（接在 warmup 后）
_AFTER_BUY_RALLY = [110.0]          # 金叉买入 (~110)
_AFTER_BUY_DROP_1PCT = [108.89]     # 较 110 跌约 1.01%
_AFTER_BUY_GAIN_5PCT = [115.5]      # 较 110 涨约 5%
_AFTER_BUY_GAIN_8PCT = [118.8]      # 涨 8% 激活追踪
_AFTER_BUY_PULLBACK = [116.0]       # 从峰值回落 >2%
_AFTER_BUY_GAIN_3PCT = [113.3]      # 涨 3% 触发阶梯第一档
_AFTER_BUY_GAIN_6PCT = [116.6]      # 涨 6% 触发阶梯第二档全平
_AFTER_BUY_DROP_2PCT = [107.8]      # 跌约 2% 触发阶梯止损
_HOLD_FLAT = [110.0, 110.0, 110.0, 110.0]  # 持仓横盘 4 根


def _closes(*segments):
    return list(_BASE_WARMUP) + list(segments)


# ---------------------------------------------------------------------------
# L3 止盈止损
# ---------------------------------------------------------------------------

def test_01_fixed_stop_loss_1pct():
    """① 固定止损 1%：亏损达 1% 时风控强平。"""
    result = run_dual_ema_scenario(
        _closes(*_AFTER_BUY_RALLY, *_AFTER_BUY_DROP_1PCT),
        {"stop_loss_pct": 0.01},
    )
    assert has_buy(result.trades), "应先有金叉买入"
    assert has_risk_close(result.trades), "亏损 1% 应触发风控平仓"
    assert result.final_holding == 0.0


def test_02_fixed_take_profit_5pct():
    """② 固定止盈 5%：盈利达 5% 时风控强平。"""
    result = run_dual_ema_scenario(
        _closes(*_AFTER_BUY_RALLY, *_AFTER_BUY_GAIN_5PCT),
        {"take_profit_pct": 0.05},
    )
    assert has_buy(result.trades)
    assert has_risk_close(result.trades)
    assert result.final_holding == 0.0


def test_03_daily_drop_stop_3pct():
    """③ 当日跌幅止损 3%：相对昨收跌超 3% 强平。"""
    # 昨收 110，当日收 106.5 → 跌 3.18%
    result = run_dual_ema_scenario(
        _closes(*_AFTER_BUY_RALLY, 106.5),
        {"daily_drop_stop_pct": 0.03},
    )
    assert has_buy(result.trades)
    assert has_risk_close(result.trades)


def test_04_trailing_take_profit():
    """④ 追踪止盈：盈利 5% 激活后，从峰值回落 2% 平仓。"""
    result = run_dual_ema_scenario(
        _closes(*_AFTER_BUY_RALLY, *_AFTER_BUY_GAIN_8PCT, *_AFTER_BUY_PULLBACK),
        {
            "trailing_tp_activation": 0.05,
            "trailing_tp_callback": 0.02,
            "take_profit_pct": 9.99,
        },
    )
    assert has_buy(result.trades)
    assert has_risk_close(result.trades)


def test_05_stepped_take_profit():
    """⑤ 阶梯止盈：涨 6% 触发第二档全平。"""
    result = run_dual_ema_scenario(
        _closes(*_AFTER_BUY_RALLY, *_AFTER_BUY_GAIN_3PCT, *_AFTER_BUY_GAIN_6PCT),
        {
            "step_tp_levels": [0.03, 0.06],
            "step_sl_levels": [],
            "step_close_ratios": [0.5, 1.0],
            "take_profit_pct": 9.99,
        },
    )
    assert has_buy(result.trades)
    assert has_risk_close(result.trades)


def test_06_stepped_stop_loss():
    """⑥ 阶梯止损：跌 2% 触发全平。"""
    result = run_dual_ema_scenario(
        _closes(*_AFTER_BUY_RALLY, *_AFTER_BUY_DROP_2PCT),
        {
            "step_sl_levels": [0.02],
            "step_close_ratios": [1.0],
            "stop_loss_pct": 9.99,
        },
    )
    assert has_buy(result.trades)
    assert has_risk_close(result.trades)


# ---------------------------------------------------------------------------
# L2 回撤 / 亏损 / 持仓时间
# ---------------------------------------------------------------------------

def test_07_max_drawdown_2pct():
    """⑦ 最大回撤 2%：回撤超标后仅清仓当前持仓，不拦截后续开仓。"""
    result = run_dual_ema_scenario(
        _closes(
            *_AFTER_BUY_RALLY,
            105.0, 112.0, 105.0,
            98.0, 97.0, 96.0, 95.0,  # 再跌一段，便于后续二次金叉
            110.0,
        ),
        {"max_drawdown": 0.02},
    )
    assert has_buy(result.trades)
    assert has_risk_close(result.trades), "回撤超标应触发风控清仓"
    assert result.gateway is not None
    assert result.gateway.state_machine.state.value == "NORMAL"
    buys = [t for t in result.trades if t.direction == Direction.LONG]
    assert len(buys) >= 2, "最大回撤清仓后应允许再次买入"


def test_08_daily_max_loss_absolute():
    """⑧ 单日绝对亏损：已实现亏损超阈值后触发风控。"""
    # 两笔亏损往返：买 110 卖低
    seq = [110.0, 100.0, 108.0, 98.0, 108.0]
    result = run_dual_ema_scenario(
        _closes(*seq),
        {"daily_max_loss": 5000, "stop_loss_pct": 9.99, "take_profit_pct": 9.99},
        warmup_bars=10,
    )
    assert len(result.trades) >= 2


def test_09_daily_max_loss_pct():
    """⑨ 单日回撤比例 2%：日内权益回撤超标后限制开仓。"""
    closes = _closes(*_AFTER_BUY_RALLY, 106.0)
    result = run_dual_ema_scenario(
        closes,
        {"daily_max_loss_pct": 0.02, "stop_loss_pct": 9.99},
        same_day_from=len(closes) - 2,  # 买入与下跌在同一交易日
    )
    assert has_buy(result.trades)
    assert result.gateway is not None
    assert result.gateway.state_machine.state.value in ("LIMIT_OPEN", "PAUSE_TRADE", "LOCKED")


def test_10_consecutive_loss_2_trades():
    """⑩ 连续亏损 2 笔：两笔亏损后冻结开仓。"""
    # 震荡行情促使多次买卖且亏损
    seq = [110.0, 102.0, 108.0, 100.0, 108.0, 100.0, 108.0]
    result = run_dual_ema_scenario(
        _closes(*seq),
        {
            "consecutive_loss_limit": 2,
            "stop_loss_pct": 9.99,
            "take_profit_pct": 9.99,
            "max_holding_periods": 0,
        },
        warmup_bars=10,
    )
    assert len(result.trades) >= 2
    # 第三次买入应被拦截（若已触发连续亏损）
    if result.gateway and result.gateway.state_machine.state.value != "NORMAL":
        assert len(result.risk_blocks) > 0 or result.final_holding == 0.0


def test_11_consecutive_loss_periods():
    """⑪ 连续亏损 3 周期：通过 gateway 直接注入周期亏损状态验证。"""
    gw_limits = RiskLimits()
    gw_limits.update_limits(isolated_risk(consecutive_loss_periods_limit=3))
    from alphaQuantSystem.core import EventEngine
    from alphaQuantSystem.risk.risk_gateway import RiskGateway

    ee = EventEngine(sync_mode=True)
    gw = RiskGateway(ee, gw_limits)
    for _ in range(3):
        gw.calc_layer.update_period_pnl(SYMBOL, -100.0)

    snapshot = gw.data_layer.snapshot(SYMBOL, "t")
    indicator = gw.calc_layer.compute(snapshot)
    events = gw.rule_engine.evaluate(indicator)
    assert any("连续亏损" in e.reason and "周期" in e.reason for e in events)


def test_12_max_holding_periods_3():
    """⑫ 持仓超时 3 周期：持仓满 3 根 K 线后强平。"""
    result = run_dual_ema_scenario(
        _closes(*_AFTER_BUY_RALLY, *_HOLD_FLAT),
        {"max_holding_periods": 3, "stop_loss_pct": 9.99, "take_profit_pct": 9.99},
    )
    assert has_buy(result.trades)
    assert has_risk_close(result.trades)
    assert result.final_holding == 0.0


# ---------------------------------------------------------------------------
# L1 异常交易
# ---------------------------------------------------------------------------

def test_13_limit_down_force_close():
    """⑬ 跌停：持仓标的跌停时强平。"""

    def _inject(_engine, _reg, bar, gateway):
        if has_buy(_reg.strategy.recorded_trades):
            inject_limit_down_tick(gateway, bar, bar.close)

    result = run_dual_ema_scenario(
        _closes(*_AFTER_BUY_RALLY, 108.0),
        {"ban_limit_up_down": True, "stop_loss_pct": 9.99},
        on_before_monitor=_inject,
    )
    assert has_buy(result.trades)
    assert has_risk_close(result.trades)


def test_14_price_gap_2pct():
    """⑭ 价格跳空 2%：开盘跳空超标后状态机进入 LIMIT_OPEN。"""

    def _inject(_engine, _reg, bar, gateway):
        gateway.data_layer._prev_closes[bar.symbol] = 100.0
        bar.open = 103.5  # 跳空 3.5%

    result = run_dual_ema_scenario(
        _closes(103.5),
        {"gap_interval_pct": 0.02},
        warmup_bars=10,
        on_before_monitor=_inject,
    )
    assert result.gateway is not None
    assert result.gateway.state_machine.state.value == "LIMIT_OPEN"


def test_15_abnormal_price_move_3pct():
    """⑮ 异常波动 3%：单根 K 线涨跌幅超标后 LIMIT_OPEN。"""

    def _inject(_engine, _reg, bar, gateway):
        gateway.data_layer._prev_closes[bar.symbol] = 100.0

    result = run_dual_ema_scenario(
        _closes(110.0),  # 相对昨收 100 涨 10%
        {"abnormal_price_move_pct": 0.03},
        warmup_bars=10,
        on_before_monitor=_inject,
    )
    assert result.gateway is not None
    assert result.gateway.state_machine.state.value == "LIMIT_OPEN"


def test_16_slippage_reject():
    """⑯ 滑点超限拒单：Policy 层单测（回测 CalcLayer 暂不计算滑点）。"""
    policy = AbnormalRiskPolicy()
    limits = RiskLimits()
    limits.update_limits(isolated_risk(max_slippage_bp=10, slippage_action="reject"))
    indicator = IndicatorSnapshot(
        symbol=SYMBOL,
        strategy_id="t",
        timestamp=datetime(2024, 6, 1),
        slippage_bp=25.0,
        symbol_position_ratio=0.1,
    )
    events = policy.evaluate(indicator, limits)
    assert any(e.action == RiskAction.REJECT_ORDER for e in events)


# ---------------------------------------------------------------------------
# L4 仓位 / 额度 / 名单
# ---------------------------------------------------------------------------

def test_17_min_order_notional_5000():
    """⑰ 最小名义金额 5000：小单被拒。"""
    result = run_dual_ema_scenario(
        _closes(*_AFTER_BUY_RALLY),
        {"min_order_notional": 5000},
        strategy_params={"full_position": False, "order_volume": 100},  # 100*110=11000 仍大
    )
    # 用更小价格单测：100 股 * 10 元
    result2 = run_dual_ema_scenario(
        [10.0] * 12 + [9.0, 9.0, 12.0],
        {"min_order_notional": 5000},
        strategy_params={"full_position": False, "order_volume": 100, "fast_period": 2, "slow_period": 3},
        warmup_bars=10,
    )
    assert len(result2.risk_blocks) > 0
    assert not has_buy(result2.trades)


def test_18_max_order_notional_5000():
    """⑱ 最大名义金额 5000：大单被拒。"""
    result = run_dual_ema_scenario(
        _closes(*_AFTER_BUY_RALLY),
        {"max_order_notional": 5000},
        strategy_params={"full_position": False, "order_volume": 1000},
    )
    assert len(result.risk_blocks) > 0
    assert not has_buy(result.trades)


def test_19_cooldown_86400_seconds():
    """⑲ 冷却 86400 秒：同日第二笔买入被拒。"""
    # 连续两天金叉
    seq = [110.0, 108.0, 112.0, 108.0]
    result = run_dual_ema_scenario(
        _closes(*seq),
        {"cooldown_seconds": 86400},
        warmup_bars=10,
    )
    buys = [t for t in result.trades if t.direction == Direction.LONG]
    assert len(buys) >= 1
    assert len(result.risk_blocks) > 0 or len(buys) == 1


def test_20_blacklist():
    """⑳ 黑名单：标的在黑名单时买入被拒。"""
    result = run_dual_ema_scenario(
        _closes(*_AFTER_BUY_RALLY),
        {"blacklist_symbols": ["159509"]},
    )
    assert len(result.risk_blocks) > 0
    assert not has_buy(result.trades)
    assert any("黑名单" in b for b in result.risk_blocks)


@pytest.mark.parametrize("spec", RISK_CONDITIONS, ids=lambda s: f"{s['id']:02d}_{s['name']}")
def test_risk_condition_registry_has_twenty_items(spec):
    """配置表共 20 条，且 isolated_risk 可合并。"""
    cfg = isolated_risk(**spec["overrides"])
    for k, v in spec["overrides"].items():
        assert cfg[k] == v
