"""
场景适配层（Layer 5 / 6）

隔离所有场景差异（Skill 3.5）：
  - 回测：滑点模拟、执行延迟模拟、无容错
  - 实盘：真实交易执行、容错重试、状态恢复

统一不变逻辑（Skill 第九节）：
  - 指标计算、阈值判定、优先级一致
  - 触发条件、执行动作、状态变更一致
"""
from __future__ import annotations
import time
from typing import Optional

import numpy as np
from loguru import logger


class SceneAdapter:
    """场景适配层：提供回测/实盘差异化的执行辅助方法。

    核心原则：
      - 计算层 + 规则引擎层共用同一套代码
      - 仅执行参数（滑点、延迟、容错）按场景分支
    """

    def __init__(self, scene: str = "live"):
        """
        Args:
            scene: 'live' 或 'backtest'
        """
        self.scene = scene

    # ---- 滑点模拟 ----

    def apply_slippage(
        self,
        price: float,
        direction: str,  # 'long' or 'short'
        slippage_bp: float = 1.0,
    ) -> float:
        """对成交价施加滑点。

        回测：模拟不利滑点（买高卖低），防止虚优。
        实盘：直接返回原价（真实市场已含滑点）。

        Args:
            price: 目标价格
            direction: 'long'(买入) 或 'short'(卖出)
            slippage_bp: 滑点基点（万分之一），默认 1bp
        Returns:
            调整后价格
        """
        if self.scene != "backtest":
            return price
        if price <= 0:
            return price
        # 回测模拟不利滑点：买入时价格上浮，卖出时价格下浮
        slip_pct = slippage_bp / 10000.0
        if direction == "long":
            return price * (1.0 + slip_pct)
        else:
            return price * (1.0 - slip_pct)

    # ---- 执行延迟 ----

    def simulate_delay(self, delay_ms: float = 0.0):
        """模拟执行延迟。

        回测：sleep 模拟延迟（禁用时 delay_ms=0 跳过）。
        实盘：不额外加延迟（网络延迟由交易柜台自然产生）。
        """
        if self.scene != "backtest":
            return
        if delay_ms > 0:
            time.sleep(delay_ms / 1000.0)

    def get_default_delay_ms(self) -> float:
        """获取默认执行延迟（毫秒）"""
        if self.scene == "backtest":
            return 10.0  # 回测默认10ms模拟延迟
        return 0.0

    # ---- 成交价格确定 ----

    def determine_fill_price(
        self,
        signal_price: float,
        current_price: float,
        direction: str,
        slippage_bp: float = 1.0,
    ) -> float:
        """确定成交价格。

        回测：用当前价 + 滑点模拟
        实盘：用信号价格（由交易柜台实际成交回报）

        Args:
            signal_price: 信号价格（限价单价格，市价单为0）
            current_price: 当前最新价
            direction: 'long' 或 'short'
            slippage_bp: 滑点基点
        Returns:
            预期/模拟成交价
        """
        # 限价单直接使用信号价
        if signal_price > 0:
            base_price = signal_price
        else:
            # 市价单使用当前价
            base_price = current_price if current_price > 0 else signal_price

        return self.apply_slippage(base_price, direction, slippage_bp)

    # ---- 异常处理 ----

    def handle_retry(
        self,
        attempt: int,
        max_retries: int = 3,
        error: Optional[Exception] = None,
    ) -> bool:
        """容错重试判断。

        实盘：允许重试，达到上限告警。
        回测：不重试，直接返回失败。
        """
        if self.scene == "backtest":
            logger.warning(f"[SceneAdapter] 回测模式不重试: {error}")
            return False
        if attempt >= max_retries:
            logger.error(
                f"[SceneAdapter] 实盘重试 {attempt}/{max_retries} 次失败: {error}"
            )
            return False
        wait_s = 0.5 * (2 ** (attempt - 1))  # 指数退避
        logger.warning(
            f"[SceneAdapter] 第 {attempt} 次重试，等待 {wait_s:.1f}s: {error}"
        )
        time.sleep(wait_s)
        return True

    # ---- 状态恢复 ----

    def needs_state_recovery(self) -> bool:
        """实盘断连后是否需要恢复风控状态"""
        return self.scene == "live"

    # ---- 场景标识 ----

    @property
    def is_backtest(self) -> bool:
        return self.scene == "backtest"

    @property
    def is_live(self) -> bool:
        return self.scene == "live"
