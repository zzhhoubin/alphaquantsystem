"""
五福 ETF 轮动策略 — 动量打分模块

从 ``聚宽五福策略.py`` 抽取动量计算与过滤逻辑，脱离聚宽运行环境。
输入 ETF 池与日期，返回池内各标的的动量得分及辅助指标。

用法::

    from alphaQuantSystem.examples.wufu_momentum_score import score_etf_pool, MomentumScoreConfig

    df = score_etf_pool(
        ["518880.SH", "513100.SH", "159915.SZ"],
        "2026-06-10",
    )
    print(df.sort_values("momentum_score", ascending=False))
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Literal, Optional, Union

import numpy as np
import pandas as pd

from alphaQuantSystem.data import DataEngine
from alphaQuantSystem.data.tdx_local import load_tdx_bars
from alphaQuantSystem.utils.helpers import adjust_symbol

DateLike = Union[str, date, datetime]

# 聚宽代码后缀 → 本地后缀
_JQ_SUFFIX = {
    "XSHG": ".SH",
    "XSHE": ".SZ",
}


def normalize_symbol(symbol: str) -> str:
    """聚宽/本地代码统一为 ``XXXXXX.SH`` / ``XXXXXX.SZ``。"""
    s = symbol.strip().upper()
    if "." in s:
        code, suffix = s.rsplit(".", 1)
        mapped = _JQ_SUFFIX.get(suffix)
        if mapped:
            return code[:6] + mapped
        if suffix in ("SH", "SZ"):
            return code[:6] + "." + suffix
    return adjust_symbol(s[:6])


def _to_date_str(value: DateLike) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y%m%d")
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    s = str(value).strip().replace("-", "").replace("/", "")
    if len(s) != 8:
        raise ValueError(f"无法解析日期: {value!r}，请使用 YYYY-MM-DD 或 YYYYMMDD")
    return s


@dataclass
class MomentumScoreConfig:
    """与五福策略 ``initialize`` 中动量相关参数保持一致。"""

    lookback_days: int = 25
    min_score_threshold: float = 0
    max_score_threshold: float = 5

    use_short_momentum_period: bool = False
    short_momentum_lookback: int = 21
    short_momentum_min_score: float = 0
    short_momentum_max_score: float = 6

    enable_r2_filter: bool = True
    r2_threshold: float = 0.4

    enable_volume_check: bool = True
    volume_lookback: int = 5
    volume_threshold: float = 1.8

    enable_loss_filter: bool = True
    loss: float = 0.97

    enable_premium_filter: bool = False
    max_premium_rate: float = 30.0

    laplace_s_param: float = 0.05
    laplace_min_slope: float = 0.002
    gaussian_sigma: float = 1.2
    gaussian_min_slope: float = 0.002

    current_filter: Literal["正常期", "震荡期"] = "正常期"
    enable_range_bound_mode: bool = True

    hist_source: Optional[str] = None
    min_bars_ratio: float = 0.8


def calculate_momentum_score(
    price_series: np.ndarray,
    lookback_days: int,
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """加权对数回归动量得分 = 年化收益 × R²（与聚宽原版一致）。"""
    if len(price_series) < lookback_days + 1:
        return None, None, None

    recent = price_series[-(lookback_days + 1) :]
    y = np.log(recent)
    x = np.arange(len(y))
    weights = np.linspace(1, 2, len(y))
    w = weights**2
    w_sum = np.sum(w)
    x_bar = np.sum(w * x) / w_sum
    y_bar = np.sum(w * y) / w_sum
    dx = x - x_bar
    dy = y - y_bar
    variance_x = np.sum(w * dx**2)
    if variance_x == 0:
        return 0.0, 0.0, 0.0

    slope = np.sum(w * dx * dy) / variance_x
    intercept = y_bar - slope * x_bar
    annualized_returns = math.exp(slope * 250) - 1
    y_pred = slope * x + intercept
    ss_res = np.sum(weights * (y - y_pred) ** 2)
    ss_tot = np.sum(weights * (y - np.mean(y)) ** 2)
    r_squared = 1 - ss_res / ss_tot if ss_tot else 0.0
    momentum_score = annualized_returns * r_squared
    return momentum_score, annualized_returns, r_squared


def laplace_filter(price: np.ndarray, s: float = 0.05) -> np.ndarray:
    alpha = 1 - np.exp(-s)
    result = np.zeros(len(price))
    result[0] = price[0]
    for t in range(1, len(price)):
        result[t] = alpha * price[t] + (1 - alpha) * result[t - 1]
    return result


def gaussian_filter_last_two(price: np.ndarray, sigma: float = 1.2) -> tuple[float, float]:
    n = len(price)
    if n < 2:
        return 0.0, 0.0
    idx_1 = np.arange(n)
    weights_1 = np.exp(-((idx_1 + 1) ** 2) / (2 * sigma**2))[::-1]
    weights_1 /= np.sum(weights_1)
    g1 = float(np.sum(price * weights_1))
    price_2 = price[:-1]
    idx_2 = np.arange(n - 1)
    weights_2 = np.exp(-((idx_2 + 1) ** 2) / (2 * sigma**2))[::-1]
    weights_2 /= np.sum(weights_2)
    g2 = float(np.sum(price_2 * weights_2))
    return g1, g2


def _get_elapsed_trading_minutes(now: Optional[datetime] = None) -> int:
    """当日截止到当前时刻已发生的交易时长（分钟），自 9:30 起算并扣除午休。"""
    now = now or datetime.now()
    elapsed = (now.hour - 9) * 60 + now.minute - 30
    if now.hour >= 13:
        elapsed -= 90
    return max(1, min(elapsed, 240))


def _get_today_volume_from_spot(symbol: str, spot_df: pd.DataFrame) -> Optional[float]:
    """从 ``get_etf_spot()`` 结果中读取标的当日累计成交量。"""
    if spot_df is None or spot_df.empty or "成交量" not in spot_df.columns:
        return None
    code_col = "代码" if "代码" in spot_df.columns else None
    if code_col is None:
        return None
    code6 = normalize_symbol(symbol).split(".")[0]
    row = spot_df[spot_df[code_col].astype(str).str.zfill(6) == code6]
    if row.empty:
        return None
    vol = pd.to_numeric(row.iloc[0]["成交量"], errors="coerce")
    if pd.isna(vol) or float(vol) <= 0:
        return None
    return float(vol)


def _get_volume_ratio(
    hist_volumes: np.ndarray,
    today_vol: float,
    lookback_days: int,
    elapsed_minutes: int,
) -> Optional[float]:
    """
    成交量比 = 当日预估全日成交量 / 近 N 日平均全日成交量。

    当日预估全日成交量 = 当日累计成交量 × 240 / 已交易分钟数。
    """
    if hist_volumes is None or len(hist_volumes) < lookback_days:
        return None
    past = hist_volumes[-lookback_days:]
    if np.any(np.isnan(past)) or np.any(past == 0):
        return None
    avg_volume = float(np.mean(past))
    if avg_volume <= 0:
        return None
    projected_today_vol = today_vol * (240.0 / elapsed_minutes)
    return 100*projected_today_vol / avg_volume


def _fetch_etf_bars(
    symbol: str,
    end_date: str,
    lookback: int,
) -> pd.DataFrame:
    start_dt = pd.Timestamp(end_date) - pd.Timedelta(days=lookback * 3)
    end_ts = pd.Timestamp(end_date)
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


def score_single_etf(
    symbol: str,
    bars: pd.DataFrame,
    config: MomentumScoreConfig,
    etf_name: Optional[str] = None,
    premium_rate: Optional[float] = None,
    today_vol: Optional[float] = None,
    elapsed_minutes: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    """计算单只 ETF 的全部打分指标。"""
    min_required = int(max(config.lookback_days, config.short_momentum_lookback) * config.min_bars_ratio)
    if bars.empty or len(bars) < min_required:
        return None

    closes = bars["close"].to_numpy(dtype=float)
    volumes = bars["volume"].to_numpy(dtype=float)
    valid = (~np.isnan(volumes)) & (volumes > 0)
    hist_closes = closes[valid]
    hist_volumes = volumes[valid]
    if len(hist_closes) < min_required:
        return None

    current_price = float(hist_closes[-1])
    price_series = hist_closes

    momentum_score, annualized_returns, r_squared = calculate_momentum_score(
        price_series, config.lookback_days
    )
    if momentum_score is None:
        return None

    short_momentum_score, _, _ = calculate_momentum_score(
        price_series, config.short_momentum_lookback
    )

    passed_momentum = config.min_score_threshold <= momentum_score <= config.max_score_threshold
    passed_short_momentum = (
        short_momentum_score is not None
        and config.short_momentum_min_score <= short_momentum_score <= config.short_momentum_max_score
    )

    volume_ratio = None
    if today_vol is not None and today_vol > 0:
        elapsed = elapsed_minutes if elapsed_minutes is not None else _get_elapsed_trading_minutes()
        volume_ratio = _get_volume_ratio(
            hist_volumes, today_vol, config.volume_lookback, elapsed
        )

    day_ratios: list[float] = []
    passed_loss_filter = True
    if len(price_series) >= 4:
        day_ratios = [
            price_series[-1] / price_series[-2],
            price_series[-2] / price_series[-3],
            price_series[-3] / price_series[-4],
        ]
        if min(day_ratios) < config.loss:
            passed_loss_filter = False

    passed_premium = True
    if premium_rate is not None:
        passed_premium = premium_rate <= config.max_premium_rate

    laplace_value = laplace_slope = 0.0
    gaussian_value = gaussian_slope = 0.0
    passed_laplace = passed_gaussian = False
    if len(price_series) >= 10:
        laplace_values = laplace_filter(price_series, s=config.laplace_s_param)
        if len(laplace_values) >= 2:
            laplace_value = float(laplace_values[-1])
            laplace_slope = float(laplace_values[-1] - laplace_values[-2])
            passed_laplace = current_price > laplace_values[-1] and laplace_slope > config.laplace_min_slope
        g1, g2 = gaussian_filter_last_two(price_series, sigma=config.gaussian_sigma)
        gaussian_value = g1
        gaussian_slope = g1 - g2
        passed_gaussian = current_price > g1 and gaussian_slope > config.gaussian_min_slope

    if config.current_filter == "正常期":
        filter_value, filter_slope, passed_filter = laplace_value, laplace_slope, passed_laplace
    else:
        filter_value, filter_slope, passed_filter = gaussian_value, gaussian_slope, passed_gaussian

    return {
        "symbol": symbol,
        "etf_name": etf_name or symbol,
        "momentum_score": momentum_score,
        "short_momentum_score": short_momentum_score,
        "annualized_returns": annualized_returns,
        "r_squared": r_squared,
        "current_price": current_price,
        "volume_ratio": volume_ratio,
        "day_ratios": day_ratios,
        "premium_rate": premium_rate,
        "passed_momentum": passed_momentum,
        "passed_short_momentum": passed_short_momentum,
        "passed_r2": r_squared > config.r2_threshold,
        "passed_volume": volume_ratio is not None and volume_ratio < config.volume_threshold,
        "passed_loss": passed_loss_filter,
        "passed_premium": passed_premium,
        "laplace_value": laplace_value,
        "laplace_slope": laplace_slope,
        "gaussian_value": gaussian_value,
        "gaussian_slope": gaussian_slope,
        "passed_laplace": passed_laplace,
        "passed_gaussian": passed_gaussian,
        "filter_value": filter_value,
        "filter_slope": filter_slope,
        "passed_filter": passed_filter,
    }


def apply_filters(metrics_list: list[dict[str, Any]], config: MomentumScoreConfig) -> list[dict[str, Any]]:
    """按五福策略开关依次过滤。"""
    use_short = config.use_short_momentum_period
    steps: list[tuple[str, Any, bool]] = [
        ("passed_momentum", lambda m: m["passed_momentum"], not use_short),
        ("passed_short_momentum", lambda m: m["passed_short_momentum"], use_short),
        ("passed_r2", lambda m: m["passed_r2"], config.enable_r2_filter),
        ("passed_volume", lambda m: m["passed_volume"], config.enable_volume_check),
        ("passed_loss", lambda m: m["passed_loss"], config.enable_loss_filter),
        ("passed_premium", lambda m: m["passed_premium"], config.enable_premium_filter),
        ("passed_filter", lambda m: m["passed_filter"], config.enable_range_bound_mode),
    ]
    filtered = metrics_list[:]
    for _, cond, enabled in steps:
        if enabled:
            filtered = [m for m in filtered if cond(m)]
    return filtered


def score_etf_pool(
    etf_pool: Iterable[str],
    as_of_date: DateLike,
    config: Optional[MomentumScoreConfig] = None,
    names: Optional[dict[str, str]] = None,
    data_engine: Optional[DataEngine] = None,
    apply_filter: bool = False,
) -> pd.DataFrame:
    """
    对 ETF 池在指定日期计算动量打分。

    参数:
        etf_pool: ETF 代码列表（支持聚宽 ``518880.XSHG`` 或本地 ``518880.SH``）。
        as_of_date: 打分基准日（使用该日收盘价作为当前价）。
        config: 打分参数，默认与五福策略一致。
        names: 可选代码→名称映射。
        data_engine: 可选，复用已有 DataEngine 实例。
        apply_filter: 为 True 时仅返回通过全部过滤条件的标的。

    返回:
        DataFrame，按主排序键（动量或短期动量）降序，含 ``rank`` 列。
    """
    cfg = config or MomentumScoreConfig()
    engine = data_engine or DataEngine()
    end_date = _to_date_str(as_of_date)
    symbols = [normalize_symbol(s) for s in etf_pool]
    name_map = names or {}

    lookback = max(cfg.lookback_days, cfg.short_momentum_lookback, cfg.volume_lookback) + 20
    rows: list[dict[str, Any]] = []
    spot_df = engine.get_etf_spot()
    elapsed_minutes = _get_elapsed_trading_minutes()

    for symbol in symbols:
        bars = _fetch_etf_bars(symbol, end_date, lookback)
        today_vol = _get_today_volume_from_spot(symbol, spot_df)
        metrics = score_single_etf(
            symbol,
            bars,
            cfg,
            etf_name=name_map.get(symbol),
            today_vol=today_vol,
            elapsed_minutes=elapsed_minutes,
        )
        if metrics:
            rows.append(metrics)

    if not rows:
        return pd.DataFrame()

    score_key = "short_momentum_score" if cfg.use_short_momentum_period else "momentum_score"
    for item in rows:
        for key in ("momentum_score", "short_momentum_score"):
            val = item.get(key)
            if val is None or (isinstance(val, float) and np.isnan(val)):
                item[key] = float("-inf")

    rows.sort(key=lambda x: x.get(score_key, float("-inf")), reverse=True)

    if apply_filter:
        rows = apply_filters(rows, cfg)
        rows.sort(key=lambda x: x.get(score_key, float("-inf")), reverse=True)

    df = pd.DataFrame(rows)
    df.insert(0, "rank", range(1, len(df) + 1))
    df["as_of_date"] = end_date
    df["sort_score"] = df[score_key]
    df["all_passed"] = (
        df["passed_momentum"]
        & df["passed_short_momentum"]
        & df["passed_r2"]
        & df["passed_volume"]
        & df["passed_loss"]
        & df["passed_premium"]
        & df["passed_filter"]
    )
    return df


# 五福策略固定池（聚宽格式，便于直接传入）
DEFAULT_WUFU_ETF_POOL: list[str] = [
    "518880.XSHG", "161226.XSHE", "159980.XSHE", "501018.XSHG", "159985.XSHE",
    "513100.XSHG", "159509.XSHE", "513290.XSHG", "513500.XSHG", "159518.XSHE",
    "510300.XSHG", "510500.XSHG", "512100.XSHG", "159915.XSHE", "588080.XSHG",
    "512880.XSHG", "512480.XSHG", "515880.XSHG", "513090.XSHG", "513180.XSHG",
]


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="五福策略 ETF 动量打分")
    parser.add_argument("--date", required=True, help="打分日期 YYYY-MM-DD")
    parser.add_argument(
        "--symbols",
        nargs="*",
        default=DEFAULT_WUFU_ETF_POOL[:10],
        help="ETF 代码列表，默认取五福固定池前 10 只",
    )
    parser.add_argument("--filter", action="store_true", help="仅输出通过全部过滤条件的标的")
    parser.add_argument("--source", default=None, help="历史数据源: qmt/eastmoney/akshare/tdx/按顺序")
    args = parser.parse_args()

    cfg = MomentumScoreConfig(hist_source=args.source)
    df = score_etf_pool(args.symbols, args.date, config=cfg, apply_filter=args.filter)
    if df.empty:
        print("无有效打分结果，请检查代码与数据源。")
        return

    show_cols = [
        "rank", "symbol", "etf_name", "momentum_score", "short_momentum_score",
        "annualized_returns", "r_squared", "volume_ratio", "passed_momentum",
        "passed_r2", "passed_volume", "passed_loss", "passed_filter", "all_passed",
    ]
    print(df[show_cols].to_string(index=False))


if __name__ == "__main__":
    main()
