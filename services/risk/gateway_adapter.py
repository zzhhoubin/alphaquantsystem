# alphaQuantSystem/services/risk/gateway_adapter.py
"""RiskGateway 适配器 —— 接入 SignalPipeline 的统一 evaluate 接口"""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from alphaQuantSystem.core import Direction, SignalData, BarData
from alphaQuantSystem.services.risk.service import RiskContext, RiskResult

if TYPE_CHECKING:
    from alphaQuantSystem.risk.risk_gateway import RiskGateway
    from alphaQuantSystem.services.account import AccountService
    from alphaQuantSystem.services.position import PositionService


class GatewayRiskAdapter:
    """将六层 RiskGateway 适配为 SignalPipeline 可消费的风控评估器。"""

    def __init__(
        self,
        gateway: "RiskGateway",
        position_svc: "PositionService",
        account_svc: "AccountService",
    ):
        self._gateway = gateway
        self._position_svc = position_svc
        self._account_svc = account_svc

    def _sync_data_layer(
        self,
        bar: Optional[BarData] = None,
        *,
        symbol: Optional[str] = None,
    ) -> None:
        """将 PositionService / AccountService 状态同步到 RiskGateway DataLayer。"""
        for sym, pos in self._position_svc.snapshot().items():
            if pos.volume > 0:
                self._gateway.sync_position(
                    sym, Direction.LONG, pos.volume, pos.avg_price, pos.pnl,
                )
            else:
                self._gateway.clear_position(sym)
        if symbol is not None:
            pos = self._position_svc.get(symbol)
            if pos is None or pos.volume <= 0:
                self._gateway.clear_position(symbol)
        total_mv = self._position_svc.total_market_value()
        self._gateway.sync_account(
            balance=self._account_svc.total_value(total_mv),
            available=self._account_svc.available,
            frozen=self._account_svc.locked,
        )
        if bar is not None:
            self._gateway.data_layer.update_bar(bar)

    def evaluate(
        self,
        signal: SignalData,
        ctx: RiskContext,
        *,
        bar: Optional[BarData] = None,
    ) -> RiskResult:
        self._sync_data_layer(bar, symbol=signal.symbol)
        passed, reason = self._gateway.evaluate_signal_sync(signal, dispatch_exec=False)
        return RiskResult(passed=passed, reason=reason)
