"""
佣金模型 - A股交易成本计算
支持股票、ETF、债券等不同品种的佣金和税费计算
"""
from abc import ABC, abstractmethod
from typing import Optional
from loguru import logger

from alphaQuantSystem.core import TradeData, Direction


class CommissionModel(ABC):
    """佣金模型基类"""

    @abstractmethod
    def calculate(self, trade: TradeData, *, quiet: bool = False) -> float:
        """
        计算交易成本

        参数:
            trade: 成交数据
            quiet: 为 True 时不写日志（内部试算/裁剪用）

        返回:
            总交易成本（佣金+税费）
        """
        pass


class AShareCommission(CommissionModel):
    """
    A股佣金模型
    - 双边佣金（买入+卖出）
    - 卖出单边印花税
    - 佣金最低5元
    """

    def __init__(self,
                 commission_rate: float = 0.0003,
                 stamp_duty_rate: float = 0.001,
                 min_commission: float = 5.0):
        """
        参数:
            commission_rate: 佣金率（默认万三）
            stamp_duty_rate: 印花税率（默认千一，仅卖出）
            min_commission: 最低佣金（默认5元）
        """
        self.commission_rate = commission_rate
        self.stamp_duty_rate = stamp_duty_rate
        self.min_commission = min_commission

    def calculate(self, trade: TradeData, *, quiet: bool = False) -> float:
        """计算A股交易成本"""
        trade_value = trade.volume * trade.price

        # 佣金（双边）
        commission = trade_value * self.commission_rate
        commission = max(commission, self.min_commission)

        # 印花税（仅卖出）
        stamp_duty = 0.0
        if trade.direction == Direction.SHORT:
            stamp_duty = trade_value * self.stamp_duty_rate

        total_cost = commission + stamp_duty

        if not quiet:
            side = "买入" if trade.direction == Direction.LONG else "卖出"
            logger.debug(
                f'[佣金] {trade.symbol} {side} '
                f'成交额={trade_value:.2f} 佣金={commission:.2f} '
                f'印花税={stamp_duty:.2f} 合计={total_cost:.2f}'
            )

        return total_cost


class ETFCommission(CommissionModel):
    """
    ETF/债券佣金模型
    - 双边佣金
    - 无印花税
    - 佣金最低5元（部分券商免5）
    """

    def __init__(self,
                 commission_rate: float = 0.00005,
                 min_commission: float = 0.0):
        """
        参数:
            commission_rate: 佣金率（默认万0.5）
            min_commission: 最低佣金（默认0元，部分券商免5）
        """
        self.commission_rate = commission_rate
        self.min_commission = min_commission

    def calculate(self, trade: TradeData, *, quiet: bool = False) -> float:
        """计算ETF交易成本"""
        trade_value = trade.volume * trade.price

        commission = trade_value * self.commission_rate
        commission = max(commission, self.min_commission)

        if not quiet:
            side = "买入" if trade.direction == Direction.LONG else "卖出"
            logger.debug(
                f'[佣金] {trade.symbol} {side} '
                f'成交额={trade_value:.2f} 佣金={commission:.2f}'
            )

        return commission


class AdaptiveCommission(CommissionModel):
    """
    自适应佣金模型
    根据标的代码自动选择佣金类型
    """

    def __init__(self,
                 stock_commission_rate: float = 0.0003,
                 etf_commission_rate: float = 0.00005,
                 stamp_duty_rate: float = 0.001,
                 min_commission: float = 5.0):
        """
        参数:
            stock_commission_rate: 股票佣金率
            etf_commission_rate: ETF佣金率
            stamp_duty_rate: 印花税率
            min_commission: 最低佣金
        """
        self.stock_model = AShareCommission(
            commission_rate=stock_commission_rate,
            stamp_duty_rate=stamp_duty_rate,
            min_commission=min_commission
        )
        self.etf_model = ETFCommission(
            commission_rate=etf_commission_rate,
            min_commission=0.0  # ETF通常免5
        )

    def calculate(self, trade: TradeData, *, quiet: bool = False) -> float:
        """根据标的类型自动选择佣金模型"""
        if self._is_etf_or_bond(trade.symbol):
            return self.etf_model.calculate(trade, quiet=quiet)
        else:
            return self.stock_model.calculate(trade, quiet=quiet)

    @staticmethod
    def _is_etf_or_bond(symbol: str) -> bool:
        """
        判断是否为ETF或债券
        ETF: 以51/15/16/56/58开头
        债券: 以11/12开头
        """
        code = symbol.split('.')[0]
        if len(code) >= 2:
            prefix = code[:2]
            return prefix in ('51', '15', '16', '56', '58', '11', '12')
        return False
