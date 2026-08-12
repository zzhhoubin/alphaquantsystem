"""
日线 close 拼接 — 用指定时刻的 1 分钟 bar close 替换日线 close

场景：回测/分析时希望日线收盘价反映盘中某一时刻（如 13:10）的价格，
而非日终收盘价；开高低成交量等其他字段保持不变。

用法::

    from alphaQuantSystem.examples.daily_close_splice import splice_daily_close

    df = splice_daily_close(
        "159509.SZ",
        start_date="20260601",
        end_date="20260610",
        minute_time="13:10",
    )
    print(df[["date", "close", "close_original"]])
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, time
from typing import Optional, Union

import pandas as pd

from alphaQuantSystem.data import DataEngine

DateLike = Union[str, date, datetime]


def _to_yyyymmdd(value: DateLike) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y%m%d")
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    s = str(value).strip().replace("-", "").replace("/", "")
    if len(s) != 8 or not s.isdigit():
        raise ValueError(f"无法解析日期: {value!r}，请使用 YYYY-MM-DD 或 YYYYMMDD")
    return s


def _parse_minute_time(value: str) -> time:
    """解析 ``13:10`` / ``1310`` / ``13:10:00`` 为 time 对象。"""
    s = str(value).strip()
    if ":" in s:
        parts = s.split(":")
        if len(parts) == 2:
            h, m = int(parts[0]), int(parts[1])
            return time(h, m)
        if len(parts) == 3:
            h, m, sec = int(parts[0]), int(parts[1]), int(parts[2])
            return time(h, m, sec)
    if len(s) == 4 and s.isdigit():
        return time(int(s[:2]), int(s[2:]))
    raise ValueError(f"无法解析分钟时刻: {value!r}，请使用 HH:MM 或 HHMM")


def _normalize_daily(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out = out.dropna(subset=["date"]).sort_values("date")
    out = out.drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
    out["_trade_date"] = out["date"].dt.normalize()
    return out


def _normalize_minute(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out = out.dropna(subset=["date"]).sort_values("date")
    out = out.drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
    out["_trade_date"] = out["date"].dt.normalize()
    out["_bar_time"] = out["date"].dt.time
    return out


def _pick_minute_close(
    minute_df: pd.DataFrame,
    trade_date: pd.Timestamp,
    target_time: time,
) -> Optional[float]:
    """取指定交易日、指定时刻的 1 分钟 bar close；无精确匹配时返回 None。"""
    day_bars = minute_df[minute_df["_trade_date"] == trade_date]
    if day_bars.empty:
        return None
    matched = day_bars[day_bars["_bar_time"] == target_time]
    if matched.empty:
        return None
    val = pd.to_numeric(matched["close"].iloc[-1], errors="coerce")
    if pd.isna(val):
        return None
    return float(val)


def splice_daily_close(
    symbol: str,
    start_date: DateLike,
    end_date: DateLike,
    minute_time: str = "13:10",
    *,
    source: Optional[str] = None,
    engine: Optional[DataEngine] = None,
) -> pd.DataFrame:
    """
    将日线 close 替换为当日指定分钟 bar 的 close，其余 OHLCV 不变。

    参数:
        symbol: 标的代码，如 ``159509.SZ``。
        start_date / end_date: 日期范围 YYYYMMDD 或 YYYY-MM-DD。
        minute_time: 分钟时刻，如 ``13:10``。
        source: 透传给 ``DataEngine.get_hist_data`` 的数据源。
        engine: 可选，复用已有 DataEngine 实例。

    返回:
        日线 DataFrame，新增列:
        - ``close_original``: 原始日线 close
        - ``close_spliced``: 是否成功替换
        - ``minute_bar_time``: 实际使用的分钟 bar 时间
    """
    start = _to_yyyymmdd(start_date)
    end = _to_yyyymmdd(end_date)
    target_time = _parse_minute_time(minute_time)
    data_engine = engine or DataEngine()

    daily_raw = data_engine.get_hist_data(
        symbol=symbol,
        period="d",
        start_date=start,
        end_date=end,
        source=source,
    )
    if daily_raw is None or daily_raw.empty:
        return pd.DataFrame()

    minute_raw = data_engine.get_hist_data(
        symbol=symbol,
        period="1m",
        start_date=start,
        end_date=end,
        source=source,
    )
    if minute_raw is None or minute_raw.empty:
        raise RuntimeError(
            f"无法获取 {symbol} 的 1 分钟数据 ({start}~{end})，无法拼接 close"
        )

    daily = _normalize_daily(daily_raw)
    minute = _normalize_minute(minute_raw)

    out = daily.copy()
    out["close_original"] = pd.to_numeric(out["close"], errors="coerce")
    out["close_spliced"] = False
    out["minute_bar_time"] = pd.NaT

    for i, row in out.iterrows():
        trade_date = row["_trade_date"]
        minute_close = _pick_minute_close(minute, trade_date, target_time)
        if minute_close is None:
            continue
        out.at[i, "close"] = minute_close
        out.at[i, "close_spliced"] = True
        out.at[i, "minute_bar_time"] = pd.Timestamp.combine(
            trade_date.date(), target_time
        )

    out = out.drop(columns=["_trade_date"])
    return out


def _demo() -> None:
    symbol = "159509.SZ"
    trade_date = "20260209"
    minute_time = "13:10"

    engine = DataEngine()
    print(trade_date)
    daily = engine.get_hist_data(symbol, period="d", start_date=trade_date, end_date=trade_date)
    minute = engine.get_hist_data(symbol, period="1m", start_date=trade_date, end_date=trade_date)

    print(f"=== {symbol} {trade_date} 日线 close 拼接示例 ({minute_time}) ===\n")

    if daily is not None and not daily.empty:
        orig_close = float(daily["close"].iloc[-1])
        print(f"原始日线 close: {orig_close:.4f}")
    else:
        print("原始日线数据为空")
        orig_close = None

    if minute is not None and not minute.empty:
        m = _normalize_minute(minute)
        target = _parse_minute_time(minute_time)
        td = pd.Timestamp(trade_date)
        mc = _pick_minute_close(m, td, target)
        if mc is not None:
            print(f"{minute_time} 分钟 bar close: {mc:.4f}")
        else:
            print(f"未找到 {minute_time} 分钟 bar")
    else:
        print("1 分钟数据为空")
        mc = None

    spliced = splice_daily_close(
        symbol,
        start_date=trade_date,
        end_date=trade_date,
        minute_time=minute_time,
        engine=engine,
    )
    if spliced.empty:
        print("\n拼接结果为空")
        return

    row = spliced.iloc[-1]
    print(f"\n拼接后 close: {row['close']:.4f}")
    print(f"是否替换: {row['close_spliced']}")
    if orig_close is not None and mc is not None:
        print(f"\n验证: 日线 {orig_close:.4f} -> 分钟 {mc:.4f} -> 拼接 {row['close']:.4f}")


def main() -> None:
    parser = argparse.ArgumentParser(description="日线 close 用指定分钟 bar close 替换")
    parser.add_argument("symbol", nargs="?", default="159509.SZ", help="标的代码")
    parser.add_argument("--start", default="20260209", help="起始日期 YYYYMMDD")
    parser.add_argument("--end", default="20260209", help="结束日期 YYYYMMDD")
    parser.add_argument("--time", default="13:10", dest="minute_time", help="分钟时刻 HH:MM")
    parser.add_argument("--source", default=None, help="数据源，如 qmt / eastmoney")
    parser.add_argument("--demo", action="store_true", help="运行内置示例并打印对比")
    args = parser.parse_args()

    if args.demo:
        _demo()
        return

    df = splice_daily_close(
        args.symbol,
        start_date=args.start,
        end_date=args.end,
        minute_time=args.minute_time,
        source=args.source,
    )
    cols = [c for c in ["date", "open", "high", "low", "close", "close_original", "close_spliced", "volume"]
            if c in df.columns]
    print(df[cols].to_string(index=False))


if __name__ == "__main__":
    main()
