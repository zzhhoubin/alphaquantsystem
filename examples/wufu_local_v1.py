"""
五福 ETF 轮动策略 — alphaQuantSystem 本地版 v1

从 ``聚宽五福策略.py`` 移植，适配 ``BaseStrategy`` 框架：
- 日 K 回测：在基准标的 ``risk_benchmark`` 的 ``on_bar`` 中串联晨间/午后流水线
- 实盘：可通过 ``schedule`` 注册 ``09:00`` / ``13:10`` / ``15:10`` 定时任务
- 动量打分复用 ``wufu_momentum_score`` 模块
- 固定比例止损建议交给框架风控（``stop_loss_pct=0.05`` + 分钟监控）

用法（回测）::

    python -m alphaQuantSystem.examples.wufu_local_v1 backtest
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from loguru import logger

from alphaQuantSystem import App, BaseStrategy
from alphaQuantSystem.core import BarData, Direction, TradeData
from alphaQuantSystem.data import DataEngine
from alphaQuantSystem.data.tdx_local import filter_tdx_symbols, load_tdx_bars
from alphaQuantSystem.examples.wufu_momentum_score import (
    MomentumScoreConfig,
    apply_filters,
    normalize_symbol,
    score_etf_pool,
)
from alphaQuantSystem.risk.presets import isolated_risk
from alphaQuantSystem.utils.helpers import round_volume, split_order_volumes

# ── 聚宽原版固定 ETF 池（聚宽后缀，注册时统一 normalize）──
FIXED_ETF_POOL_JQ: list[str] = [
    "518880.XSHG", "161226.XSHE", "159980.XSHE", "501018.XSHG", "159985.XSHE",
    "513100.XSHG", "159509.XSHE", "513290.XSHG", "513500.XSHG", "159518.XSHE",
    "159502.XSHE", "159529.XSHE", "513400.XSHG", "520830.XSHG", "513520.XSHG",
    "513030.XSHG", "513090.XSHG", "513180.XSHG", "513120.XSHG", "513330.XSHG",
    "513750.XSHG", "159892.XSHE", "159605.XSHE", "513190.XSHG", "510900.XSHG",
    "513630.XSHG", "513920.XSHG", "159323.XSHE", "513970.XSHG", "510500.XSHG",
    "512100.XSHG", "563300.XSHG", "510300.XSHG", "512050.XSHG", "510760.XSHG",
    "159915.XSHE", "159949.XSHE", "159967.XSHE", "588080.XSHG", "588220.XSHG",
    "511380.XSHG", "513310.XSHG", "588200.XSHG", "159852.XSHE", "512880.XSHG",
    "159206.XSHE", "512400.XSHG", "512980.XSHG", "159516.XSHE", "512480.XSHG",
    "515880.XSHG", "562500.XSHG", "159218.XSHE", "159869.XSHE", "159870.XSHE",
    "159326.XSHE", "159851.XSHE", "560860.XSHG", "159363.XSHE", "588170.XSHG",
    "159755.XSHE", "512170.XSHG", "512800.XSHG", "159819.XSHE", "512710.XSHG",
    "159638.XSHE", "517520.XSHG", "515980.XSHG", "159995.XSHE", "159227.XSHE",
    "512660.XSHG", "512690.XSHG", "516150.XSHG", "512890.XSHG", "588790.XSHG",
    "159992.XSHE", "512070.XSHG", "562800.XSHG", "512010.XSHG", "515790.XSHG",
    "510880.XSHG", "159928.XSHE", "159883.XSHE", "159998.XSHE", "515220.XSHG",
    "561980.XSHG", "515400.XSHG", "515120.XSHG", "159566.XSHE", "515050.XSHG",
    "516510.XSHG", "159256.XSHE", "159766.XSHE", "512200.XSHG", "513350.XSHG",
    "159583.XSHE", "159732.XSHE", "516160.XSHG", "516520.XSHG", "562590.XSHG",
    "515030.XSHG", "512670.XSHG", "561330.XSHG", "516190.XSHG", "159840.XSHE",
    "159611.XSHE", "159981.XSHE", "159865.XSHE", "561360.XSHG", "159667.XSHE",
    "515170.XSHG", "513360.XSHG", "159825.XSHE", "515210.XSHG",
]

DEFAULT_DEFENSIVE_ETF = "511880.XSHG"
DEFAULT_RISK_BENCHMARK = "510300.XSHG"
DEFAULT_HIST_SOURCE: Optional[str] = None

_LIQUIDITY_DAYS = 3
_CONSERVATIVE_THRESHOLD = 10_000_000.0

# ── 动态池分组常量（对齐聚宽 update_sector_pool）──
_FUND_COMPANIES = sorted(list(set([
    '易方达', '广发', '华夏', '华安', '嘉实', '富国', '招商', '鹏华', '南方', '汇添富', '国泰', '平安',
    '银华', '天弘', '建信', '工银', '华泰柏瑞', '博时', '景顺长城', '景顺', '华宝', '申万菱信', '万家', '中欧',
    '兴证全球', '浙商', '诺安', '前海开源', '泰康', '泰达宏利', '农银汇理', '交银', '东方红', '财通', '华商',
    '国联', '永赢', '金鹰', '德邦', '创金合信', '西部利得', '圆信永丰', '泓德', '汇安', '诺德', '恒生前海',
    '华润元大', '大成', '海富通', '摩根', '华泰', '中信', '中银', '兴全', '国信', '长城', '中金', '浙商证券',
    '东海', '东吴', '浦银安盛', '信达澳亚', '中加', '中航', '中融', '中邮', '中庚', '中信保诚', '中信建投',
    '中银国际', '中银证券', '九泰', '交银施罗德', '光大保德信', '兴银', '农银', '国投瑞银', '国海富兰克林',
    '国联安', '国金', '太平', '方正富邦', '民生加银', '汇丰晋信', '银河', '长信', '长安', '长盛', '长江证券', '鹏扬'
])), key=len, reverse=True)
_NOISE_WORDS = sorted(list(set([
    '6666', '8888', '9999', 'A类', 'AH', 'B', 'BS', 'C', 'C类', 'CS', 'DB', 'E', 'E类',
    'ETF', 'ETF基金', 'ETF联接', 'FG', 'G60', 'GF', 'GT', 'HGS', 'LOF', 'LOF基金', 'LOF联接',
    'SG', 'SZ', 'TF', 'TK', 'WJ', 'YH', 'ZS', 'ZZ', '板块', '策略', '产业', '场内', '场外', '低波',
    '基本面', '基金', '精选', '联接', '联接基金', '量化', '龙头', '民企', '民营', '国企', '央企', '智能',
    '全指', '上市开放式', '指基', '指增', '指数', '指数A', '指数C', '指数ETF', '指数基金', '主题', '增强',
    '上海', '黄', '30', '50', '100', '300', '500', '1000', '2000', '大', '新', '四川', '浙江', '湖北',
])), key=len, reverse=True)
_SPECIAL_GROUPS = sorted([
    {'name': '香港组', 'keywords': sorted(
        ['恒生', '恒指', '港股', '港股通', 'H股', '香港', '港', 'HKC', 'HK', 'HGS', 'H', '中概', 'HS科技'], key=len,
        reverse=True),
     'remove_words': sorted(
         ['恒生', '恒指', '港股', '港股通', 'H股', '香港', '港', 'HKC', 'HK', 'HGS', 'H', '中概', 'HS'], key=len,
         reverse=True)},
    {'name': '科创组',
     'keywords': sorted(['科创', '科创板', '科综', 'KC', 'K C', '双创', '科创创业', '创创'], key=len, reverse=True),
     'remove_words': sorted(
         ['科创', '科创板', '科综', 'KC', 'K C', '双创', '科创创业', '创创', '债券', '债汇', '债指', '债沪', '债易',
          '债基', '债兴', '债摩', '债', 'AAA'], key=len, reverse=True)},
    {'name': '创业组', 'keywords': sorted(['创业板', '创业', '创板', '创成长'], key=len, reverse=True),
     'remove_words': sorted(['创业板', '创业', '创板', '创成长'], key=len, reverse=True)},
    {'name': '美指组', 'keywords': sorted(['标普', '纳指', '纳斯达克'], key=len, reverse=True),
     'remove_words': sorted(['标普', '纳指', '纳斯达克'], key=len, reverse=True)}
], key=lambda x: max(len(kw) for kw in x['keywords']), reverse=True)
_EXCLUDE_KEYWORDS = sorted(list(set([
    '300', '500', '1000', '2000', '800', '30', '50', '100', '180', '200',
    '沪深', '中证', '上证', '深证', '深成', 'A50', 'A100', 'A500', '深100',
    '短融', '可转债', '转债', '双债', '利率债', '国债', '地债', '政金债', '国开债', '基准国债', '新综债',
    '信用债', '企业债', '公司债', '城投债', '城投', '美元债', '沪公司债', '科创债', '科债', '科创AAA',
    '自由现金流', '现金流', '现金流E', '现金流基', '现金流TF', '现金流全', '300现金流', '800现金流',
    '货币', '现金', '快线', '快钱', '中银现金', '500现金', '800现金', '现金800', '现金自由', '现金指数',
    '全指现金', '现金全指', 'ESG', 'MSCI', 'MS', '债',
])), key=len, reverse=True)


def _select_recent_trade_days(
        bars_map: dict[str, pd.DataFrame],
        end_date: str,
        count: int = _LIQUIDITY_DAYS,
) -> list[pd.Timestamp]:
    """取截止 end_date（含）的最近 count 个交易日；T0 时 end=previous_date，即 T-3~T-1。"""
    end_ts = pd.to_datetime(end_date, format="%Y%m%d", errors="coerce")
    if pd.isna(end_ts):
        return []
    end_day = pd.Timestamp(end_ts).normalize()
    all_days: set[pd.Timestamp] = set()
    for df in bars_map.values():
        if df is None or df.empty:
            continue
        if "date" in df.columns:
            dts = pd.to_datetime(df["date"], errors="coerce")
        else:
            dts = pd.to_datetime(df.index, errors="coerce")
        for dt in dts.dropna().normalize():
            if dt <= end_day:
                all_days.add(dt)
    return sorted(all_days)[-count:]


def _sum_amount_on_days(df: pd.DataFrame, selected_days: list[pd.Timestamp]) -> float:
    if df is None or df.empty or not selected_days or "amount" not in df.columns:
        return 0.0
    if "date" in df.columns:
        dts = pd.to_datetime(df["date"], errors="coerce")
    else:
        dts = pd.to_datetime(df.index, errors="coerce")
    amounts = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
    normalized_days = pd.DatetimeIndex(dts).normalize()
    selected_set = set(selected_days)
    mask = normalized_days.isin(selected_set)
    return float(amounts[mask].sum())


def _clean_etf_name(
        original_name: str,
        *,
        is_special: bool = False,
        matched_group_name: Optional[str] = None,
) -> str:
    cleaned = original_name
    for company in _FUND_COMPANIES:
        cleaned = cleaned.replace(company, '')
    if is_special and matched_group_name:
        for group in _SPECIAL_GROUPS:
            if group['name'] == matched_group_name:
                for word in group['remove_words']:
                    cleaned = cleaned.replace(word, '')
                break
    for noise in _NOISE_WORDS:
        cleaned = cleaned.replace(noise, '')
    return cleaned.strip()


def build_wufu_symbols(
        fixed_pool: Optional[List[str]] = None,
        *,
        defensive: str = DEFAULT_DEFENSIVE_ETF,
        benchmark: str = DEFAULT_RISK_BENCHMARK,
) -> List[str]:
    """构建策略注册用标的列表（去重、本地后缀）。"""
    pool = fixed_pool or FIXED_ETF_POOL_JQ
    symbols = {normalize_symbol(s) for s in pool}
    symbols.add(normalize_symbol(defensive))
    symbols.add(normalize_symbol(benchmark))
    return sorted(symbols)


def calculate_rsi(close: np.ndarray, period: int = 14) -> Optional[float]:
    if len(close) < period + 1:
        return None
    deltas = np.diff(close)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = float(np.mean(gains[-period:]))
    avg_loss = float(np.mean(losses[-period:]))
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _date_str(value: Optional[date]) -> str:
    if value is None:
        return ""
    return value.strftime("%Y%m%d")


def _fetch_tdx_daily_bars(
        symbol: str,
        end_date: str,
        lookback: int,
) -> pd.DataFrame:
    """从通达信本地日线加载截止 end_date 的历史 K 线。"""
    if not end_date:
        return pd.DataFrame()
    end_ts = pd.Timestamp(end_date)
    start_dt = end_ts - pd.Timedelta(days=lookback * 3)
    bars_map = load_tdx_bars(symbol, freq="day", parallel=False)
    code6 = normalize_symbol(symbol).split(".")[0]
    df = bars_map.get(code6)
    if df is None:
        df = next(iter(bars_map.values()), pd.DataFrame())
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    if "date" in out.columns:
        out["date"] = pd.to_datetime(out["date"], errors="coerce")
    else:
        out["date"] = pd.to_datetime(out.index, errors="coerce")
    out = out.reset_index(drop=True)
    out = out[out["date"].notna()]
    out = out[(out["date"] >= start_dt) & (out["date"] <= end_ts)]
    out = out.sort_values("date").drop_duplicates("date", keep="last")
    return out.tail(lookback)


class WufuLocalV1Strategy(BaseStrategy):
    """五福 ETF 轮动 — 本地 v1（单标的持仓，动量轮动 + 震荡期滤波切换）。"""

    # ── 持仓与资金 ──
    holdings_num: int = 1
    defensive_etf: str = "511880.SH"
    min_money: float = 10.0
    hist_source: Optional[str] = None
    # 买入资金缓冲（对齐聚宽实盘：滑点/佣金/多笔冻结留余量）
    buy_cash_buffer_ratio: float = 0.002
    buy_cash_reserve: float = 50_000.0
    live_slippage: float = 0.001
    buy_commission_rate: float = 0.00005
    live_use_market_order: bool = True

    # ── 动量参数 ──
    lookback_days: int = 25
    min_score_threshold: float = 0.0
    max_score_threshold: float = 5.0
    score_threshold_ratio: float = 0.9
    use_short_momentum_period: bool = False
    short_momentum_lookback: int = 21
    short_momentum_min_score: float = 0.0
    short_momentum_max_score: float = 6.0

    # ── 过滤开关 ──
    enable_r2_filter: bool = True
    r2_threshold: float = 0.4
    enable_volume_check: bool = True
    volume_lookback: int = 5
    volume_threshold: float = 1.8
    enable_loss_filter: bool = True
    loss: float = 0.97
    enable_premium_filter: bool = False
    max_premium_rate: float = 30.0

    # ── 滤波器 ──
    laplace_s_param: float = 0.05
    laplace_min_slope: float = 0.002
    gaussian_sigma: float = 1.2
    gaussian_min_slope: float = 0.002

    # ── 震荡期 ──
    enable_range_bound_mode: bool = True
    risk_benchmark: str = "510300.SH"
    lookback_high_low_days: int = 20
    enable_bias_trigger: bool = True
    bias_threshold: float = 0.08
    ma_period: int = 20
    enable_rsi_trigger: bool = True
    rsi_overbought: float = 70.0
    rsi_pullback: float = 65.0
    enable_stop_loss_trigger: bool = True
    enable_low_point_rise_trigger: bool = True
    low_point_rise_threshold: float = 0.04
    enable_stable_signal_trigger: bool = True
    drawdown_recovery: float = 0.02
    max_range_bound_days: int = 20
    filter_switch_cooldown: int = 3
    drawdown_threshold: float = 0.03

    # ── 止损（策略内标记；实际平仓可交给风控）──
    use_fixed_stop_loss: bool = True
    fixed_stop_loss_threshold: float = 0.95
    use_pct_stop_loss: bool = False
    pct_stop_loss_threshold: float = 0.95

    # ── 池管理 ──
    enable_dynamic_pool: bool = True
    fixed_etf_pool: Optional[List[str]] = None

    # ── 实盘 schedule（与 add_strategy schedule 中 morning_routine 时刻保持一致）──
    live_morning_time: str = "11:29"

    def on_init(self) -> None:
        self._data = DataEngine()
        self._clock_symbol = normalize_symbol(self.risk_benchmark)
        self.defensive_etf = normalize_symbol(self.defensive_etf)
        self.risk_benchmark = self._clock_symbol

        raw_pool = self.fixed_etf_pool or FIXED_ETF_POOL_JQ
        self._fixed_pool = [normalize_symbol(s) for s in raw_pool]

        self._filtered_fixed_pool: List[str] = []
        self._dynamic_pool: List[str] = []
        self._merged_pool: List[str] = []
        self._ranked_result: List[Dict[str, Any]] = []
        self._target_list: List[str] = []
        self._etf_names: Dict[str, str] = {}
        self._avg_threshold: Optional[float] = None

        self._current_filter: str = "正常期"
        self._risk_state: str = "正常期"
        self._previous_rsi: Optional[float] = None
        self._previous_drawdown: Optional[float] = None
        self._stop_loss_triggered_today: bool = False
        self._stop_loss_submitted_today: set[str] = set()
        self._last_switch_date: Optional[date] = None
        self._range_bound_start: Optional[date] = None
        self._range_bound_days: int = 0
        self._stable_days: int = 0
        self._max_portfolio_value: float = 0.0
        self._drawdown_records: List[Dict[str, Any]] = []
        self._last_processed_date: Optional[date] = None
        self._yesterday_close_cache: Dict[str, float] = {}

        logger.info("【五福闹新春】本地 v1 初始化，固定池 {} 只", len(self._fixed_pool))
        self._init_range_bound_status()

    def on_start(self) -> None:
        logger.info(
            "【五福本地 v1】启动 | 持仓数={} | 防御ETF={} | 震荡期={}",
            self.holdings_num, self.defensive_etf, self.enable_range_bound_mode,
        )
        self.enable_catch_up_morning_if_needed = True
        if self.ctx._mode == "live" and self.enable_catch_up_morning_if_needed is True:
            self._catch_up_morning_if_needed()

    def _catch_up_morning_if_needed(self) -> None:
        """盘中启动且已过晨间时刻、合并池为空时补跑 morning_routine。"""
        now_hhmm = datetime.now().strftime("%H:%M")
        if now_hhmm <= self.live_morning_time:
            return
        if self._merged_pool:
            return
        logger.info(
            "【五福】Catch-up morning_routine (started after {}, now {})",
            self.live_morning_time,
            now_hhmm,
        )
        sync = getattr(self.ctx, "_live_sync_broker", None)
        if sync is not None:
            sync()
        self.morning_routine()
        if self._pipeline is not None:
            self._pipeline.drain()

    # ── 回测：基准标的日 K 驱动每日流水线；实盘：仅止损，流水线走 schedule ──
    def on_bar(self, bar: BarData) -> None:
        if self.ctx._mode == "live":
            if self.use_fixed_stop_loss:
                self._minute_fixed_stop_loss(bar)
            if self.use_pct_stop_loss:
                self._minute_pct_stop_loss(bar)
            return

        if bar.symbol != self._clock_symbol:
            return
        trade_date = bar.event_time.date()
        if self._last_processed_date == trade_date:
            return
        self._last_processed_date = trade_date

        if self.use_fixed_stop_loss:
            self._minute_fixed_stop_loss(bar)
        if self.use_pct_stop_loss:
            self._minute_pct_stop_loss(bar)

        self.morning_routine()
        self.afternoon_routine()
        self.reset_daily_flags()

    # ── 实盘定时任务（与聚宽 run_daily 对应）──
    def morning_routine(self) -> None:
        logger.info("▶️ 【晨间流水线】启动")
        self._check_positions()
        self._monitor_drawdown()
        self._update_liquidity_threshold()
        if self.enable_dynamic_pool:
            self._update_dynamic_pool()
        self._filter_fixed_pool()
        self._merge_pools()
        logger.info("⏸️ 【晨间流水线】完成，合并池 {} 只", len(self._merged_pool))

    def afternoon_routine(self) -> None:
        logger.info("▶️ 【午后流水线】启动")
        if not self._merged_pool:
            logger.warning("合并池为空，补跑晨间流水线")
            self.morning_routine()
            if not self._merged_pool:
                logger.warning("补跑晨间后合并池仍为空，跳过午后调仓")
                return
        self._check_exit_range_bound()
        self._check_enter_range_bound()
        self._ranked_result = self._rank_merged_pool()
        self._execute_sells()
        self._drain_and_sync()
        self._execute_buys()
        logger.info("⏸️ 【午后流水线】完成")

    def reset_daily_flags(self) -> None:
        self._yesterday_close_cache.clear()
        self._stop_loss_submitted_today.clear()
        if self._current_filter == "震荡期" and self._range_bound_start is not None:
            days = self._trade_days_between(self._range_bound_start, self.ctx.current_date)
            self._range_bound_days = max(0, days - 1)
            logger.info("📊 震荡期已持续 {} 个交易日", self._range_bound_days)

    def on_trade(self, trade: TradeData) -> None:
        if trade.direction == Direction.SHORT and self.enable_stop_loss_trigger:
            tag = (trade.tag or "").lower()
            if "stop" in tag or "止损" in (trade.reason or ""):
                self._stop_loss_triggered_today = True

    # ── 动量配置 ──
    def _momentum_config(self) -> MomentumScoreConfig:
        return MomentumScoreConfig(
            lookback_days=self.lookback_days,
            min_score_threshold=self.min_score_threshold,
            max_score_threshold=self.max_score_threshold,
            use_short_momentum_period=self.use_short_momentum_period,
            short_momentum_lookback=self.short_momentum_lookback,
            short_momentum_min_score=self.short_momentum_min_score,
            short_momentum_max_score=self.short_momentum_max_score,
            enable_r2_filter=self.enable_r2_filter,
            r2_threshold=self.r2_threshold,
            enable_volume_check=self.enable_volume_check,
            volume_lookback=self.volume_lookback,
            volume_threshold=self.volume_threshold,
            enable_loss_filter=self.enable_loss_filter,
            loss=self.loss,
            enable_premium_filter=self.enable_premium_filter,
            max_premium_rate=self.max_premium_rate,
            laplace_s_param=self.laplace_s_param,
            laplace_min_slope=self.laplace_min_slope,
            gaussian_sigma=self.gaussian_sigma,
            gaussian_min_slope=self.gaussian_min_slope,
            current_filter=self._current_filter,  # type: ignore[arg-type]
            enable_range_bound_mode=self.enable_range_bound_mode,
            hist_source=self.hist_source,
        )

    def _as_of_date(self) -> str:
        """打分/过滤使用的历史截止日（对齐聚宽 previous_date）。"""
        d = self.ctx.previous_date or self.ctx.current_date
        return _date_str(d)

    # ── 流动性 ──
    def _update_liquidity_threshold(self) -> None:
        """对齐聚宽: 用全市场ETF近3个交易日总成交额均值计算门槛。"""
        try:
            end = self._as_of_date()
            if not end:
                self._avg_threshold = _CONSERVATIVE_THRESHOLD
                logger.warning("【流动性阈值】截止日为空，使用保守值 {:.0f} 万", _CONSERVATIVE_THRESHOLD / 1e4)
                return

            etf_codes = filter_tdx_symbols("etf")
            if not etf_codes:
                self._avg_threshold = _CONSERVATIVE_THRESHOLD
                logger.warning("【流动性阈值】未找到本地ETF列表，使用保守值 {:.0f} 万", _CONSERVATIVE_THRESHOLD / 1e4)
                return

            bars_map = load_tdx_bars(etf_codes, freq="day", parallel=True)
            end_ts = pd.to_datetime(end, format="%Y%m%d", errors="coerce")
            if pd.isna(end_ts):
                self._avg_threshold = _CONSERVATIVE_THRESHOLD
                logger.warning("【流动性阈值】截止日解析失败({})，使用保守值 {:.0f} 万", end,
                               _CONSERVATIVE_THRESHOLD / 1e4)
                return

            # 先收集可用交易日，再取截止日前最近3个交易日（不含截止日当天，严格 T-1/T-2/T-3）。
            all_days: set[pd.Timestamp] = set()
            end_day = pd.Timestamp(end_ts).normalize()
            for df in bars_map.values():
                if df is None or df.empty:
                    continue
                if "date" in df.columns:
                    dts = pd.to_datetime(df["date"], errors="coerce")
                else:
                    dts = pd.to_datetime(df.index, errors="coerce")
                for dt in dts.dropna().normalize():
                    if dt < end_day:
                        all_days.add(dt)

            selected_days = sorted(all_days)[-_LIQUIDITY_DAYS:]
            if len(selected_days) < _LIQUIDITY_DAYS:
                self._avg_threshold = _CONSERVATIVE_THRESHOLD
                logger.warning(
                    "【流动性阈值】有效交易日不足{}天(仅{}天)，使用保守值 {:.0f} 万",
                    _LIQUIDITY_DAYS,
                    len(selected_days),
                    _CONSERVATIVE_THRESHOLD / 1e4,
                )
                return

            day_totals: dict[pd.Timestamp, float] = {d: 0.0 for d in selected_days}
            day_counts: dict[pd.Timestamp, int] = {d: 0 for d in selected_days}
            selected_set = set(selected_days)

            for df in bars_map.values():
                if df is None or df.empty:
                    continue
                if "amount" not in df.columns:
                    continue
                if "date" in df.columns:
                    dts = pd.to_datetime(df["date"], errors="coerce")
                else:
                    dts = pd.to_datetime(df.index, errors="coerce")
                amounts = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
                normalized_days = pd.DatetimeIndex(dts).normalize()
                tmp = pd.DataFrame({"day": normalized_days, "amount": amounts})
                tmp = tmp.dropna(subset=["day"])
                tmp = tmp[tmp["day"].isin(selected_set)]
                if tmp.empty:
                    continue
                grouped = tmp.groupby("day", as_index=True)["amount"].sum()
                for day, money in grouped.items():
                    val = float(money)
                    day_totals[day] += val
                    if val > 0:
                        day_counts[day] += 1

            daily_totals = pd.Series(day_totals).sort_index()
            avg_total_money = float(daily_totals.mean())
            threshold = avg_total_money / 20000.0
            self._avg_threshold = threshold

            for day, money in daily_totals.items():
                logger.info(
                    "【流动性阈值】{} 全市场ETF总成交额 {:.2f} 亿 ({}只ETF有成交)",
                    day.date(),
                    money / 1e8,
                    day_counts.get(day, 0),
                )
            logger.info(
                "【流动性阈值】近{}日全市场ETF日均总成交额 {:.2f} 亿，门槛 {:.0f} 万({:,.0f} 元)",
                len(daily_totals),
                avg_total_money / 1e8,
                threshold / 1e4,
                threshold,
            )
        except Exception as e:
            logger.warning("【流动性阈值】计算异常: {}，使用保守值", e)
            self._avg_threshold = _CONSERVATIVE_THRESHOLD

    def _avg_daily_amount(self, symbol: str, days: int = _LIQUIDITY_DAYS) -> float:
        end = self._as_of_date()
        if not end:
            return 0.0
        df = _fetch_tdx_daily_bars(symbol, end, lookback=days + 5)
        if df.empty:
            return 0.0
        tail = df.tail(days)
        if "amount" not in tail.columns:
            return 0.0
        return float(pd.to_numeric(tail["amount"], errors="coerce").fillna(0).sum()) / days

    def _filter_fixed_pool(self) -> None:
        threshold = self._avg_threshold or _CONSERVATIVE_THRESHOLD
        qualified: List[str] = []
        for sym in self._fixed_pool:
            if self._avg_daily_amount(sym) > threshold:
                qualified.append(sym)
        self._filtered_fixed_pool = qualified or list(self._fixed_pool)
        logger.info("【固定池过滤】{} → {} 只", len(self._fixed_pool), len(self._filtered_fixed_pool))

    def _load_etf_name_map(self, codes6: List[str]) -> Dict[str, str]:
        """加载 6 位代码 → 名称映射（spot 仅用于名称，不用于成交额）。"""
        names: Dict[str, str] = {}
        for code6 in codes6:
            sym = normalize_symbol(code6)
            if sym in self._etf_names:
                names[code6] = self._etf_names[sym]
        try:
            spot = self._data.get_etf_spot()
            if spot is not None and not spot.empty:
                code_col = "代码" if "代码" in spot.columns else "stock_code"
                name_col = "名称" if "名称" in spot.columns else "name"
                if code_col in spot.columns:
                    for _, row in spot.iterrows():
                        code6 = str(row.get(code_col, "")).zfill(6)
                        if not code6 or code6 == "000000":
                            continue
                        name = str(row.get(name_col, code6) or code6)
                        names[code6] = name
                        self._etf_names[normalize_symbol(code6)] = name
        except Exception as e:
            logger.debug("【动态池】spot 名称加载失败: {}", e)
        for code6 in codes6:
            names.setdefault(code6, code6)
        return names

    def _batch_avg_daily_amounts(
            self,
            codes6: List[str],
            bars_map: dict[str, pd.DataFrame],
            selected_days: list[pd.Timestamp],
    ) -> pd.Series:
        """批量计算近 N 日日均成交额（通达信本地 amount 列）。"""
        if not selected_days:
            return pd.Series(dtype=float)
        day_count = len(selected_days)
        rows: list[tuple[str, float]] = []
        for code6 in codes6:
            df = bars_map.get(code6)
            total = _sum_amount_on_days(df, selected_days) if df is not None else 0.0
            if total > 0:
                rows.append((code6, total / day_count))
        if not rows:
            return pd.Series(dtype=float)
        out = pd.Series({c: v for c, v in rows})
        return out.sort_values(ascending=False)

    def _update_dynamic_pool(self) -> None:
        """对齐聚宽 update_sector_pool：分组 + 流动性过滤 + 行业去重 + Top100。"""
        logger.info("【动态池更新】开始执行")
        if self._avg_threshold is None:
            logger.info("【动态池更新】阈值未初始化，立即计算")
            self._update_liquidity_threshold()
        dynamic_threshold = self._avg_threshold or _CONSERVATIVE_THRESHOLD

        try:
            etf_codes = filter_tdx_symbols("etf")
            if not etf_codes:
                logger.warning("【动态池更新】未找到本地 ETF 列表")
                self._dynamic_pool = []
                return
            etf_names = self._load_etf_name_map(etf_codes)
        except Exception as e:
            logger.warning("【动态池更新】获取全市场ETF列表失败: {}", e)
            self._dynamic_pool = []
            return

        logger.info("【动态池更新】全市场ETF总数: {}只", len(etf_codes))
        normal_etfs: List[str] = []
        special_etfs: List[str] = []
        special_group_map: Dict[str, str] = {}
        excluded_count = 0

        for code6 in etf_codes:
            try:
                name = etf_names.get(code6, code6)
                is_special = False
                matched_group: Optional[str] = None
                for group in _SPECIAL_GROUPS:
                    for kw in group['keywords']:
                        if kw in name:
                            is_special = True
                            matched_group = group['name']
                            break
                    if is_special:
                        break
                is_excluded = any(k in name for k in _EXCLUDE_KEYWORDS)
                if is_excluded:
                    excluded_count += 1
                    continue
                if is_special:
                    special_etfs.append(code6)
                    if matched_group:
                        special_group_map[code6] = matched_group
                else:
                    normal_etfs.append(code6)
            except Exception:
                continue

        group_counts: Dict[str, int] = {}
        for code6 in special_etfs:
            group_name = special_group_map.get(code6, '未知')
            group_counts[group_name] = group_counts.get(group_name, 0) + 1
        logger.info("【动态池更新】特别组分布: {}", group_counts)
        logger.info("【动态池更新】进入特别组: {}只", len(special_etfs))
        logger.info("【动态池更新】进入普通组: {}只", len(normal_etfs))
        logger.info("【动态池更新】排除ETF: {}只", excluded_count)

        end_date = self._as_of_date()
        if not end_date:
            logger.warning("【动态池更新】截止日为空，跳过")
            self._dynamic_pool = []
            return

        try:
            bars_map = load_tdx_bars(etf_codes, freq="day", parallel=True)
        except Exception as e:
            logger.warning("【动态池更新】加载通达信日线失败: {}", e)
            self._dynamic_pool = []
            return

        selected_days = _select_recent_trade_days(bars_map, end_date, _LIQUIDITY_DAYS)
        if len(selected_days) < _LIQUIDITY_DAYS:
            logger.warning(
                "【动态池更新】有效交易日不足{}天(仅{}天)，跳过",
                _LIQUIDITY_DAYS,
                len(selected_days),
            )
            self._dynamic_pool = []
            return
        logger.info(
            "【动态池更新】成交额窗口 {} ~ {} ({}个交易日)",
            selected_days[0].date(),
            selected_days[-1].date(),
            len(selected_days),
        )

        def filter_by_liquidity(etf_code_list: List[str]) -> tuple[pd.Series, int]:
            if not etf_code_list:
                return pd.Series(dtype=float), 0
            try:
                avg_daily = self._batch_avg_daily_amounts(etf_code_list, bars_map, selected_days)
                qualified = avg_daily[avg_daily > dynamic_threshold].sort_values(ascending=False)
                filtered_out = len(etf_code_list) - len(qualified)
                return qualified, filtered_out
            except Exception:
                return pd.Series(dtype=float), len(etf_code_list)

        normal_qualified, _ = filter_by_liquidity(normal_etfs)
        special_qualified, _ = filter_by_liquidity(special_etfs)
        normal_sorted = normal_qualified.index.tolist()
        special_sorted = special_qualified.index.tolist()
        logger.info("【动态池更新】特别组流动性过滤: {}→{}只", len(special_etfs), len(special_sorted))
        logger.info("【动态池更新】普通组流动性过滤: {}→{}只", len(normal_etfs), len(normal_sorted))

        if not normal_sorted and not special_sorted:
            logger.warning("【动态池更新】无ETF通过流动性过滤")
            self._dynamic_pool = []
            return

        normal_industry_groups: Dict[str, list] = {}
        for code6 in normal_sorted:
            try:
                original_name = etf_names.get(code6, code6)
                money = float(normal_qualified[code6])
                cleaned = _clean_etf_name(original_name, is_special=False)
                if cleaned == '':
                    continue
                industry_key = cleaned[:2] if len(cleaned) >= 2 else cleaned
                normal_industry_groups.setdefault(industry_key, []).append({
                    'code': code6, 'original_name': original_name, 'cleaned_name': cleaned,
                    'money': money, 'group_type': '普通',
                })
            except Exception:
                continue

        special_industry_groups: Dict[str, list] = {}
        for code6 in special_sorted:
            try:
                original_name = etf_names.get(code6, code6)
                matched_group = special_group_map.get(code6, '未知')
                money = float(special_qualified[code6])
                cleaned = _clean_etf_name(
                    original_name, is_special=True, matched_group_name=matched_group,
                )
                if cleaned == '':
                    continue
                industry_key = cleaned[:2] if len(cleaned) >= 2 else cleaned
                group_key = f"{matched_group}_{industry_key}"
                special_industry_groups.setdefault(group_key, []).append({
                    'code': code6, 'original_name': original_name, 'cleaned_name': cleaned,
                    'money': money, 'group_type': matched_group, 'display_group': matched_group,
                })
            except Exception:
                continue

        final_pool_info: list = []
        for items in normal_industry_groups.values():
            final_pool_info.append(sorted(items, key=lambda x: x['money'], reverse=True)[0])
        for items in special_industry_groups.values():
            final_pool_info.append(sorted(items, key=lambda x: x['money'], reverse=True)[0])

        final_pool_info_sorted = sorted(final_pool_info, key=lambda x: x['money'], reverse=True)
        top_100 = final_pool_info_sorted[:100]
        self._dynamic_pool = [normalize_symbol(item['code']) for item in top_100]
        for item in top_100:
            self._etf_names[normalize_symbol(item['code'])] = item['original_name']

        logger.info("【动态池更新完成】动态池共{}只ETF", len(self._dynamic_pool))
        if len(self._dynamic_pool) <= 10:
            for item in top_100[:10]:
                logger.info(
                    "  {} {} 日均成交额: {:.2f}亿",
                    normalize_symbol(item['code']),
                    item['original_name'],
                    item['money'] / 1e8,
                )

    def _merge_pools(self) -> None:
        merged = sorted(set(self._filtered_fixed_pool) | set(self._dynamic_pool))
        self._merged_pool = merged

    # ── 震荡期 ──
    def _fetch_benchmark_ohlc(self, count: int) -> Optional[pd.DataFrame]:
        end = self._as_of_date()
        if not end:
            return None
        df = _fetch_tdx_daily_bars(self.risk_benchmark, end, lookback=count)
        if df.empty:
            return None
        out = df.copy()
        out["date"] = pd.to_datetime(out["date"])
        return out.sort_values("date").drop_duplicates("date", keep="last")

    def _init_range_bound_status(self) -> None:
        if not self.enable_range_bound_mode:
            return
        df = self._fetch_benchmark_ohlc(max(self.ma_period, self.lookback_high_low_days) + 30)
        if df is None or len(df) < max(self.ma_period, self.lookback_high_low_days):
            return
        close = df["close"].to_numpy(dtype=float)
        high = df["high"].to_numpy(dtype=float)
        low = df["low"].to_numpy(dtype=float)
        current = close[-1]
        recent_high = float(np.max(high[-self.lookback_high_low_days:]))
        recent_low = float(np.min(low[-self.lookback_high_low_days:]))
        ma = float(np.mean(close[-self.ma_period:]))
        bias = (current - ma) / ma if ma > 0 else 0.0
        rsi = calculate_rsi(close)
        enter = False
        if self.enable_bias_trigger and bias > self.bias_threshold:
            enter = True
        if self.enable_rsi_trigger and rsi is not None and len(close) >= 15:
            prev_rsi = calculate_rsi(close[:-1])
            if prev_rsi is not None and prev_rsi > self.rsi_overbought and rsi < self.rsi_pullback:
                enter = True
        if enter:
            self._current_filter = "震荡期"
            self._risk_state = "震荡期"
            self._range_bound_start = self.ctx.previous_date or self.ctx.current_date
        else:
            self._current_filter = "正常期"
            self._risk_state = "正常期"
            self._previous_drawdown = (recent_high - current) / recent_high if recent_high > 0 else 0.0
            self._previous_rsi = rsi
        logger.info("【震荡期初始化】{} | 乖离率 {:.2%} | RSI {}", self._current_filter, bias, rsi)

    def _can_switch_filter(self) -> bool:
        if self._last_switch_date is None:
            return True
        days = self._trade_days_between(self._last_switch_date, self.ctx.current_date)
        return days - 1 >= self.filter_switch_cooldown

    def _trade_days_between(self, start: date, end: date) -> int:
        if start is None or end is None:
            return 0
        n = 0
        d = start
        while d <= end:
            if d.weekday() < 5:
                n += 1
            d += timedelta(days=1)
        return n

    def _check_enter_range_bound(self) -> None:
        if not self.enable_range_bound_mode or self._current_filter == "震荡期":
            return
        if not self._can_switch_filter():
            return
        signals: list[str] = []
        df = self._fetch_benchmark_ohlc(max(self.ma_period, self.lookback_high_low_days) + 10)
        if df is not None and len(df) >= self.ma_period:
            close = df["close"].to_numpy(dtype=float)
            price = close[-1]
            if self.enable_bias_trigger:
                ma = float(np.mean(close[-self.ma_period:]))
                bias = (price - ma) / ma if ma > 0 else 0.0
                if bias > self.bias_threshold:
                    signals.append(f"乖离率{bias:.2%}")
            if self.enable_rsi_trigger and len(close) >= 15:
                rsi = calculate_rsi(close)
                prev_rsi = calculate_rsi(close[:-1])
                if rsi is not None and prev_rsi is not None:
                    if prev_rsi > self.rsi_overbought and rsi < self.rsi_pullback and rsi < prev_rsi:
                        signals.append(f"RSI{prev_rsi:.0f}→{rsi:.0f}")
        if self.enable_stop_loss_trigger and self._stop_loss_triggered_today:
            signals.append("今日触发止损")
            self._stop_loss_triggered_today = False
        if signals:
            self._current_filter = "震荡期"
            self._risk_state = "震荡期"
            self._last_switch_date = self.ctx.current_date
            self._range_bound_start = self.ctx.current_date
            self._stable_days = 0
            logger.info("🔔 【进入震荡期】{}", "; ".join(signals))

    def _check_exit_range_bound(self) -> None:
        if not self.enable_range_bound_mode or self._current_filter != "震荡期":
            return
        df = self._fetch_benchmark_ohlc(max(self.ma_period, self.lookback_high_low_days) + 30)
        if df is None:
            return
        close = df["close"].to_numpy(dtype=float)
        high = df["high"].to_numpy(dtype=float)
        low = df["low"].to_numpy(dtype=float)
        price = close[-1]
        recent_high = float(np.max(high[-self.lookback_high_low_days:]))
        recent_low = float(np.min(low[-self.lookback_high_low_days:]))
        drawdown = (recent_high - price) / recent_high if recent_high > 0 else 0.0
        rise = (price - recent_low) / recent_low if recent_low > 0 else 0.0
        ma = float(np.mean(close[-self.ma_period:]))
        rsi = calculate_rsi(close)
        recovery: list[str] = []

        if self.enable_low_point_rise_trigger and rise >= self.low_point_rise_threshold:
            recovery.append(f"低点涨幅{rise:.2%}")
        if self.enable_stable_signal_trigger:
            if price > ma:
                recovery.append("站上均线")
            if len(close) >= 2 and close[-1] > close[-2]:
                recovery.append("价格上涨")
            if self._previous_drawdown is not None and drawdown < self._previous_drawdown:
                recovery.append("回撤收窄")
            if rsi is not None and self._previous_rsi is not None and rsi > self._previous_rsi:
                recovery.append("RSI回升")
            if drawdown < self.drawdown_recovery:
                self._stable_days += 1
            else:
                self._stable_days = 0
        self._previous_drawdown = drawdown
        self._previous_rsi = rsi

        rb_days = 0
        if self._range_bound_start is not None:
            rb_days = self._trade_days_between(self._range_bound_start, self.ctx.current_date) - 1
        low_rise_ok = self.enable_low_point_rise_trigger and rise >= self.low_point_rise_threshold
        stable_ok = (
                self.enable_stable_signal_trigger
                and drawdown < self.drawdown_recovery
                and len(recovery) >= 2
                and self._stable_days >= 2
        )
        force_ok = rb_days >= self.max_range_bound_days
        if (low_rise_ok or stable_ok or force_ok) and self._can_switch_filter():
            self._current_filter = "正常期"
            self._risk_state = "正常期"
            self._last_switch_date = self.ctx.current_date
            self._range_bound_start = None
            self._stable_days = 0
            logger.info("🔔 【退出震荡期】{}", "; ".join(recovery) or f"震荡期满{rb_days}天")

    # ── 动量排序与目标选取（对齐聚宽四步逻辑）──
    @staticmethod
    def _fmt_filter_status(value_str: str, passed: bool) -> str:
        return f"{value_str} {'✅' if passed else '❌'}"

    def _format_metrics_log_line(self, m: Dict[str, Any]) -> str:
        score = m.get("momentum_score", float("-inf"))
        short_score = m.get("short_momentum_score", float("-inf"))
        score_str = f"{score:.4f}" if score != float("-inf") else "nan"
        short_score_str = f"{short_score:.4f}" if short_score != float("-inf") else "nan"
        r2 = m.get("r_squared")
        r2_str = f"{r2:.3f}" if r2 is not None and not (isinstance(r2, float) and math.isnan(r2)) else "nan"
        vol_val = m.get("volume_ratio")
        vol_str = f"{vol_val:.2f}" if vol_val is not None else "N/A"
        day_ratios = m.get("day_ratios") or []
        min_ratio = min(day_ratios) if day_ratios else "N/A"
        if isinstance(min_ratio, float) and not math.isnan(min_ratio):
            loss_val = f"{min_ratio:.4f}"
        else:
            loss_val = str(min_ratio)
        premium = m.get("premium_rate")
        premium_str = f"{premium:.2f}%" if premium is not None else "N/A"
        sym = m.get("etf") or m.get("symbol", "")
        name = m.get("etf_name", sym)
        fmt = self._fmt_filter_status
        return (
            f"{sym} {name}: "
            f"动量得分: {fmt(score_str, m.get('passed_momentum', False))}，"
            f"短期动量: {fmt(short_score_str, m.get('passed_short_momentum', False))}，"
            f"R²: {fmt(r2_str, m.get('passed_r2', False))}，"
            f"成交量比值: {fmt(vol_str, m.get('passed_volume', False))}，"
            f"短期风控: {fmt(loss_val, m.get('passed_loss', False))}，"
            f"溢价率: {fmt(premium_str, m.get('passed_premium', False))}，"
            f"拉普拉斯斜率: {m.get('laplace_slope', 0):.4f} {fmt('', m.get('passed_laplace', False))}，"
            f"高斯斜率: {m.get('gaussian_slope', 0):.4f} {fmt('', m.get('passed_gaussian', False))}"
        )

    def _rank_merged_pool(self) -> List[Dict[str, Any]]:
        if not self._merged_pool:
            return []
        cfg = self._momentum_config()
        use_short = self.use_short_momentum_period
        score_key = "short_momentum_score" if use_short else "momentum_score"
        momentum_label = "短期" if use_short else "原"
        filter_label = "拉普拉斯(正常期)" if self._current_filter == "正常期" else "高斯(震荡期)"

        logger.info("【动量得分计算】使用合并池，合计{}只ETF", len(self._merged_pool))
        logger.info("【当前滤波器】{}", filter_label)
        logger.info(
            "【动量模式】{}",
            "使用短期动量得分(21天,0-6分)" if use_short else "使用动量得分(25天,0-5分)",
        )

        df = score_etf_pool(
            self._merged_pool,
            self._as_of_date(),
            config=cfg,
            names=self._etf_names,
            data_engine=self._data,
            apply_filter=False,
        )
        if df.empty:
            logger.warning("【动量计算】无法获取有效打分数据")
            return []

        all_metrics = df.to_dict("records")
        all_metrics.sort(key=lambda x: x.get(score_key, float("-inf")), reverse=True)

        log_buffer: List[str] = [""]
        log_buffer.append(f">>> 第一步：所有ETF按{momentum_label}动量得分从大到小排序 <<<")
        for m in all_metrics[:100]:
            item = dict(m)
            item["etf"] = m.get("symbol", "")
            log_buffer.append(self._format_metrics_log_line(item))

        filtered = apply_filters(all_metrics, cfg)
        filtered.sort(key=lambda x: x.get(score_key, float("-inf")), reverse=True)
        top10 = filtered[:10]

        log_buffer.append("")
        log_buffer.append(
            f">>> 第二步：符合全部过滤条件的ETF按{momentum_label}动量得分从大到小排序(前10名) <<<"
        )
        if top10:
            for m in top10:
                item = dict(m)
                item["etf"] = m.get("symbol", "")
                log_buffer.append(self._format_metrics_log_line(item))
        else:
            log_buffer.append("（无符合条件的ETF）")
            logger.info("\n".join(log_buffer))
            return []

        if len(top10) >= self.holdings_num:
            ref_item = top10[self.holdings_num - 1]
            reference_score = ref_item.get(score_key, float("-inf"))
            score_threshold = reference_score * self.score_threshold_ratio
            log_buffer.append("")
            log_buffer.append(
                f">>> 第三步：选取{momentum_label}动量得分≥第{self.holdings_num}名"
                f"({ref_item.get('etf_name', ref_item.get('symbol', ''))})"
                f"得分{reference_score:.4f}×{self.score_threshold_ratio}={score_threshold:.4f}的ETF <<<"
            )
            candidates = [m for m in top10 if m.get(score_key, float("-inf")) >= score_threshold]
        else:
            log_buffer.append("")
            log_buffer.append(f">>> 第三步：前10名不足{self.holdings_num}只，全部作为候选池 <<<")
            candidates = top10[:]

        log_buffer.append(
            f"【候选池】共{len(candidates)}只ETF（按{momentum_label}动量得分排序）："
        )
        for i, item in enumerate(candidates):
            sym = item.get("symbol", "")
            log_buffer.append(
                f"  {i + 1}. {item.get('etf_name', sym)}({sym}) {score_key}: {item.get(score_key, 0):.4f}"
            )

        log_buffer.append("")
        log_buffer.append(">>> 第四步：结合当前持仓进行调整 <<<")
        holdings = [sym for sym, pos in self.ctx.portfolio.positions.items() if pos.total_amount > 0]
        log_buffer.append(f"当前持仓ETF：{holdings}")
        cand_map = {m["symbol"]: m for m in candidates}
        retained = [cand_map[s] for s in holdings if s in cand_map]
        log_buffer.append(f"其中存在于候选池中的持仓ETF：{[item['symbol'] for item in retained]}")

        if len(retained) >= self.holdings_num:
            retained.sort(key=lambda x: x.get(score_key, float("-inf")), reverse=True)
            final = retained[: self.holdings_num]
            log_buffer.append(
                f"保留的持仓ETF数量({len(retained)})超过目标持仓数({self.holdings_num})，"
                f"将从保留的ETF中按{momentum_label}动量得分取前{self.holdings_num}只作为最终目标。"
            )
        else:
            need = self.holdings_num - len(retained)
            held = {m["symbol"] for m in retained}
            extra = [m for m in candidates if m["symbol"] not in held][:need]
            final = retained + extra
            log_buffer.append(f"保留持仓ETF {len(retained)}只，还需补充{need}只。")
            if retained:
                log_buffer.append("保留的ETF（按原有顺序）：")
                for item in retained:
                    sym = item["symbol"]
                    log_buffer.append(f"  {item.get('etf_name', sym)}({sym})")
            if extra:
                log_buffer.append("补充的ETF（按动量得分排序）：")
                for i, item in enumerate(extra):
                    sym = item["symbol"]
                    log_buffer.append(
                        f"  {i + 1}. {item.get('etf_name', sym)}({sym}) {score_key}: {item.get(score_key, 0):.4f}"
                    )

        log_buffer.append(f"【最终目标】共{len(final)}只ETF：")
        for i, item in enumerate(final):
            sym = item["symbol"]
            log_buffer.append(f"  {i + 1}. {item.get('etf_name', sym)}({sym})")
        log_buffer.append("=" * 50)
        logger.info("\n".join(log_buffer))

        out: List[Dict[str, Any]] = []
        for m in final:
            item = dict(m)
            item["etf"] = m["symbol"]
            item["etf_name"] = m.get("etf_name", m["symbol"])
            out.append(item)
        return out

    # ── 交易执行 ──
    def _execute_sells(self) -> None:
        logger.info("========== 卖出操作开始 ==========")
        targets: List[str] = []
        if self._ranked_result:
            for m in self._ranked_result[: self.holdings_num]:
                targets.append(m["etf"])
                logger.info("确定最终目标: {} {}", m["etf"], m.get("etf_name", m["etf"]))
        elif self._defensive_available():
            targets = [self.defensive_etf]
            logger.info("🛡️ 确定最终目标(防御模式): {}", self.defensive_etf)
        else:
            logger.info("💤 无最终目标(空仓模式)")
        self._target_list = targets
        target_set = set(targets)
        sell_count = 0
        for sym, pos in list(self.ctx.portfolio.positions.items()):
            if pos.total_amount > 0 and sym not in target_set:
                ok, _ = self._order_target_value(
                    sym, 0.0, pos.price or self._latest_price(sym),
                    reason="轮动卖出", tag="rotate_sell",
                )
                if ok:
                    sell_count += 1
                    logger.info("✅ 已提交卖出: {} 数量={}", sym, pos.total_amount)
                else:
                    logger.info(
                        "❌ 卖出跳过: {} 数量={} 可卖={}（可能 T+1 或价格无效）",
                        sym, pos.total_amount, pos.closeable_amount,
                    )
        logger.info("本次共提交卖出 {} 只ETF", sell_count)
        logger.info("========== 卖出操作完成 ==========")

    def _execute_buys(self) -> None:
        logger.info("========== 买入操作开始 ==========")
        if not self._target_list:
            logger.info("今日无目标ETF，保持空仓")
            logger.info("========== 买入操作完成 ==========")
            return

        held = {s for s, p in self.ctx.portfolio.positions.items() if p.total_amount > 0}
        target_set = set(self._target_list)
        held_in_target = held & target_set
        etfs_to_buy = [s for s in self._target_list if s not in held]
        # 仅统计目标列表内的持仓占用空位；非目标持仓由卖出阶段处理
        max_buy = max(0, self.holdings_num - len(held_in_target))
        num_to_buy = min(len(etfs_to_buy), max_buy)

        if num_to_buy <= 0:
            if not etfs_to_buy:
                logger.info("目标ETF均已持仓，无需买入")
            else:
                logger.info(
                    "目标持仓空位已满 | 当前持仓{}只 目标内{}只 目标持仓数{}",
                    len(held), len(held_in_target), self.holdings_num,
                )
            logger.info("========== 买入操作完成 ==========")
            return

        etfs_to_buy = etfs_to_buy[:num_to_buy]
        cash = self.ctx.portfolio.available_cash
        deployable = self._deployable_cash(cash)
        per_etf = deployable // num_to_buy if num_to_buy else 0.0
        logger.info(
            "当前持仓{}只(目标内{}只), 计划买入{}只 | 可用现金 {:.2f} 可部署 {:.2f} 每只分配 {:.2f}",
            len(held), len(held_in_target), num_to_buy, cash, deployable, per_etf,
        )
        if deployable < self.min_money or per_etf < self.min_money:
            logger.info(
                "可部署金额 {:.2f} 或单只分配 {:.2f} < 最小交易额 {:.2f}，无法买入",
                deployable, per_etf, self.min_money,
            )
            logger.info("========== 买入操作完成 ==========")
            return

        remaining_deployable = deployable
        for i, sym in enumerate(etfs_to_buy):
            if remaining_deployable < self.min_money:
                break
            if i == len(etfs_to_buy) - 1:
                value = remaining_deployable
            else:
                value = min(per_etf, remaining_deployable)
            price = self._latest_price(sym)
            if price <= 0:
                logger.info("❌ 买入跳过: {} 无法获取有效价格", sym)
                continue
            ok, used = self._order_target_value(
                sym, value, price, reason="轮动买入", tag="rotate_buy",
            )
            if ok:
                remaining_deployable = max(0.0, remaining_deployable - used)
                logger.info(
                    "✅ 已提交买入: {} 预算 {:.2f} 已用 {:.2f} 价格 {:.3f} 剩余预算 {:.2f}",
                    sym, value, used, price, remaining_deployable,
                )
            else:
                logger.info("❌ 买入下单失败: {} 目标金额 {:.2f}", sym, value)
        logger.info("========== 买入操作完成 ==========")

    def _deployable_cash(self, available: float) -> float:
        """可用现金扣除比例缓冲与固定保留后的可部署金额。"""
        return max(0.0, available * (1.0 - self.buy_cash_buffer_ratio) - self.buy_cash_reserve)

    @staticmethod
    def _buy_unit_cost(quote_price: float, *, slippage: float, commission_rate: float) -> float:
        """买入每股预估成本（含滑点加价与佣金）。"""
        return (quote_price + slippage) * (1.0 + commission_rate)

    def _submit_order_price(self, quote_price: float) -> float:
        """实盘市价单传 0（由柜台按最新价撮合）；回测仍用限价。"""
        if self.ctx._mode == "live" and self.live_use_market_order:
            return 0.0
        return quote_price

    def _order_target_value(
            self,
            symbol: str,
            target_value: float,
            price: float,
            *,
            reason: str,
            tag: str,
    ) -> tuple[bool, float]:
        if price <= 0:
            return False, 0.0
        if self.ctx._mode == "live" and target_value > 0:
            fresh = self._latest_price(symbol)
            if fresh > 0:
                price = fresh
        pos = self.ctx.portfolio.positions.get(symbol)
        current = pos.total_amount if pos else 0.0
        if target_value > 0:
            unit_cost = self._buy_unit_cost(
                price,
                slippage=self.live_slippage,
                commission_rate=self.buy_commission_rate,
            )
            target_amount = round_volume(symbol, target_value / unit_cost)
        else:
            target_amount = 0
        diff = target_amount - current
        if diff == 0:
            return False, 0.0
        trade_value = abs(diff) * price
        if 0 < trade_value < self.min_money:
            return False, 0.0
        if diff < 0:
            closeable = pos.closeable_amount if pos else 0.0
            if closeable <= 0:
                return False, 0.0
            diff = -min(abs(diff), closeable)
        used_budget = 0.0
        if diff > 0:
            unit_cost = self._buy_unit_cost(
                price,
                slippage=self.live_slippage,
                commission_rate=self.buy_commission_rate,
            )
            chunks = split_order_volumes(symbol, diff)
            if len(chunks) > 1:
                logger.info(
                    "买入拆单 {} 笔 {} 合计 {} 股",
                    len(chunks), symbol, sum(chunks),
                )
            remaining_budget = target_value
            submitted: list[int] = []
            for vol in chunks:
                if remaining_budget < self.min_money:
                    break
                max_vol = round_volume(symbol, remaining_budget / unit_cost)
                vol = min(vol, max_vol)
                if vol <= 0:
                    break
                submitted.append(vol)
                remaining_budget -= vol * unit_cost
            if not submitted:
                return False, 0.0
            submit_price = self._submit_order_price(price)
            order_type = "市价" if submit_price <= 0 else f"限价@{submit_price:.3f}"
            if submit_price <= 0:
                logger.info("买入委托类型: {} {} {}", symbol, order_type, sum(submitted))
            for vol in submitted:
                self.buy(symbol, vol, price=submit_price, reason=reason, tag=tag)
            used_budget = sum(submitted) * unit_cost
        else:
            chunks = split_order_volumes(symbol, abs(diff))
            if len(chunks) > 1:
                logger.info(
                    "卖出拆单 {} 笔 {} 合计 {} 股",
                    len(chunks), symbol, sum(chunks),
                )
            submit_price = self._submit_order_price(price)
            for vol in chunks:
                self.sell(symbol, vol, price=submit_price, reason=reason, tag=tag)
        return True, used_budget

    def _latest_price(self, symbol: str) -> float:
        sym = normalize_symbol(symbol)

        if self.ctx._mode == "live":
            rt = self._data.get_realtime(sym)
            if rt:
                px = float(rt.get("最新价") or 0)
                if px > 0:
                    return px

        pos = self.ctx.portfolio.positions.get(sym) or self.ctx.portfolio.positions.get(symbol)
        if pos and pos.price > 0:
            return pos.price

        if self.ctx._mode == "live":
            return 0.0

        end = self._as_of_date() or _date_str(self.ctx.current_date)
        if end:
            df = _fetch_tdx_daily_bars(sym, end, lookback=10)
            if not df.empty:
                close = float(df["close"].iloc[-1])
                if close > 0:
                    return close
        return 0.0

    def _defensive_available(self) -> bool:
        return normalize_symbol(self.defensive_etf) in self._merged_pool or True

    # ── 监控与止损（日 K 回测近似）──
    def _check_positions(self) -> None:
        for sym, pos in self.ctx.portfolio.positions.items():
            if pos.total_amount > 0:
                logger.info(
                    "📊 持仓 {} 数量={} 成本={:.3f} 现价={:.3f}",
                    sym, pos.total_amount, pos.avg_cost, pos.price,
                )

    def _monitor_drawdown(self) -> None:
        val = self.ctx.portfolio.total_value
        print('vallll', val)
        print('self._max_portfolio_valuess', self._max_portfolio_value)
        if val > self._max_portfolio_value:
            self._max_portfolio_value = val
        if self._max_portfolio_value <= 0:
            return
        dd = (self._max_portfolio_value - val) / self._max_portfolio_value
        if dd >= self.drawdown_threshold:
            logger.info("【回撤预警】{:.2%} | 净值 {:.0f} | 滤波器 {}", dd, val, self._current_filter)

    def _drain_and_sync(self) -> None:
        """实盘午后：先落实卖单并刷新资金/持仓快照，再计算买入。"""
        if self._pipeline is not None:
            self._pipeline.drain()
        sync = self.ctx._live_sync_broker
        if sync is not None:
            sync()

    def _minute_fixed_stop_loss(self, bar: BarData) -> None:
        if self.ctx._mode == "live":
            sym = bar.symbol
            pos = self.ctx.portfolio.positions.get(sym)
            if pos is None or pos.total_amount <= 0 or pos.closeable_amount <= 0:
                return
            if sym in self._stop_loss_submitted_today:
                return
            price = bar.close if bar.close > 0 else pos.price
            cost = pos.avg_cost
            if cost <= 0 or price <= 0:
                return
            if price <= cost * self.fixed_stop_loss_threshold:
                ok, _ = self._order_target_value(sym, 0.0, price, reason="固定比例止损", tag="stop_loss")
                if ok:
                    self._stop_loss_submitted_today.add(sym)
            return

        for sym, pos in list(self.ctx.portfolio.positions.items()):
            if pos.total_amount <= 0 or pos.closeable_amount <= 0:
                continue
            price = pos.price or self._latest_price(sym)
            cost = pos.avg_cost
            if cost <= 0 or price <= 0:
                continue
            if price <= cost * self.fixed_stop_loss_threshold:
                self._order_target_value(sym, 0.0, price, reason="固定比例止损", tag="stop_loss")

    def _minute_pct_stop_loss(self, bar: BarData) -> None:
        end = self._as_of_date()
        if self.ctx._mode == "live":
            sym = bar.symbol
            pos = self.ctx.portfolio.positions.get(sym)
            if pos is None or pos.total_amount <= 0 or pos.closeable_amount <= 0:
                return
            if sym in self._stop_loss_submitted_today:
                return
            yclose = self._yesterday_close_cache.get(sym)
            if yclose is None and end:
                df = _fetch_tdx_daily_bars(sym, end, lookback=3)
                if not df.empty:
                    yclose = float(df["close"].iloc[-1])
                    self._yesterday_close_cache[sym] = yclose
            if not yclose or yclose <= 0:
                return
            price = bar.close if bar.close > 0 else pos.price
            if price <= yclose * self.pct_stop_loss_threshold:
                ok, _ = self._order_target_value(sym, 0.0, price, reason="当日跌幅止损", tag="stop_loss")
                if ok:
                    self._stop_loss_submitted_today.add(sym)
            return

        for sym, pos in list(self.ctx.portfolio.positions.items()):
            if pos.total_amount <= 0 or pos.closeable_amount <= 0:
                continue
            yclose = self._yesterday_close_cache.get(sym)
            if yclose is None and end:
                df = _fetch_tdx_daily_bars(sym, end, lookback=3)
                if not df.empty:
                    yclose = float(df["close"].iloc[-1])
                    self._yesterday_close_cache[sym] = yclose
            if not yclose or yclose <= 0:
                continue
            price = pos.price or self._latest_price(sym)
            if price <= yclose * self.pct_stop_loss_threshold:
                self._order_target_value(sym, 0.0, price, reason="当日跌幅止损", tag="stop_loss")


# ── 回测入口 ──
BACKTEST_START = "20240101"
BACKTEST_END = "20241231"
INITIAL_CASH = 1_000_000
COMMISSION_RATE = 0.0001
SLIPPAGE = 0.0001
WARMUP_BARS = 30


def build_backtest_app() -> App:
    symbols = build_wufu_symbols()
    app = App()
    app.use_qmt(is_live=False)
    app.with_data(sources=["qmt", "akshare"])
    app.with_trading(commission_rate=COMMISSION_RATE, slippage=SLIPPAGE)
    app.add_strategy(
        WufuLocalV1Strategy,
        symbols=symbols,
        period="D",
        warmup_bars=WARMUP_BARS,
        risk=isolated_risk(
            stop_loss_pct=1.0 - 0.95,
            monitor={"period": "1m", "price": "close"},
        ),
        schedule={
            "11:29": "morning_routine",
            "13:41": "afternoon_routine",
            "15:10": "reset_daily_flags",
        },
    )
    return app


def main() -> None:
    import sys
    from alphaQuantSystem.monitor.logger import setup_logger

    mode = sys.argv[1] if len(sys.argv) > 1 else "backtest"
    setup_logger()
    app = build_backtest_app()
    if mode == "backtest":
        result = app.run(
            mode="backtest",
            start=BACKTEST_START,
            end=BACKTEST_END,
            initial_cash=INITIAL_CASH,
        )
        if result is not None:
            result.print_summary()
    else:
        app.run(mode="live")


if __name__ == "__main__":
    main()
