"""
通用工具函数
"""
import math
import time
from datetime import datetime

# A 股 ETF/股票柜台单笔申报上限（股），与 risk_limits.per_symbol_max_qty 默认一致
MAX_SINGLE_ORDER_QTY = 1_000_000

def adjust_symbol(symbol: str) -> str:
    """统一代码格式为 XXXXXX.SH / XXXXXX.SZ"""
    s = symbol.strip()
    if s[-3:] in ('.SH', '.SZ', '.sh', '.sz'):
        return s.upper()
    code = s[:6]
    # 沪市基金/ETF 段统一走 50/51/52/56/58 前缀（覆盖 588/560/561/562/563/520... 等）。
    # A 股不存在 5 开头的股票代码、深市 ETF 全是 159xxx，故 5xxxxx → .SH 不会冲突。
    if (
        code[:3] in ['600', '601', '603', '605', '688', '113', '110', '118', '501']
        or code[:2] in ('11', '50', '51', '52', '56', '58')
    ):
        return code + '.SH'
    return code + '.SZ'

def round_volume(symbol: str, volume: float) -> int:
    """按品种取整手数（债券10手，股票/ETF 100股）"""
    code = symbol[:6]
    if code[:3] in ['110', '113', '123', '127', '128', '111'] or code[:2] in ['11', '12']:
        return int(math.floor(volume / 10) * 10)
    return int(math.floor(volume / 100) * 100)


def split_order_volumes(
    symbol: str,
    volume: float,
    max_qty: int = MAX_SINGLE_ORDER_QTY,
) -> list[int]:
    """将委托量拆成多笔，每笔不超过单笔上限且为整手。"""
    total = round_volume(symbol, volume)
    if total <= 0:
        return []
    if max_qty <= 0:
        return [total]
    cap = round_volume(symbol, max_qty)
    if cap <= 0:
        cap = max_qty
    chunks: list[int] = []
    remaining = total
    while remaining > 0:
        chunk = round_volume(symbol, min(remaining, cap))
        if chunk <= 0:
            break
        chunks.append(chunk)
        remaining -= chunk
    return chunks

def select_asset_type(symbol: str) -> str:
    """判断标的类型：bond / fund / stock"""
    code = symbol[:6]
    if code[:3] in ['110', '113', '123', '127', '128', '111', '118']:
        return 'bond'
    if code[:3] in ['510', '511', '512', '513', '514', '515', '516', '517', '518', '588', '159', '501', '164']:
        return 'fund'
    return 'stock'

def is_trading_time() -> bool:
    """判断当前是否在 A 股交易时段"""
    now = datetime.now().time()
    from datetime import time as dtime
    morning = dtime(9, 30) <= now <= dtime(12, 30)
    afternoon = dtime(13, 0) <= now <= dtime(15, 0)
    return morning or afternoon

def is_trading_day() -> bool:
    """简单判断是否为工作日（不含节假日）"""
    return datetime.now().weekday() < 5

def conv_timestamp(ct: int) -> str:
    """毫秒时间戳转字符串"""
    local_time = time.localtime(ct / 1000)
    return time.strftime('%Y%m%d%H%M%S', local_time)
