"""
风险限制配置 —— 完整风控参数容器

严格覆盖 Skill 第四-五节所有规则所需参数：
  4.1 价格盈亏风控（止盈止损/追踪/阶梯）
  4.2 回撤与亏损风控
  4.3 仓位风控
  4.4 异常交易风控
  4.5 时间周期风控
  第五节 优先级编码（L1-L4）
"""
import json
from typing import Any, Dict, Optional

from loguru import logger


class RiskLimits:
    """风控参数配置容器，支持 JSON 持久化与运行时覆盖"""

    DEFAULT_LIMITS: Dict[str, Any] = {
        # ========== 场景标识 ==========
        # 'live' 或 'backtest'，决定执行层与适配层的分支行为
        'scene': 'live',

        # ========== 风控监控调度配置 ==========
        # None 表示风控周期跟随策略周期（保持现状）；非 None 时为完整 monitor 字典
        'monitor': None,

        # ========== 4.1 价格盈亏风控（L3） ==========
        # 固定止盈止损（比例制）
        'take_profit_pct': 0.10,         # 止盈阈值（10%）
        'stop_loss_pct': 0.05,           # 止损阈值（5%）
        'daily_drop_stop_pct': 0.05,      # 当日跌幅止损阈值（5%）
        # 动态追踪止盈
        'trailing_tp_activation': 0.08,  # 激活阈值（盈利8%后启动追踪）
        'trailing_tp_callback': 0.03,    # 回撤比例（从最高点回落3%触发平仓）
        # 阶梯止盈止损
        # 'step_tp_levels': [0.05, 0.10, 0.15],    # 多档止盈阈值
        'step_tp_levels': [],    # 多档止盈阈值
        # 'step_sl_levels': [0.03, 0.05, 0.08],    # 多档止损阈值
        'step_sl_levels': [],    # 多档止损阈值
        # 'step_close_ratios': [0.3, 0.4, 0.3],     # 对应每档平仓比例（和必须≤1.0）
        'step_close_ratios': [],     # 对应每档平仓比例（和必须≤1.0）

        # ========== 4.2 回撤与亏损风控（L2） ==========
        'max_drawdown': 0.15,             # 最大回撤阈值
        'daily_max_loss': 500000,         # 单日最大亏损（绝对值）
        'daily_max_loss_pct': 0.05,       # 单日最大亏损（比例）
        'daily_loss_action': 'limit_open',  # 日亏损超限动作: 'limit_open' | 'pause_trade' | 'lock'
        'trailing_dd_window': 20,         # 回撤计算窗口
        # 连续亏损
        'consecutive_loss_limit': 5,      # 连续亏损笔数上限
        'consecutive_loss_action': 'freeze',  # 超限动作: 'limit_open' | 'freeze'
        'consecutive_loss_periods_limit': 10, # 连续亏损周期上限
        'consecutive_periods_action': 'freeze',

        # ========== 4.3 仓位风控（L4） ==========
        'max_leverage': 1.0,
        'max_gross_exposure': 1.0,
        'max_net_exposure': 0.8,
        'max_position_size': 0.3,         # 单策略仓位占比上限
        'max_position_value': 2000000,
        'per_symbol_max_qty': 1000000,
        'per_symbol_min_qty': 100,
        'max_concentration': 0.4,
        'sector_max_exposure': 0.3,
        'pair_max_correlation': 0.9,
        # 交易频率
        'max_trades_per_day': 200,
        'max_orders_per_minute': 30,
        'max_open_orders': 50,
        'max_cancel_ratio': 0.8,
        'cooldown_seconds': 3,
        # 名义金额
        'max_order_notional': 1000000,
        'min_order_notional': 5000,

        # ========== 4.4 异常交易风控（L1） ==========
        'max_slippage_bp': 20,            # 最大滑点(bp)
        'slippage_action': 'log',         # 滑点超限动作: 'log' | 'reject' | 'pause'
        'price_deviation_limit_bp': 50,   # 价格偏离限制(bp)
        'spread_limit_bp': 30,            # 价差限制(bp)
        'gap_interval_pct': 0.03,         # 跳空阈值（比例）
        'abnormal_price_move_pct': 0.05,  # 异常价格波动
        'ban_limit_up_down': True,        # 涨停/跌停禁止交易
        'ban_auction_session': True,      # 集合竞价禁止交易
        'open_gap_limit_pct': 0.05,       # 开盘跳空限制
        'max_cancel_orders_per_minute': 5, # 每分钟最多撤单次数
        'cancel_exceed_action': 'pause',  # 废单过多动作: 'pause' | 'lock'
        'duplicate_order_interval': 2,    # 重复委托检测间隔(秒)
        'order_timeout_seconds': 30,      # 委托超时未成交阈值

        # ========== 4.5 时间周期风控 ==========
        'max_holding_periods': 20,        # 最大持仓K线周期数（超时强制平仓 L2）
        'max_holding_seconds': 14400,     # 最大持仓秒数（4小时）
        'trade_time_windows': [['09:30', '11:30'], ['13:00', '15:00']],
        'close_before_minutes': 5,        # 收盘前N分钟禁止开仓

        # ========== 波动率 ==========
        'var_limit': 0.05,
        'max_annualized_vol': 0.35,
        'atr_multiple_stop': 3.0,
        'vola_regime_block': False,

        # ========== 资金 ==========
        'min_cash_ratio': 0.1,
        'min_free_margin_ratio': 0.2,
        'max_margin_usage': 0.8,

        # ========== 流动性 ==========
        'min_turnover': 5000000,
        'min_volume': 100000,
        'min_top1_depth': 200000,

        # ========== 黑白名单 ==========
        'blacklist_symbols': [],
        'whitelist_symbols': [],
        'banned_sectors': [],

        # ========== 组合 ==========
        'max_portfolio_beta': 1.2,
        'max_factor_exposure': {'SIZE': 1.0, 'VALUE': 1.0},

        # ========== 交易规则开关 ==========
        'auto_deleveraging': True,
        'reduce_size_step': 0.1,
        'block_new_positions': False,
        't_plus_one_enforce': True,
        'short_sell_ban': True,
        'financing_only_symbols': [],
    }

    def __init__(self, config_file: Optional[str] = None):
        self.limits = self.DEFAULT_LIMITS.copy()
        if config_file:
            self.load_config(config_file)

    # ---- 持久化 ----

    def load_config(self, config_file: str):
        """从 JSON 文件加载并合并配置"""
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
            self.limits.update(config.get('risk_limits', {}))
            logger.info(f"风控配置已加载: {config_file}")
        except Exception as e:
            logger.warning(f"加载风险配置文件失败: {e}")

    def save_config(self, config_file: str):
        """保存当前配置到 JSON 文件"""
        try:
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump({'risk_limits': self.limits}, f, indent=4, ensure_ascii=False)
            logger.info(f"风控配置已保存: {config_file}")
        except Exception as e:
            logger.warning(f"保存风险配置文件失败: {e}")

    # ---- 运行时接口 ----

    def update_limits(self, new_limits: Dict[str, Any]):
        """批量更新配置"""
        self.limits.update(new_limits)

    def get_limit(self, name: str, default: Any = None) -> Any:
        """获取单项配置"""
        return self.limits.get(name, default)

    def set_limit(self, name: str, value: Any):
        """设置单项配置"""
        self.limits[name] = value

    @property
    def scene(self) -> str:
        """场景标识: 'live' 或 'backtest'"""
        return self.limits.get('scene', 'live')

    @scene.setter
    def scene(self, value: str):
        self.limits['scene'] = value

    def to_dict(self) -> Dict[str, Any]:
        """返回完整配置副本"""
        return self.limits.copy()

    # ---- 风控条件摘要 ----

    def describe(self) -> str:
        """生成当前风控条件摘要，按 L1→L4 分组，自动识别已关闭项。

        用于策略启动时输出，方便确认当前生效的风控参数。
        """
        L = self.limits
        lines = ["[风控条件] 当前策略风控参数:"]

        # ---- L1 异常交易 ----
        l1 = []
        l1.append(f"涨跌停禁止={'是' if L.get('ban_limit_up_down') else '否'}")
        l1.append(f"集合竞价禁止={'是' if L.get('ban_auction_session') else '否'}")
        l1.append(f"滑点阈值={L.get('max_slippage_bp')}bp({L.get('slippage_action')})")
        l1.append(f"价格跳空={L.get('gap_interval_pct'):.0%}")
        l1.append(f"异常波动={L.get('abnormal_price_move_pct'):.0%}")
        l1.append(f"价格偏离={L.get('price_deviation_limit_bp')}bp")
        l1.append(f"价差限制={L.get('spread_limit_bp')}bp")
        l1.append(f"开盘跳空={L.get('open_gap_limit_pct'):.0%}")
        cancel = L.get('max_cancel_orders_per_minute', 0)
        cancel_act = L.get('cancel_exceed_action', 'pause')
        l1.append(f"废单上限={'关' if cancel == 0 else f'{cancel}次/分({cancel_act})'}")
        l1.append(f"重复委托间隔={L.get('duplicate_order_interval')}s")
        l1.append(f"委托超时={L.get('order_timeout_seconds')}s")
        lines.append("  L1 异常交易: " + " | ".join(l1))

        # ---- L2 回撤与亏损 ----
        l2 = []
        l2.append(f"最大回撤={L.get('max_drawdown'):.0%}")
        l2.append(f"单日亏损={L.get('daily_max_loss'):,.0f}({L.get('daily_loss_action')})")
        l2.append(f"单日回撤={L.get('daily_max_loss_pct'):.0%}")
        consec = L.get('consecutive_loss_limit', 0)
        consec_act = L.get('consecutive_loss_action', 'freeze')
        l2.append(f"连续亏损笔数={'关' if consec == 0 else f'{consec}笔({consec_act})'}")
        consec_p = L.get('consecutive_loss_periods_limit', 0)
        consec_p_act = L.get('consecutive_periods_action', 'freeze')
        l2.append(f"连续亏损周期={'关' if consec_p == 0 else f'{consec_p}周期({consec_p_act})'}")
        hp = L.get('max_holding_periods', 0)
        hs = L.get('max_holding_seconds', 0)
        if hp == 0 and hs == 0:
            l2.append("持仓超时=关")
        else:
            parts = []
            if hp > 0:
                parts.append(f"{hp}周期")
            if hs > 0:
                parts.append(f"{hs}s")
            l2.append(f"持仓超时={'/'.join(parts)}")
        l2.append(f"VaR={L.get('var_limit'):.0%}")
        l2.append(f"年化波动上限={L.get('max_annualized_vol'):.0%}")
        l2.append(f"ATR止损={L.get('atr_multiple_stop')}倍")
        l2.append(f"高波阻止={'是' if L.get('vola_regime_block') else '否'}")
        lines.append("  L2 回撤止损: " + " | ".join(l2))

        # ---- L3 止盈止损 ----
        l3 = []
        l3.append(f"固定止盈={L.get('take_profit_pct'):.0%}")
        l3.append(f"固定止损={L.get('stop_loss_pct'):.0%}")
        l3.append(f"当日跌幅止损={L.get('daily_drop_stop_pct'):.0%}")
        l3.append(f"追踪止盈(激活{L.get('trailing_tp_activation'):.0%}/回落{L.get('trailing_tp_callback'):.0%})")
        step_tp = L.get('step_tp_levels', [])
        step_sl = L.get('step_sl_levels', [])
        ratios = L.get('step_close_ratios', [])
        if step_tp:
            tp_str = "/".join(f"{v:.0%}" for v in step_tp)
            r_str = "/".join(f"{r:.0%}" for r in ratios[:len(step_tp)]) if ratios else "?"
            l3.append(f"阶梯止盈={tp_str}(平{r_str})")
        else:
            l3.append("阶梯止盈=关")
        if step_sl:
            sl_str = "/".join(f"{v:.0%}" for v in step_sl)
            r_str = "/".join(f"{r:.0%}" for r in ratios[:len(step_sl)]) if ratios else "?"
            l3.append(f"阶梯止损={sl_str}(平{r_str})")
        else:
            l3.append("阶梯止损=关")
        lines.append("  L3 止盈止损: " + " | ".join(l3))

        # ---- L4 仓位与额度 ----
        l4 = []
        l4.append(f"总仓位≤{L.get('max_position_size'):.0%}")
        l4.append(f"单标的≤{L.get('max_concentration'):.0%}")
        l4.append(f"单笔手数[{L.get('per_symbol_min_qty'):,},{L.get('per_symbol_max_qty'):,}]")
        l4.append(f"名义金额[{L.get('min_order_notional'):,},{L.get('max_order_notional'):,}]")
        max_trades = L.get('max_trades_per_day', 0)
        l4.append(f"日交易={'关' if max_trades == 0 else f'≤{max_trades}次'}")
        cd = L.get('cooldown_seconds', 0)
        l4.append(f"冷却={'关' if cd == 0 else f'{cd}s'}")
        l4.append(f"杠杆≤{L.get('max_leverage')}")
        l4.append(f"净敞口≤{L.get('max_net_exposure'):.0%}")
        l4.append(f"行业集中度≤{L.get('sector_max_exposure'):.0%}")
        l4.append(f"组合Beta≤{L.get('max_portfolio_beta')}")
        bl = L.get('blacklist_symbols', [])
        wl = L.get('whitelist_symbols', [])
        l4.append(f"黑名单={'关' if not bl else f'{len(bl)}个'}")
        l4.append(f"白名单={'关' if not wl else f'{len(wl)}个'}")
        tw = L.get('trade_time_windows', [])
        l4.append(f"交易时段={'不限' if not tw else f'{len(tw)}个窗口'}")
        l4.append(f"收盘前{L.get('close_before_minutes')}分禁开")
        lines.append("  L4 仓位限制: " + " | ".join(l4))

        # ---- 交易规则开关 ----
        sw = []
        sw.append(f"自动去杠杆={'是' if L.get('auto_deleveraging') else '否'}")
        sw.append(f"全局禁开={'是' if L.get('block_new_positions') else '否'}")
        sw.append(f"T+1={'是' if L.get('t_plus_one_enforce') else '否'}")
        sw.append(f"禁卖空={'是' if L.get('short_sell_ban') else '否'}")
        lines.append("  交易开关: " + " | ".join(sw))

        return "\n".join(lines)
