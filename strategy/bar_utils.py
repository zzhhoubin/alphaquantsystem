"""
K 线 DataFrame 行与 BarData 的互转、收盘价序列预热等策略层共用工具。
"""
from __future__ import annotations
from collections import deque
from datetime import datetime
from typing import Deque, Optional, Union
import pandas as pd
from alphaQuantSystem.core import BarData

def bar_from_ohlcv_row(row: Union[pd.Series, object], symbol: str, interval: str) -> BarData:
    """
    将 pandas 单行（Series，通常为 df.iloc[-1]）转为 BarData。
    兼容列名 open/high/low/close/volume/amount；索引为 datetime 时写入 event_time。
    """
    close = float(row['close'])
    open_v = float(row.get('open', close))
    high_v = float(row.get('high', close))
    low_v = float(row.get('low', close))
    vol = float(row.get('volume', 0))
    amt = float(row.get('amount', 0))
    idx = getattr(row, 'name', None)
    event_time = idx if isinstance(idx, datetime) else datetime.now()
    return BarData(symbol=symbol, open=open_v, high=high_v, low=low_v, close=close, volume=vol, amount=amt, event_time=event_time, interval=interval)

def bar_dedupe_key_from_row(row: Union[pd.Series, object], close: Optional[float]=None) -> tuple:
    """
    用于轮询场景：判断是否与上一根为同一根 K（时间戳 + 收盘价）。
    """
    c = float(close) if close is not None else float(row['close'])
    if hasattr(row, 'get'):
        t = row.get('date', row.name)
    else:
        t = getattr(row, 'name', None)
    return (str(t), c)

def append_close_tail_to_deque(df: Optional[pd.DataFrame], dq: Deque[float], *, tail_bars: int) -> int:
    """
    将 df 中最后 tail_bars 条收盘价依次追加到 deque（不清空 deque）。
    返回实际追加条数；df 无效时返回 0。
    """
    if df is None or df.empty or 'close' not in df.columns or (tail_bars <= 0):
        return 0
    closes = df['close'].astype(float).tolist()[-tail_bars:]
    for px in closes:
        dq.append(float(px))
    return len(closes)
