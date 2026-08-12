"""P1 修复回归：实盘 sync_mode、RiskGateway 接入 SignalPipeline。"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from alphaQuantSystem.core import BarData, Direction, EventEngine, PositionData, SignalData
from alphaQuantSystem.risk.presets import isolated_risk
from alphaQuantSystem.risk.risk_gateway import RiskGateway
from alphaQuantSystem.engine.engine import StrategyEngine
from alphaQuantSystem.engine.signal_pipeline import SignalPipeline
from alphaQuantSystem.services.account import AccountService
from alphaQuantSystem.services.position import PositionService
from alphaQuantSystem.services.risk import GatewayRiskAdapter, RiskContext


def test_live_event_engine_uses_sync_mode():
    engine = StrategyEngine()
    ee = engine._ensure_event_engine()
    assert ee._sync_mode is True


def test_gateway_risk_adapter_blocks_via_evaluate_signal_sync():
    gateway = MagicMock()
    gateway.evaluate_signal_sync.return_value = (False, "notional too small")

    adapter = GatewayRiskAdapter(
        gateway=gateway,
        position_svc=PositionService(),
        account_svc=AccountService(1_000_000),
    )
    signal = SignalData(
        strategy_id="t", symbol="600000", direction=Direction.LONG,
        volume=100, price=10.0,
    )
    bar = BarData(
        symbol="600000", open=10, high=11, low=9, close=10.0, volume=1000,
        event_time=datetime(2024, 1, 2), interval="D",
    )

    result = adapter.evaluate(signal, RiskContext(), bar=bar)

    assert result.passed is False
    assert result.reason == "notional too small"
    gateway.evaluate_signal_sync.assert_called_once_with(signal, dispatch_exec=False)


def test_pnl_pct_uses_synced_position_avg_not_stale_entry_cache():
    """浮动盈亏应按当前持仓均价计算，不能被 _entry_prices 旧值带偏。"""
    ee = EventEngine(sync_mode=True)
    from alphaQuantSystem.risk.risk_limits import RiskLimits
    limits = RiskLimits()
    limits.update_limits(isolated_risk(stop_loss_pct=0.01))
    gw = RiskGateway(ee, limits)
    gw.sync_account(balance=1_000_000, available=0, frozen=0)
    gw.sync_position("159509.SZ", Direction.LONG, 986_400, 1.701, pnl=-24_660)
    gw.calc_layer._entry_prices["159509.SZ"] = 1.859  # 模拟旧仓位残留
    gw.data_layer.update_bar(BarData(
        symbol="159509.SZ", open=1.68, high=1.69, low=1.67, close=1.676,
        volume=1e6, event_time=datetime(2024, 7, 12), interval="D",
    ))
    snap = gw.data_layer.snapshot("159509.SZ")
    ind = gw.calc_layer.compute(snap)
    assert ind.unrealized_pnl_pct == pytest.approx((1.676 - 1.701) / 1.701, rel=1e-4)


def test_stop_loss_idempotency_resets_on_new_open():
    """上一笔持仓触发止损缓存后，新开仓应能再次触发止损。"""
    ee = EventEngine(sync_mode=True)
    from alphaQuantSystem.risk.risk_limits import RiskLimits
    limits = RiskLimits()
    limits.update_limits(isolated_risk(stop_loss_pct=0.01))
    gw = RiskGateway(ee, limits)

    gw.sync_account(balance=1_000_000, available=500_000, frozen=0)
    gw.sync_position("159509.SZ", Direction.LONG, 1000, 10.0, pnl=-200)
    gw.on_strategy_open("159509.SZ", 10.0, datetime(2024, 1, 2))
    gw.data_layer.update_bar(BarData(
        symbol="159509.SZ", open=9.8, high=9.9, low=9.7, close=9.85,
        volume=1e6, event_time=datetime(2024, 1, 3), interval="D",
    ))
    events1 = gw.check_monitoring_risk("159509.SZ")
    assert any(e.rule_id.endswith("stop_loss") for e in events1)

    gw.on_strategy_close("159509.SZ", -150, datetime(2024, 1, 4))
    gw.on_strategy_open("159509.SZ", 20.0, datetime(2024, 2, 1))
    gw.sync_position("159509.SZ", Direction.LONG, 1000, 20.0, pnl=-250)
    gw.data_layer.update_bar(BarData(
        symbol="159509.SZ", open=19.5, high=19.6, low=19.4, close=19.75,
        volume=1e6, event_time=datetime(2024, 2, 2), interval="D",
    ))
    events2 = gw.check_monitoring_risk("159509.SZ")
    assert any(e.rule_id.endswith("stop_loss") for e in events2), "新仓位应能再次触发止损"


def test_max_drawdown_not_fired_same_day_as_buy_when_account_already_in_drawdown():
    """开仓日不因历史账户回撤误平：峰值应在买入成交后按本笔持仓重新计量。"""
    ee = EventEngine(sync_mode=True)
    from alphaQuantSystem.risk.risk_limits import RiskLimits
    limits = RiskLimits()
    limits.update_limits(isolated_risk(max_drawdown=0.05))
    gw = RiskGateway(ee, limits)
    gw.set_equity_baseline(1_000_000)
    gw.calc_layer._peak_equity = 1_000_000
    equity_after_prior_loss = 890_000.0  # 相对历史峰值已回撤 11%
    gw.sync_account(balance=equity_after_prior_loss, available=200_000, frozen=0)
    gw.on_strategy_open(
        "159509.SZ", 1.553, datetime(2024, 9, 13),
        equity=equity_after_prior_loss,
    )
    gw.sync_position("159509.SZ", Direction.LONG, 952_400, 1.553, pnl=0)
    gw.data_layer.update_bar(BarData(
        symbol="159509.SZ", open=1.55, high=1.56, low=1.54, close=1.553,
        volume=1e6, event_time=datetime(2024, 9, 13), interval="D",
    ))
    events = gw.check_monitoring_risk("159509.SZ")
    dd_events = [e for e in events if e.rule_id.endswith("max_drawdown")]
    assert dd_events == []


def test_buy_after_close_does_not_emit_ghost_stop_loss():
    """平仓后 DataLayer 应清空，下次买入评估不得误报止损。"""
    ee = EventEngine(sync_mode=True)
    from alphaQuantSystem.risk.risk_limits import RiskLimits
    limits = RiskLimits()
    limits.update_limits(isolated_risk(stop_loss_pct=0.01))
    gw = RiskGateway(ee, limits)
    gw.sync_account(balance=1_000_000, available=500_000, frozen=0)
    gw.sync_position("159509.SZ", Direction.LONG, 986_400, 1.701, pnl=-24_660)
    gw.on_strategy_open("159509.SZ", 1.701, datetime(2024, 7, 8))
    gw.data_layer.update_bar(BarData(
        symbol="159509.SZ", open=1.68, high=1.69, low=1.67, close=1.676,
        volume=1e6, event_time=datetime(2024, 7, 12), interval="D",
    ))
    gw.on_strategy_close("159509.SZ", -24_660, datetime(2024, 7, 12))
    assert not gw.data_layer.snapshot("159509.SZ").has_position

    gw.data_layer.update_bar(BarData(
        symbol="159509.SZ", open=1.54, high=1.55, low=1.52, close=1.533,
        volume=1e6, event_time=datetime(2024, 8, 14), interval="D",
    ))
    buy = SignalData(
        strategy_id="DualEma", symbol="159509.SZ",
        direction=Direction.LONG, volume=1_000_000, price=1.533,
        event_time=datetime(2024, 8, 14),
    )
    passed, reason = gw.evaluate_signal_sync(buy, dispatch_exec=False)
    assert passed is True, reason
    stop_loss_logs = [
        r for r in gw.log_layer._records
        if "stop_loss" in r.get("rule_id", "")
    ]
    assert stop_loss_logs == []


def test_gateway_adapter_clears_stale_position_before_buy_evaluate():
    """Adapter 同步时应对空仓标的清除 DataLayer 残留。"""
    ee = EventEngine(sync_mode=True)
    from alphaQuantSystem.risk.risk_limits import RiskLimits
    limits = RiskLimits()
    limits.update_limits(isolated_risk(stop_loss_pct=0.01))
    gw = RiskGateway(ee, limits)
    gw.sync_account(balance=1_000_000, available=500_000, frozen=0)
    gw.sync_position("159509.SZ", Direction.LONG, 986_400, 1.701, pnl=0)

    pos_svc = PositionService()
    acc_svc = AccountService(1_000_000)
    adapter = GatewayRiskAdapter(gateway=gw, position_svc=pos_svc, account_svc=acc_svc)

    bar = BarData(
        symbol="159509.SZ", open=1.54, high=1.55, low=1.52, close=1.533,
        volume=1e6, event_time=datetime(2024, 8, 14), interval="D",
    )
    buy = SignalData(
        strategy_id="DualEma", symbol="159509.SZ",
        direction=Direction.LONG, volume=100_000, price=1.533,
        event_time=datetime(2024, 8, 14),
    )
    result = adapter.evaluate(buy, RiskContext(), bar=bar)
    assert result.passed is True
    assert not gw.data_layer.snapshot("159509.SZ").has_position
    stop_loss_logs = [
        r for r in gw.log_layer._records
        if "stop_loss" in r.get("rule_id", "")
    ]
    assert stop_loss_logs == []


def test_evaluate_signal_sync_allows_risk_close_sell_when_position_ratio_over_limit():
    """止损强平卖出不得被持仓监控里的 L4 REJECT_ORDER 拦截。"""
    ee = EventEngine(sync_mode=True)
    from alphaQuantSystem.risk.risk_limits import RiskLimits
    limits = RiskLimits()
    limits.update_limits(isolated_risk(stop_loss_pct=0.01, max_position_size=1.0))
    gw = RiskGateway(ee, limits)
    gw.sync_account(balance=1_000_000, available=0, frozen=0)
    gw.sync_position("159509.SZ", Direction.LONG, 1_000_000, 1.0, pnl=-20_000)
    gw.on_strategy_open("159509.SZ", 1.0, datetime(2024, 1, 2))
    gw.data_layer.update_bar(BarData(
        symbol="159509.SZ", open=0.98, high=0.99, low=0.97, close=0.98,
        volume=1e6, event_time=datetime(2024, 1, 3), interval="D",
    ))

    sell = SignalData(
        strategy_id="DualEma", symbol="159509.SZ",
        direction=Direction.SHORT, volume=1_000_000, price=0.98,
        event_time=datetime(2024, 1, 3), tag="risk_close",
    )
    passed, reason = gw.evaluate_signal_sync(sell, dispatch_exec=False)
    assert passed is True, reason


def test_create_gateway_risk_registers_on_shared_event_engine():
    engine = StrategyEngine()
    from alphaQuantSystem.engine.engine import StrategyReg
    reg = StrategyReg(strategy_id="demo", risk={"min_order_notional": 5000})

    with patch("alphaQuantSystem.engine.engine.RiskGateway") as mock_gw_cls:
        mock_gw_cls.return_value = MagicMock()
        adapter = engine._create_gateway_risk(reg, scene="backtest")

    assert isinstance(adapter, GatewayRiskAdapter)
    assert engine._event_engine is not None
    assert engine._event_engine._sync_mode is True
    mock_gw_cls.assert_called_once()
