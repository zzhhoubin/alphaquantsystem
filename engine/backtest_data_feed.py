# alphaQuantSystem/engine/backtest_data_feed.py
"""Backtest data feed — pull mode: iter_warmup_periods() / iter_bars()"""
from __future__ import annotations
from datetime import datetime, timedelta
from typing import Dict, Iterator, List, Optional

import pandas as pd
from loguru import logger

from alphaQuantSystem.core import BarData
from alphaQuantSystem.data.data_engine import DataEngine


class BacktestDataFeed:
    """Backtest-only data feed. One instance, two iterators."""

    def __init__(self):
        self._hist_data: Dict[str, pd.DataFrame] = {}
        self._start: Optional[pd.Timestamp] = None
        self._end: Optional[pd.Timestamp] = None
        self.warmup_bars: int = 0
        self._all_symbols: List[str] = []
        self._period: str = "D"
        self._data_engine = DataEngine()
        # 分钟数据按交易日懒加载缓存：key = f"{symbol}:{trade_date}:{period}"
        self._intraday_cache: Dict[str, pd.DataFrame] = {}
        # 记录已确认不可用的 (symbol, period)，避免重复尝试
        self._intraday_unavailable: set = set()

    def subscribe(
        self,
        symbols: List[str],
        period: str = "D",
        *,
        start: str,
        end: str,
        warmup_bars: int = 0,
        source: Optional[str] = None,
    ) -> None:
        self._all_symbols = sorted(symbols)
        self._period = period
        self.warmup_bars = warmup_bars
        self._start = pd.to_datetime(start)
        self._end = pd.to_datetime(end)

        warm_start = self._start
        if warmup_bars > 0:
            warm_start = self._start - timedelta(days=warmup_bars * 3)

        for symbol in self._all_symbols:
            try:
                df = self._data_engine.get_hist_data(
                    symbol=symbol, period=period,
                    start_date=warm_start.strftime("%Y%m%d"),
                    end_date=end, source=source,
                )
                if df is not None and not df.empty:
                    self._hist_data[symbol] = self._normalize(df)
            except Exception as e:
                logger.warning(f"[BacktestDataFeed] {symbol} load failed: {e}")

    @staticmethod
    def _normalize(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        if "date" in out.columns:
            out["date"] = pd.to_datetime(out["date"], errors="coerce")
            out = out.dropna(subset=["date"]).sort_values("date").drop_duplicates(subset=["date"], keep="last")
            out = out.set_index("date")
        elif not isinstance(out.index, pd.DatetimeIndex):
            out.index = pd.to_datetime(out.index, errors="coerce")
        out.index = pd.to_datetime(out.index).normalize()
        out = out[~out.index.duplicated(keep="last")].sort_index()
        return out

    @staticmethod
    def _normalize_minute(df: pd.DataFrame) -> pd.DataFrame:
        """Normalize minute data: keep time-of-day (no .normalize())."""
        out = df.copy()
        if "date" in out.columns:
            out["date"] = pd.to_datetime(out["date"], errors="coerce")
            out = out.dropna(subset=["date"]).sort_values("date").drop_duplicates(subset=["date"], keep="last")
            out = out.set_index("date")
        elif not isinstance(out.index, pd.DatetimeIndex):
            out.index = pd.to_datetime(out.index, errors="coerce")
        # 分钟线保留到分钟，不做 .normalize()
        out = out[~out.index.duplicated(keep="last")].sort_index()
        return out

    def _build_date_range(self, start: pd.Timestamp, end: pd.Timestamp) -> List[pd.Timestamp]:
        all_dates = set()
        for frame in self._hist_data.values():
            all_dates.update(frame.index)
        return sorted(d for d in all_dates if start <= pd.Timestamp(d).normalize() <= end)

    def _row_to_bar(self, symbol: str, row, dt: pd.Timestamp) -> BarData:
        if isinstance(row, pd.DataFrame):
            row = row.iloc[-1]
        return BarData(
            symbol=symbol,
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
            amount=float(row.get("amount", 0.0)),
            event_time=dt.to_pydatetime(),
            interval=self._period,
        )

    def iter_warmup_periods(self) -> Iterator[List[BarData]]:
        """Yield warmup periods (each group = bars for all symbols at same time, sorted alphabetically)"""
        if self.warmup_bars <= 0 or self._start is None:
            return
        warm_end = self._start - timedelta(days=1)
        warm_dates = []
        for dt in self._build_date_range(pd.Timestamp("2000-01-01"), warm_end):
            warm_dates.append(pd.Timestamp(dt).normalize())
        if not warm_dates:
            return
        warm_dates = warm_dates[-self.warmup_bars:]
        for dt in warm_dates:
            bars = []
            for symbol in self._all_symbols:
                df = self._hist_data.get(symbol)
                if df is not None and dt in df.index:
                    bars.append(self._row_to_bar(symbol, df.loc[dt], dt))
            if bars:
                yield bars

    def iter_bars(self) -> Iterator[BarData]:
        """Yield formal period bars ([start, end], sorted by time, then alphabetically by symbol)"""
        if self._start is None or self._end is None:
            return
        for dt in self._build_date_range(self._start, self._end):
            for symbol in self._all_symbols:
                df = self._hist_data.get(symbol)
                if df is not None and dt in df.index:
                    yield self._row_to_bar(symbol, df.loc[dt], dt)

    # ── 分钟/日内数据（双周期风控用）──

    def _load_intraday(self, symbol: str, trade_date: pd.Timestamp, period: str) -> Optional[pd.DataFrame]:
        """按交易日懒加载单个标的的分钟数据，缓存供当日复用。

        若某 (symbol, period) 首次加载失败（数据源不支持），后续交易日直接跳过，
        避免每个交易日都重试全部数据源。
        """
        unavailable_key = (symbol, period)
        if unavailable_key in self._intraday_unavailable:
            return None
        cache_key = f"{symbol}:{trade_date.strftime('%Y%m%d')}:{period}"
        if cache_key in self._intraday_cache:
            return self._intraday_cache[cache_key]
        try:
            df = self._data_engine.get_hist_data(
                symbol=symbol, period=period,
                start_date=trade_date.strftime("%Y%m%d"),
                end_date=trade_date.strftime("%Y%m%d"),
            )
            if df is not None and not df.empty:
                df = self._normalize_minute(df)
                self._intraday_cache[cache_key] = df
                return df
            else:
                # 数据源返回空：标记不可用，只告警一次
                self._intraday_unavailable.add(unavailable_key)
                logger.warning(
                    f"[BacktestDataFeed] 分钟数据不可用: {symbol} period={period}，"
                    f"日内风控将跳过；策略周期风控正常执行"
                )
        except Exception as e:
            self._intraday_unavailable.add(unavailable_key)
            logger.warning(f"[BacktestDataFeed] intraday {symbol} {period} load failed: {e}")
        return None

    def iter_intraday_bars(
        self, symbol: str, trade_date: pd.Timestamp, period: str,
    ) -> Iterator[BarData]:
        """Yield intraday bars for a single symbol on a single trade date.

        按交易日懒加载分钟数据，当日数据缓存在内存中（单日约 240 行/标的），
        避免全量预加载导致内存膨胀。调用方应在交易日结束后 clear_intraday_cache()。
        """
        df = self._load_intraday(symbol, trade_date, period)
        if df is None:
            return
        for idx, row in df.iterrows():
            dt = idx if isinstance(idx, pd.Timestamp) else pd.Timestamp(idx)
            yield BarData(
                symbol=symbol,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
                amount=float(row.get("amount", 0.0)),
                event_time=dt.to_pydatetime(),
                interval=period,
            )

    def clear_intraday_cache(self) -> None:
        """清除日内数据缓存（每个交易日结束后调用，释放内存）。"""
        self._intraday_cache.clear()
