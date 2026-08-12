"""风控预设：单条条件隔离配置，供回测/实盘按需启用。"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict

# 其余规则拉到不触发（与 tests/risk_configs.RISK_DISABLED 一致）
RISK_DISABLED: Dict[str, Any] = {
    "enabled": True,
    "take_profit_pct": 9.99,
    "stop_loss_pct": 9.99,
    "daily_drop_stop_pct": 9.99,
    "trailing_tp_activation": 9.99,
    "trailing_tp_callback": 9.99,
    "step_tp_levels": [],
    "step_sl_levels": [],
    "step_close_ratios": [],
    "max_drawdown": 9.99,
    "daily_max_loss": 9_999_999_999,
    "daily_max_loss_pct": 9.99,
    "daily_loss_action": "limit_open",
    "consecutive_loss_limit": 0,
    "consecutive_loss_periods_limit": 0,
    "max_holding_periods": 0,
    "max_holding_seconds": 0,
    "ban_limit_up_down": False,
    "gap_interval_pct": 9.99,
    "abnormal_price_move_pct": 9.99,
    "max_slippage_bp": 9_999_999,
    "slippage_action": "reject",
    "max_position_size": 1.0,
    "max_concentration": 1.0,
    "per_symbol_max_qty": 9_999_999_999,
    "per_symbol_min_qty": 1,
    "max_order_notional": 9_999_999_999,
    "min_order_notional": 0,
    "max_trades_per_day": 9_999_999,
    "cooldown_seconds": 0,
    "blacklist_symbols": [],
    "whitelist_symbols": [],
    "trade_time_windows": [],
    "close_before_minutes": 0,
}


def isolated_risk(**only: Any) -> Dict[str, Any]:
    """在全部关闭基线上仅打开指定风控项。

    示例（只测 1% 固定止损）::

        risk=isolated_risk(stop_loss_pct=0.01)

    示例（日策略 + 分钟风控）::

        risk=isolated_risk(max_drawdown=0.05, monitor={"period": "1m", "price": "close"})
    """
    cfg = deepcopy(RISK_DISABLED)
    cfg.update(only)
    # monitor 字段支持：允许 isolated_risk(..., monitor={...}) 透传
    return cfg


def production_risk(**kwargs: Any) -> Dict[str, Any]:
    """在默认（非禁用）基线上覆盖指定风控项。

    与 isolated_risk 的区别：
      - isolated_risk 从全部关闭基线出发，仅打开传入的规则
      - production_risk 从 DEFAULT_LIMITS 基线出发，覆盖传入值

    示例（日策略 + 盘中 max_drawdown）::

        risk = production_risk(
            max_drawdown=0.05,
            monitor={"period": "1m", "price": "close"},
        )

    示例（纯日终，与现在一致）::

        risk = production_risk(max_drawdown=0.05)
    """
    from .risk_limits import RiskLimits
    cfg = RiskLimits.DEFAULT_LIMITS.copy()
    cfg.update(kwargs)
    return cfg
