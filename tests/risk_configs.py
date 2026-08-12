"""20 条风控条件的隔离配置：仅启用被测项，其余全部关闭。"""
from __future__ import annotations

from typing import Any, Dict, List, TypedDict

from alphaQuantSystem.risk.presets import RISK_DISABLED, isolated_risk


# 20 条标准风控条件（与 RiskLimits.describe 分组对应）
RISK_CONDITIONS: List[RiskConditionSpec] = [
    {"id": 1, "name": "固定止损1%", "overrides": {"stop_loss_pct": 0.01}},
    {"id": 2, "name": "固定止盈5%", "overrides": {"take_profit_pct": 0.05}},
    {"id": 3, "name": "当日跌幅止损3%", "overrides": {"daily_drop_stop_pct": 0.03}},
    {
        "id": 4,
        "name": "追踪止盈",
        "overrides": {
            "trailing_tp_activation": 0.05,
            "trailing_tp_callback": 0.02,
            "take_profit_pct": 9.99,
        },
    },
    {
        "id": 5,
        "name": "阶梯止盈",
        "overrides": {
            "step_tp_levels": [0.03, 0.06],
            "step_sl_levels": [],
            "step_close_ratios": [0.5, 1.0],
            "take_profit_pct": 9.99,
        },
    },
    {
        "id": 6,
        "name": "阶梯止损",
        "overrides": {
            "step_tp_levels": [],
            "step_sl_levels": [0.02],
            "step_close_ratios": [1.0],
            "stop_loss_pct": 9.99,
        },
    },
    {"id": 7, "name": "最大回撤2%", "overrides": {"max_drawdown": 0.02}},
    {"id": 8, "name": "单日绝对亏损", "overrides": {"daily_max_loss": 5000}},
    {"id": 9, "name": "单日回撤比例2%", "overrides": {"daily_max_loss_pct": 0.02}},
    {"id": 10, "name": "连续亏损2笔", "overrides": {"consecutive_loss_limit": 2}},
    {"id": 11, "name": "连续亏损3周期", "overrides": {"consecutive_loss_periods_limit": 3}},
    {"id": 12, "name": "持仓超时3周期", "overrides": {"max_holding_periods": 3}},
    {"id": 13, "name": "跌停强平", "overrides": {"ban_limit_up_down": True}},
    {"id": 14, "name": "价格跳空2%", "overrides": {"gap_interval_pct": 0.02}},
    {"id": 15, "name": "异常波动3%", "overrides": {"abnormal_price_move_pct": 0.03}},
    {"id": 16, "name": "滑点超限拒单", "overrides": {"max_slippage_bp": 10, "slippage_action": "reject"}},
    {"id": 17, "name": "最小名义金额5000", "overrides": {"min_order_notional": 5000}},
    {"id": 18, "name": "最大名义金额5000", "overrides": {"max_order_notional": 5000}},
    {"id": 19, "name": "冷却86400秒", "overrides": {"cooldown_seconds": 86400}},
    {"id": 20, "name": "黑名单", "overrides": {"blacklist_symbols": ["159509"]}},
]
