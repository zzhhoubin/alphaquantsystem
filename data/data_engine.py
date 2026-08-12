"""
数据引擎 —— 保留原有东财/QMT/TDX 数据获取逻辑，统一接口
对外接口：get_hist_data() / get_realtime() / get_etf_spot() / get_etf_premium_rate() / get_jisilu_funds()
"""
import json
import math
import time
from datetime import datetime, timedelta
from typing import Callable, List, Optional, Union
import pandas as pd
import requests
from loguru import logger
from .hist_source import HIST_SOURCE_DISPLAY, resolve_hist_source_chain
from .jisilu import fetch_jisilu_funds
from alphaQuantSystem.utils.helpers import adjust_symbol as _adjust_symbol

# ETF 全推接口的 CDN 主机列表：东财 push2 不同子域名指向不同 CDN 节点，
# 单域名出现 502 / 风控时，轮换其它 host 多数仍可命中。
_ETF_EM_HOSTS = (
    '88.push2.eastmoney.com',
    'push2.eastmoney.com',
    '19.push2.eastmoney.com',
    '76.push2.eastmoney.com',
)
# 浏览器化请求头，规避 push2 服务端对无头爬虫的初级风控。
_ETF_EM_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Referer': 'https://quote.eastmoney.com/center/gridlist.html',
    'Accept': 'application/json, text/plain, */*',
}
# get_etf_spot 统一对外输出列顺序；任何源都需对齐到该集合。
_ETF_SPOT_COLUMNS = [
    '代码', '名称', '最新价', '涨跌额', '涨跌幅',
    '成交量', '成交额', '开盘价', '最高价', '最低价',
    '换手率', '量比',
]
_ETF_SPOT_NUMERIC_COLUMNS = [
    '最新价', '涨跌额', '涨跌幅', '成交量', '成交额',
    '开盘价', '最高价', '最低价', '换手率', '量比',
]
# 同花顺 fund_etf_spot_ths：日终净值字段（与查询日期 trade_date 对齐）
_THS_ETF_NAV_COL = '当前-单位净值'

try:
    import akshare as ak

    _HAS_AK = True
except ImportError:
    _HAS_AK = False
try:
    from xtquant import xtdata as _xtdata

    _HAS_XTQ = True
    _xtdata.enable_hello = False
except ImportError:
    _HAS_XTQ = False
try:
    from mootdx.quotes import Quotes as _MootdxQuotes

    _HAS_TDX = True
except ImportError:
    _MootdxQuotes = None  # type: ignore[misc, assignment]
    _HAS_TDX = False

# mootdx / 通达信 K 线 frequency：0=5分 1=15分 2=30分 3=60分 4=日 5=周 6=月 7/8=1分
_MOOTDX_FREQ = {'1': 8, '5': 0, '15': 1, '30': 2, '60': 3, 'D': 4, 'W': 5, 'M': 6}


def _safe_float(value, default: float = 0.0) -> float:
    """
    职责:
        将任意值安全转为 float，None / 解析失败时返回 default。

    场景:
        QMT 全推 tick / 合约信息字段可能为 None 或非数值占位，统一兜底。
    """
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# QMT 字段在"未填充"时的占位值通常是 sys.float_info.max（约 1.79e308），
# 例如 PreClose / LongMarginRatio 等可能直接返回该值；这些必须按"无效"处理，
# 否则会污染涨跌幅计算。统一价格合理上界设为 1e6，量额合理上界设为 1e15。
_QMT_PRICE_MAX = 1e6
_QMT_VOLUME_MAX = 1e15


def _safe_price(value) -> float:
    """
    职责:
        将价格类字段安全转 float；越界 / 异常返回 ``float('nan')``。

    合理区间:
        ``0 < x < _QMT_PRICE_MAX``，用于过滤 QMT 的「无穷大占位值」。
    """
    if value is None:
        return float('nan')
    try:
        x = float(value)
    except (TypeError, ValueError):
        return float('nan')
    if not (0 < x < _QMT_PRICE_MAX):
        return float('nan')
    return x


def _safe_nonneg(value) -> float:
    """
    职责:
        将量 / 额类非负字段安全转 float；越界 / 异常返回 ``float('nan')``。

    合理区间:
        ``0 <= x < _QMT_VOLUME_MAX``。
    """
    if value is None:
        return float('nan')
    try:
        x = float(value)
    except (TypeError, ValueError):
        return float('nan')
    if not (0 <= x < _QMT_VOLUME_MAX):
        return float('nan')
    return x


_PERIOD_MAP_EF = {'1': '1', '5': '5', '15': '15', '30': '30', '60': '60', 'D': '101', 'W': '102', 'M': '103'}
_PERIOD_ALIAS = {'1m': '1', '1': '1', '5m': '5', '5': '5', '15m': '15', '15': '15', '30m': '30', '30': '30',
                 '60m': '60', '60': '60', '1h': '60', '1d': 'D', 'd': 'D', 'daily': 'D', 'D': 'D', 'W': 'W', 'M': 'M'}


# 东财实时行情 fltt=1 模式下，价格字段为「真实价 × 10**小数位数」的整数。
# 优先以接口返回的 f59（小数位数）为准；缺失时按品种前缀白名单兜底，
# 覆盖：ETF/LOF(沪 5xx 段、深 159/161/162)、可转债(110/113/118/123/127/128)。
_PRICE_3DP_PREFIX2 = {'51', '52', '56', '58', '15', '16'}
_PRICE_3DP_PREFIX3 = {'110', '113', '118', '123', '127', '128'}


def _resolve_price_divisor(symbol6: str, data: Optional[dict] = None) -> int:
    """根据东财 f59 或代码前缀返回价格还原除数（fltt=1 模式下使用）。"""
    if data is not None:
        f59 = data.get('f59')
        try:
            if f59 is not None:
                n = int(f59)
                if 0 <= n <= 6:
                    return 10 ** n
        except (TypeError, ValueError):
            pass
    if symbol6[:2] in _PRICE_3DP_PREFIX2 or symbol6[:3] in _PRICE_3DP_PREFIX3:
        return 1000
    return 100


def _symbol6_list(symbols: Optional[Union[str, List[str]]]) -> Optional[List[str]]:
    """将单码或列表规范为 6 位数字代码；None 表示不筛选。"""
    if symbols is None:
        return None
    if isinstance(symbols, str):
        symbols = [symbols]
    out: List[str] = []
    for s in symbols:
        code = str(s).strip().split('.')[0][:6]
        if code.isdigit():
            out.append(code.zfill(6))
    return out or None


def _yyyymmdd_str(d: Optional[Union[str, datetime]]) -> Optional[str]:
    if d is None:
        return None
    if isinstance(d, datetime):
        return d.strftime('%Y%m%d')
    s = str(d).replace('-', '').strip()[:8]
    return s if len(s) == 8 and s.isdigit() else None


def _sanitize_fund_nav_value(value) -> float:
    """东财未披露净值时常为 '---' / 空串，转为 NaN。"""
    if value is None:
        return float('nan')
    if isinstance(value, str):
        s = value.strip()
        if s in ('', '---', '--', '-', 'nan', 'NaN'):
            return float('nan')
    try:
        x = float(value)
    except (TypeError, ValueError):
        return float('nan')
    return x if x > 0 else float('nan')


def _norm_period(period: str) -> str:
    """
    职责:
        归一化周期别名到内部标准周期编码。

    参数:
        period (str): 原始周期字符串。

    返回:
        str: 归一化后的周期编码。

    异常:
        无显式抛出；未识别值原样返回。

    调用关系:
        - 由 DataEngine.get_hist_data() 调用。
    """
    return _PERIOD_ALIAS.get(period, period)


class DataEngine:
    """
    统一数据引擎。

    历史 K 线默认（未传 source）：东财 → QMT → AkShare → TDX。
    可选 hist_data_source：按顺序(QMT 起)、或单一源并环形降级等，见 get_hist_data。
    """

    _TDX_PAGE_SIZE = 800

    def __init__(self):
        """
        职责:
            初始化数据引擎及可选 mootdx（通达信）行情客户端。

        参数:
            无

        返回:
            None

        异常:
            无显式抛出；第三方依赖不可用时降级为空。

        调用关系:
            - 由策略、回测与应用装配层创建并复用。
        """
        self._tdx = None  # 懒加载 mootdx 客户端；单测可注入 mock

    def _get_mootdx_client(self):
        """懒加载 mootdx Quotes 客户端；失败或未安装时返回 None。"""
        if self._tdx is not None:
            return self._tdx
        if not _HAS_TDX:
            return None
        try:
            self._tdx = _MootdxQuotes.factory(market='std')
            return self._tdx
        except Exception as e:
            logger.debug('mootdx 客户端初始化失败: {}', e)
            return None

    def get_hist_data(self, symbol: str, period: str = 'D', start_date: str = '20200101', end_date: str = '20500101',
                      count: int = 8000, source: Optional[str] = None) -> pd.DataFrame:
        """
        职责:
            按数据来源配置拉取历史 K 线并统一为标准字段输出。

        参数:
            symbol (str): 标的代码。
            period (str): 周期。
            start_date (str): 起始日期 YYYYMMDD。
            end_date (str): 结束日期 YYYYMMDD。
            count (int): 数据条数上限（部分数据源使用）。
            source (Optional[str]): 数据来源；不传为旧版链路（东财→QMT→AkShare→TDX）。
                可选 ``按顺序``/``sequential``（QMT 起）、或 ``qmt``/``eastmoney``/``akshare``/``tdx``
                （从该源起环形尝试其余源）。

        返回:
            pd.DataFrame: 标准化历史数据，失败时返回空 DataFrame。

        异常:
            无显式抛出；非法 source 记日志并返回空表。

        调用关系:
            - 由 BaseStrategy.get_hist_data() 与 BacktestEngine.load_symbol_frame() 调用。
        """
        period = _norm_period(period)
        parsed = _symbol6_list(symbol)
        if not parsed:
            logger.error('无效证券代码 symbol={}', symbol)
            return pd.DataFrame()
        symbol6 = parsed[0]
        sym_disp = _adjust_symbol(symbol6)
        try:
            chain = resolve_hist_source_chain(source)
        except ValueError as e:
            logger.error('{}', e)
            return pd.DataFrame()
        for key in chain:
            df_valid = self._hist_attempt_source(key, symbol, symbol6, period, start_date, end_date, count)
            if df_valid is not None and (not df_valid.empty):
                label = HIST_SOURCE_DISPLAY.get(key, key)
                # logger.info('成功通过{}获取数据 symbol={} period={}', label, sym_disp, period)
                return df_valid
        logger.error('所有数据源均获取不到数据 symbol={} period={}', sym_disp, period)
        return pd.DataFrame()

    def _hist_attempt_source(self, key: str, symbol: str, symbol6: str, period: str, start_date: str, end_date: str,
                             count: int) -> Optional[pd.DataFrame]:
        """尝试单个数据源，校验通过后返回 DataFrame，否则 None。"""
        display = HIST_SOURCE_DISPLAY.get(key, key)
        df: Optional[pd.DataFrame] = None
        if key == 'eastmoney':
            df = self._fetch_ef(symbol6, period, start_date, end_date)
        elif key == 'qmt':
            if not _HAS_XTQ:
                return None
            df = self._fetch_qmt(symbol, period, start_date, end_date, count)
        elif key == 'akshare':
            if not _HAS_AK:
                return None
            df = self._fetch_akshare(symbol6, period, start_date, end_date)
        elif key == 'tdx':
            if not _HAS_TDX:
                return None
            df = self._fetch_tdx(symbol6, period, start_date, end_date, count)
        else:
            return None
        if df is None or df.empty:
            return None
        return self._normalize_and_check_hist_df(df=df, period=period, start_date=start_date, end_date=end_date,
                                                 source_name=display)

    def _fetch_ef(self, symbol6: str, period: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """
        职责:
            从东方财富接口获取历史行情并标准化字段。

        参数:
            symbol6 (str): 六位证券代码。
            period (str): 周期编码。
            start_date (str): 起始日期。
            end_date (str): 结束日期。

        返回:
            Optional[pd.DataFrame]: 成功返回数据表，失败返回 None。

        异常:
            内部捕获所有抓取异常并继续尝试市场前缀切换。

        调用关系:
            - 由 get_hist_data() 作为第一优先级数据源调用。
        """
        klt = _PERIOD_MAP_EF.get(period)
        if klt is None:
            return None
        for market in (0, 1):
            try:
                secid = f'{market}.{symbol6}'
                params = {'fields1': 'f1,f2,f3,f4,f5,f6', 'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61',
                          'beg': start_date, 'end': end_date, 'ut': 'fa5fd1943c7b386f172d6893dbfba10b',
                          'rtntype': end_date, 'secid': secid, 'klt': klt, 'fqt': '1', 'cb': 'jsonp1668432946680'}
                res = requests.get('http://push2his.eastmoney.com/api/qt/stock/kline/get?', params=params, timeout=10)
                text = res.text[19:-2]
                klines = json.loads(text)['data']['klines']
                rows = [k.split(',') for k in klines]
                cols = ['date', 'open', 'close', 'high', 'low', 'volume', 'amount', '振幅', '涨跌幅', '涨跌额',
                        '换手率']
                df = pd.DataFrame(rows, columns=cols)
                for c in cols[1:]:
                    df[c] = pd.to_numeric(df[c], errors='coerce')
                df['stock_code'] = _adjust_symbol(symbol6)
                return df
            except Exception:
                continue
        return None

    def _fetch_qmt(self, symbol: str, period: str, start_date: str, end_date: str, count: int = -1) -> Optional[
        pd.DataFrame]:
        """
        职责:
            使用 xtquant(QMT) 拉取历史行情并输出标准 DataFrame。

        参数:
            symbol (str): 标的代码。
            period (str): 周期编码。
            start_date (str): 起始日期。
            end_date (str): 结束日期。
            count (int): 条数限制，-1 表示尽量全量。

        返回:
            Optional[pd.DataFrame]: 成功返回数据表，失败返回 None。

        异常:
            内部捕获异常并记录 debug 日志。

        调用关系:
            - 由 get_hist_data() 在东财失败后、AkShare 之前调用。
        """
        _period_qmt = {'D': '1d', '1': '1m', '5': '5m', '15': '15m', '30': '30m', '60': '1h', 'W': '1w', 'M': '1mon'}
        qmt_period = _period_qmt.get(period, '1d')
        code = _adjust_symbol(symbol)
        try:
            _xtdata.download_history_data(stock_code=code, period=qmt_period, start_time=start_date, end_time=end_date,
                                          incrementally=False)
            raw = _xtdata.get_market_data_ex(stock_list=[code], period=qmt_period, start_time=start_date,
                                             end_time=end_date, count=count, dividend_type='front', fill_data=False)
            df = raw[code].reset_index().rename(columns={'index': 'date'})
            df['stock_code'] = code
            df['amount'] = df.get('amount', 0)
            return df[['date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'stock_code']]
        except Exception as e:
            logger.debug(f'QMT 获取数据失败: {e}')
            return None

    @staticmethod
    def _is_akshare_empty_error(exc: Exception) -> bool:
        """akshare 在无数据/无效代码时抛出的可忽略错误。"""
        if isinstance(exc, KeyError):
            key = exc.args[0] if exc.args else str(exc)
            return key in ('date', '日期')
        if isinstance(exc, ValueError):
            msg = str(exc)
            if 'Length mismatch' in msg and '0 elements' in msg:
                return True
            if 'not available' in msg.lower():
                return True
        return False

    @staticmethod
    def _is_akshare_retryable(exc: Exception) -> bool:
        if isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout,
                            requests.exceptions.ChunkedEncodingError)):
            return True
        msg = str(exc).lower()
        return any(k in msg for k in ('connection', 'remote', 'timeout', 'disconnected', 'reset'))

    def _format_akshare_df(self, raw_df: Optional[pd.DataFrame], symbol_std: str) -> Optional[pd.DataFrame]:
        if raw_df is None or raw_df.empty:
            return None
        df = raw_df.copy()
        if 'date' not in df.columns and '日期' not in df.columns and '时间' not in df.columns:
            df = df.reset_index()
            if 'date' not in df.columns and 'index' in df.columns:
                df = df.rename(columns={'index': 'date'})

        def _canon_col(col) -> str:
            s = str(col).strip()
            s = s.replace(' ', '').replace('\u3000', '')
            s = s.replace('(', '').replace(')', '').replace('（', '').replace('）', '')
            return s.lower()

        alias_map = {'index': 'date', 'date': 'date', 'datetime': 'date', 'time': 'date', '日期': 'date',
                     '时间': 'date', '交易日期': 'date', '开盘': 'open', 'open': 'open', '收盘': 'close',
                     'close': 'close', '最高': 'high', 'high': 'high', '最低': 'low', 'low': 'low',
                     '成交量': 'volume', '成交量手': 'volume', 'volume': 'volume', 'vol': 'volume',
                     '成交额': 'amount', '成交额元': 'amount', 'amount': 'amount', 'turnover': 'amount'}
        rename_map = {}
        for col in df.columns:
            canon = _canon_col(col)
            mapped = alias_map.get(canon)
            if mapped:
                rename_map[col] = mapped
        df = df.rename(columns=rename_map)
        if 'date' not in df.columns and len(df.columns) > 0:
            df = df.rename(columns={df.columns[0]: 'date'})
        if 'volume' not in df.columns and 'amount' in df.columns:
            df['volume'] = df['amount']
        required_cols = ['date', 'open', 'high', 'low', 'close', 'volume']
        if not all(col in df.columns for col in required_cols):
            logger.debug(
                'akshare 数据列不匹配，格式化失败: raw_cols={}, renamed_cols={}',
                list(raw_df.columns), list(df.columns))
            return None
        if 'amount' not in df.columns:
            df['amount'] = 0
        for col in ['open', 'high', 'low', 'close', 'volume', 'amount']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df['stock_code'] = symbol_std
        return df[['date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'stock_code']]

    def _call_akshare_api(
        self,
        label: str,
        fetcher: Callable[[], pd.DataFrame],
        symbol_std: str,
        *,
        retries: int = 1,
    ) -> Optional[pd.DataFrame]:
        """调用 akshare 接口并格式化；空数据/网络错误降级为 None。"""
        last_exc: Optional[Exception] = None
        for attempt in range(max(retries, 1)):
            try:
                formatted = self._format_akshare_df(fetcher(), symbol_std)
                if formatted is not None and not formatted.empty:
                    return formatted
                return None
            except Exception as e:
                last_exc = e
                if self._is_akshare_empty_error(e):
                    logger.debug('akshare {} 无数据: {}', label, e)
                    return None
                if attempt < retries - 1 and self._is_akshare_retryable(e):
                    time.sleep(0.6 * (attempt + 1))
                    continue
                break
        if last_exc is not None:
            logger.debug('akshare {} 失败: {}', label, last_exc)
        return None

    @staticmethod
    def _is_etf_symbol6(symbol6: str) -> bool:
        """ETF / 场内基金代码段判定：沪市 5xx 段、深市 159 段、科创 588 段等。"""
        if not symbol6 or len(symbol6) < 6:
            return False
        prefix2 = symbol6[:2]
        return prefix2 in ('15', '51', '52', '56', '58')

    def _fetch_akshare(self, symbol6: str, period: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """
        职责:
            使用 akshare 拉取历史行情并映射为统一字段。

        日线尝试顺序:
            - ETF / 场内基金: ``fund_etf_hist_em`` → 腾讯 ``stock_zh_a_hist_tx``
            - 普通 A 股: 东财 ``stock_zh_a_hist`` → 腾讯 ``stock_zh_a_hist_tx`` →
              新浪 ``stock_zh_a_daily``（不复权→前复权）
        """
        if not _HAS_AK:
            return None
        symbol_std = _adjust_symbol(symbol6)
        symbol_prefix = f'{symbol_std[-2:].lower()}{symbol6}'
        is_etf = self._is_etf_symbol6(symbol6)

        try:
            if period in {'1', '5', '15', '30', '60'}:
                start_min = f'{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]} 09:30:00'
                end_min = f'{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]} 15:00:00'
                if is_etf:
                    return self._call_akshare_api(
                        'fund_etf_hist_min_em',
                        lambda: ak.fund_etf_hist_min_em(
                            symbol=symbol6, period=period, start_date=start_min, end_date=end_min, adjust='qfq'),
                        symbol_std,
                        retries=2,
                    )
                return self._call_akshare_api(
                    'stock_zh_a_hist_min_em',
                    lambda: ak.stock_zh_a_hist_min_em(
                        symbol=symbol6, period=period, start_date=start_min, end_date=end_min, adjust='qfq'),
                    symbol_std,
                    retries=2,
                )
            period_map = {'D': 'daily', 'W': 'weekly', 'M': 'monthly'}
            ak_period = period_map.get(period)
            if ak_period is None:
                return None

            if is_etf:
                formatted = self._call_akshare_api(
                    'fund_etf_hist_em',
                    lambda: ak.fund_etf_hist_em(
                        symbol=symbol6, period=ak_period, start_date=start_date, end_date=end_date, adjust=''),
                    symbol_std,
                    retries=3,
                )
                if formatted is not None:
                    return formatted
                if period == 'D':
                    formatted = self._call_akshare_api(
                        'stock_zh_a_hist_tx',
                        lambda: ak.stock_zh_a_hist_tx(
                            symbol=symbol_prefix, start_date=start_date, end_date=end_date, adjust=''),
                        symbol_std,
                    )
                    if formatted is not None:
                        return formatted
                return None

            formatted = self._call_akshare_api(
                'stock_zh_a_hist',
                lambda: ak.stock_zh_a_hist(
                    symbol=symbol6, period=ak_period, start_date=start_date, end_date=end_date, adjust=''),
                symbol_std,
                retries=3,
            )
            if formatted is not None:
                return formatted

            if period == 'D':
                formatted = self._call_akshare_api(
                    'stock_zh_a_hist_tx',
                    lambda: ak.stock_zh_a_hist_tx(
                        symbol=symbol_prefix, start_date=start_date, end_date=end_date, adjust=''),
                    symbol_std,
                )
                if formatted is not None:
                    return formatted
                for adjust in ('', 'qfq'):
                    formatted = self._call_akshare_api(
                        f'stock_zh_a_daily({adjust or "none"})',
                        lambda adj=adjust: ak.stock_zh_a_daily(
                            symbol=symbol_prefix, start_date=start_date, end_date=end_date, adjust=adj),
                        symbol_std,
                    )
                    if formatted is not None:
                        return formatted
            return None
        except Exception as e:
            logger.debug('akshare 获取数据失败: {}', e)
            return None

    @staticmethod
    def _estimate_tdx_fetch_bars(period: str, start_date: str, end_date: str, count: int) -> int:
        """估算通达信分页请求 bar 上限（单次最多 800 条，按偏移翻页）。"""
        start_dt = DataEngine._parse_yyyymmdd(start_date)
        end_dt = DataEngine._parse_yyyymmdd(end_date) or datetime.now()
        if start_dt is None:
            return min(max(int(count), 1), 8000)
        span_days = max((min(end_dt, datetime.now()) - start_dt).days, 1)
        if period in {'1', '5', '15', '30', '60'}:
            bars_per_day = {'1': 240, '5': 48, '15': 16, '30': 8, '60': 4}.get(period, 48)
            need = span_days * bars_per_day + 200
        elif period == 'W':
            need = span_days // 5 + 50
        elif period == 'M':
            need = span_days // 20 + 24
        else:
            need = int(span_days * 1.45) + 50
        cap = min(max(int(count), 1), 8000) if int(count) > 0 else 8000
        return min(max(need, 800), cap)

    def _fetch_tdx(
        self,
        symbol6: str,
        period: str,
        start_date: str,
        end_date: str,
        count: int,
    ) -> Optional[pd.DataFrame]:
        """
        职责:
            使用 mootdx（通达信行情）分页拉取 K 线，再按 start_date/end_date 过滤。

        参数:
            symbol6 (str): 六位证券代码。
            period (str): 周期编码。
            start_date (str): 起始日期 YYYYMMDD。
            end_date (str): 结束日期 YYYYMMDD。
            count (int): 请求 bar 上限（分页累计不超过该值，默认最多 8000）。

        说明:
            通达信 bars 的 start 为从当前往历史的 bar 偏移，非日历；单次 offset 最多 800。

        返回:
            Optional[pd.DataFrame]: 成功返回数据表，失败返回 None。

        调用关系:
            - 由 get_hist_data() 最后兜底调用。
        """
        client = self._get_mootdx_client()
        if client is None:
            return None
        frequency = _MOOTDX_FREQ.get(period, 4)
        start_dt = self._parse_yyyymmdd(start_date)
        end_dt = self._parse_yyyymmdd(end_date)
        if end_dt is None:
            end_dt = datetime.now()
        if start_dt is not None and start_dt > end_dt:
            start_dt, end_dt = end_dt, start_dt

        max_bars = self._estimate_tdx_fetch_bars(period, start_date, end_date, count)
        chunks: List[pd.DataFrame] = []
        pos = 0

        try:
            while pos < max_bars:
                page_size = min(self._TDX_PAGE_SIZE, max_bars - pos)
                raw = client.bars(symbol=symbol6, frequency=frequency, start=pos, offset=page_size)
                if raw is None or raw.empty:
                    break
                chunks.append(raw.copy())
                if start_dt is not None:
                    dt_col = 'datetime' if 'datetime' in raw.columns else raw.columns[0]
                    earliest = pd.to_datetime(raw[dt_col], errors='coerce').min()
                    if pd.notna(earliest) and earliest <= start_dt:
                        break
                if len(raw) < page_size:
                    break
                pos += self._TDX_PAGE_SIZE

            if not chunks:
                return None

            merged = pd.concat(chunks, ignore_index=True)
            if 'datetime' in merged.columns:
                merged['date'] = pd.to_datetime(merged['datetime'], errors='coerce')
            else:
                merged['date'] = pd.to_datetime(merged.index, errors='coerce')
            merged = merged.dropna(subset=['date']).drop_duplicates(subset=['date'], keep='last')

            if start_dt is not None:
                merged = merged[merged['date'] >= start_dt]
            merged = merged[merged['date'] <= end_dt]
            if merged.empty:
                return None

            if 'vol' in merged.columns:
                merged['volume'] = pd.to_numeric(merged['vol'], errors='coerce')
            elif 'volume' in merged.columns:
                merged['volume'] = pd.to_numeric(merged['volume'], errors='coerce')
            else:
                return None
            if 'amount' not in merged.columns:
                merged['amount'] = 0
            else:
                merged['amount'] = pd.to_numeric(merged['amount'], errors='coerce').fillna(0)
            for col in ('open', 'high', 'low', 'close'):
                if col not in merged.columns:
                    return None
                merged[col] = pd.to_numeric(merged[col], errors='coerce')
            merged['stock_code'] = _adjust_symbol(symbol6)
            return merged[['date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'stock_code']]
        except Exception as e:
            logger.debug(f'TDX(mootdx) 分页获取失败: {e}')
            return None

    @staticmethod
    def _validate_hist_df(df: pd.DataFrame) -> pd.DataFrame:
        """
        职责:
            对历史数据进行统一时序校验与清洗，避免重复与乱序数据污染策略。

        参数:
            df (pd.DataFrame): 原始历史行情数据。

        返回:
            pd.DataFrame: 清洗后的数据表。

        异常:
            无显式抛出；日期解析失败行会被丢弃。

        调用关系:
            - 由 get_hist_data() 在各数据源成功后统一调用。
        """
        normalized = df.copy()
        normalized['date'] = pd.to_datetime(normalized['date'], errors='coerce')
        normalized = normalized.dropna(subset=['date'])
        normalized = normalized.sort_values('date')
        normalized = normalized.drop_duplicates(subset=['date'], keep='last')
        normalized = normalized.reset_index(drop=True)
        return normalized

    @staticmethod
    def _parse_yyyymmdd(date_str: str) -> Optional[datetime]:
        """
        职责:
            将 YYYYMMDD 解析为 datetime（00:00:00）。
        """
        try:
            return datetime.strptime(date_str, '%Y%m%d')
        except Exception:
            return None

    @staticmethod
    def _range_tolerance(period: str) -> timedelta:
        """
        职责:
            根据周期返回时间范围校验容差，用于处理交易日/节假日空档。
        """
        if period in {'1', '5', '15', '30', '60'}:
            return timedelta(days=2)
        if period == 'D':
            return timedelta(days=7)
        if period == 'W':
            return timedelta(days=21)
        if period == 'M':
            return timedelta(days=62)
        return timedelta(days=7)

    def _normalize_and_check_hist_df(self, df: pd.DataFrame, period: str, start_date: str, end_date: str,
                                     source_name: str) -> Optional[pd.DataFrame]:
        """
        职责:
            标准化历史数据并校验数据质量（非空、时间范围一致性、最新性）。

        校验规则:
            1) 返回数据不能为空；
            2) 结束时间必须满足请求 end_date（含容差），否则视为数据不全或非最新；
            3) 起始时间偏晚不再拒绝（常见于新上市标的或源历史深度不足），仅记 INFO 提示。
        """
        normalized = self._validate_hist_df(df)
        if normalized.empty:
            logger.warning(f'{source_name} 返回空数据，跳过该数据源')
            return None
        start_dt = self._parse_yyyymmdd(start_date)
        end_dt = self._parse_yyyymmdd(end_date)
        now_dt = datetime.now()
        tolerance = self._range_tolerance(period)
        min_dt = normalized['date'].min()
        max_dt = normalized['date'].max()
        if start_dt is not None and min_dt > start_dt + tolerance:
            logger.info(
                f'{source_name} 数据起始时间偏晚（可能为上市日或源深度限制）: '
                f'请求 start={start_date}, 实际最早={min_dt}')
        if end_dt is not None:
            expected_end = min(end_dt, now_dt)
            if max_dt < expected_end - tolerance:
                logger.warning(
                    f'{source_name} 数据结束时间异常: 请求 end={end_date}, 实际最晚={max_dt}, 期望不早于={expected_end - tolerance}')
                return None
            if end_dt > now_dt and max_dt < now_dt - tolerance:
                logger.warning(
                    f'{source_name} 数据非最新: end={end_date} 已超过当前时间, 但返回最晚时间={max_dt} 未达到最新可得区间')
                return None
        return normalized

    def _fetch_realtime_mootdx(self, symbol6: str, sym_disp: str) -> dict:
        """
        职责:
            通过 mootdx（通达信行情）获取单标的实时快照。

        返回:
            dict: 与 get_realtime 统一的中文键名；失败返回空字典。
        """
        client = self._get_mootdx_client()
        if client is None:
            return {}
        try:
            raw = client.quotes(symbol=symbol6)
            if raw is None or raw.empty:
                return {}
            row = raw.iloc[0]
            price = _safe_float(row.get('price'))
            if price <= 0:
                return {}
            last_close = _safe_float(row.get('last_close'))
            change_pct = (price - last_close) / last_close * 100 if last_close > 0 else 0.0
            vol = row.get('vol')
            if vol is None or (isinstance(vol, float) and pd.isna(vol)):
                vol = row.get('volume')
            code = str(row.get('code', symbol6))[:6]
            logger.info('成功从通达信(mootdx)获取数据 symbol={} (实时行情)', sym_disp)
            return {
                '最新价': price,
                '最高价': _safe_float(row.get('high')),
                '最低价': _safe_float(row.get('low')),
                '今开': _safe_float(row.get('open')),
                '昨收': last_close,
                '成交量': _safe_float(vol),
                '成交额': _safe_float(row.get('amount')),
                '涨跌幅': change_pct,
                '证券代码': code,
            }
        except Exception as e:
            logger.debug('mootdx 获取实时行情失败: {}', e)
            return {}

    def get_realtime(self, symbol: str) -> dict:
        """
        职责:
            获取单标的实时行情快照，优先东财，失败后降级 QMT 全推，再降级 mootdx（通达信）。

        参数:
            symbol (str): 标的代码。

        返回:
            dict: 行情字典；失败时返回空字典。

        异常:
            内部捕获抓取异常并自动降级。

        调用关系:
            - 由实盘策略用于兜底行情与状态判断。
        """
        parsed = _symbol6_list(symbol)
        if not parsed:
            logger.warning('无效证券代码，无法获取实时行情: {}', symbol)
            return {}
        symbol6 = parsed[0]
        sym_disp = _adjust_symbol(symbol6)
        for market in (0, 1):
            try:
                secid = f'{market}.{symbol6}'
                # f59=价格小数位数（fltt=1 时返回的是 真实价×10**f59 的整数原始价）
                params = {'invt': '2', 'fltt': '1', 'cb': 'jQuery_1',
                          'fields': 'f58,f57,f43,f44,f45,f46,f47,f48,f50,f51,f52,f59,f60,f116,f117,f168,f170',
                          'secid': secid, 'ut': 'fa5fd1943c7b386f172d6893dbfba10b', '_': '1685191053406'}
                res = requests.get('http://push2.eastmoney.com/api/qt/stock/get?', params=params, timeout=5)
                text = res.text
                text = text[text.index('{'):text.rindex('}') + 1]
                data = json.loads(text)['data']
                divisor = _resolve_price_divisor(symbol6, data)
                logger.info('成功从东财获取数据 symbol={} (实时行情)', sym_disp)
                return {'最新价': data['f43'] / divisor, '最高价': data['f44'] / divisor,
                        '最低价': data['f45'] / divisor, '今开': data['f46'] / divisor, '昨收': data['f60'] / divisor,
                        '成交量': data['f47'], '成交额': data['f48'], '涨跌幅': data['f170'] / 100,
                        '涨停': data['f51'] / divisor, '跌停': data['f52'] / divisor, '证券代码': data['f57'],
                        '股票名称': data['f58']}
            except Exception:
                continue
        if _HAS_XTQ:
            try:
                code = _adjust_symbol(symbol)
                tick = _xtdata.get_full_tick(code_list=[code]).get(code, {})
                if tick:
                    logger.info('成功从QMT获取数据 symbol={} (实时行情)', code)
                    return {'最新价': tick['lastPrice'], '最高价': tick['high'], '最低价': tick['low'],
                            '今开': tick['open'], '成交额': tick['amount'],
                            '涨跌幅': (tick['lastPrice'] - tick['open']) / tick['open'] * 100}
            except Exception:
                pass
        if _HAS_TDX:
            rt = self._fetch_realtime_mootdx(symbol6, sym_disp)
            if rt:
                return rt
        return {}

    def get_etf_premium_rate(self, symbol: str, trade_date: str) -> dict:
        """
        职责:
            计算单只 ETF 指定交易日的日终溢价率。

        数据源:
            - 单位净值：AkShare ``fund_etf_spot_ths(date=trade_date)`` 字段 ``当前-单位净值``
            - 收盘价：``get_hist_data`` 日线 ``close``

        公式:
            溢价率(%) = (close - 日终净值) / 日终净值 × 100

        参数:
            symbol (str): ETF 代码（6 位或带 .SH/.SZ 后缀）。
            trade_date (str): 交易日 YYYYMMDD（必填，须传给同花顺接口）。

        返回:
            dict: 键 ``代码``、``ETF名称``、``日期``、``日终净值``、``close``、``溢价率``；
            失败返回空 dict。
        """
        if not _HAS_AK:
            logger.warning('akshare 未安装，无法计算 ETF 溢价率')
            return {}
        codes = _symbol6_list(symbol)
        if not codes:
            logger.warning('无效 ETF 代码: {}', symbol)
            return {}
        symbol6 = codes[0]
        td = _yyyymmdd_str(trade_date)
        if not td:
            logger.warning('无效 trade_date: {}', trade_date)
            return {}
        try:
            ths_df = ak.fund_etf_spot_ths(date=td)
        except Exception as e:
            logger.warning('fund_etf_spot_ths date={} 失败: {}', td, e)
            return {}
        if ths_df is None or ths_df.empty:
            logger.warning('fund_etf_spot_ths date={} 返回空表', td)
            return {}
        code_col = '基金代码' if '基金代码' in ths_df.columns else '代码'
        name_col = '基金名称' if '基金名称' in ths_df.columns else '名称'
        nav_col = _THS_ETF_NAV_COL if _THS_ETF_NAV_COL in ths_df.columns else None
        if nav_col is None:
            for col in ths_df.columns:
                if '当前' in str(col) and '单位净值' in str(col):
                    nav_col = col
                    break
        if nav_col is None:
            logger.warning('fund_etf_spot_ths 缺少「当前-单位净值」列')
            return {}
        ths_df = ths_df.copy()
        ths_df['_code6'] = ths_df[code_col].astype(str).str.zfill(6)
        matched = ths_df[ths_df['_code6'] == symbol6]
        if matched.empty:
            logger.warning('fund_etf_spot_ths 未找到标的 {} date={}', symbol6, td)
            return {}
        row = matched.iloc[0]
        etf_name = str(row.get(name_col, '') or '')
        nav = _sanitize_fund_nav_value(row.get(nav_col))
        if pd.isna(nav):
            logger.warning('{} date={} 当前-单位净值无效', symbol6, td)
            return {}
        sym = _adjust_symbol(symbol6)
        close_val = float('nan')
        try:
            hdf = self.get_hist_data(sym, period='D', start_date=td, end_date=td, count=10)
            if hdf is not None and not hdf.empty and 'close' in hdf.columns:
                close_val = pd.to_numeric(hdf['close'].iloc[-1], errors='coerce')
        except Exception as e:
            logger.debug('get_hist_data 收盘价 {} date={} 失败: {}', sym, td, e)
        premium = float('nan')
        if not pd.isna(close_val) and float(close_val) > 0:
            premium = (float(close_val) - float(nav)) / float(nav) * 100
        else:
            logger.warning('{} date={} 无有效收盘价，溢价率为 NaN', symbol6, td)
        return {
            '代码': symbol6,
            'ETF名称': etf_name,
            '日期': td,
            '日终净值': float(nav),
            'close': float(close_val) if not pd.isna(close_val) else float('nan'),
            '溢价率': float(premium) if not pd.isna(premium) else float('nan'),
        }

    def get_etf_spot(self) -> pd.DataFrame:
        """
        职责:
            获取 ETF 全市场实时行情列表并做字段标准化。

        参数:
            无

        返回:
            pd.DataFrame: ETF 实时行情表；失败返回空表。

        异常:
            内部捕获异常并记录 error 日志。

        数据源链路（按优先级依次尝试，任一成功即返回）:
            1) QMT/xtquant（本机行情，条数最全；需 QMT 已登录且板块数据已下载）。
            2) AkShare ``fund_etf_spot_ths``（同花顺；仅代码/名称/净值，行情列为空）。
            3) AkShare ``fund_etf_spot_em``（东财分页封装，字段较全）。
            4) 东方财富 push2 直连（多 CDN host 轮换 + 全分页拉取 + 浏览器化头）。

        调用关系:
            - 供选股、监控或盘中看板模块调用。
        """
        sources = (
            ('QMT(xtquant)', self._fetch_etf_spot_qmt),
            ('AkShare(同花顺)', self._fetch_etf_spot_ths_akshare),
            ('AkShare(东财)', self._fetch_etf_spot_em_akshare),
            ('东方财富 push2', self._fetch_etf_spot_em_direct),
        )
        for name, fetcher in sources:
            try:
                df = fetcher()
            except Exception as e:
                logger.warning('ETF 全市场行情通过 {} 获取异常: {}', name, e)
                continue
            if df is not None and not df.empty:
                logger.info('成功从 {} 获取数据 (ETF 全市场行情列表, {} 条)', name, len(df))
                return df
            logger.warning('ETF 全市场行情通过 {} 返回空表，尝试下一数据源', name)
        logger.error('所有 ETF 行情数据源均获取失败')
        return pd.DataFrame()

    @staticmethod
    def _em_clist_diff(payload: dict) -> list:
        """从东财 clist/get JSON 中提取 diff 列表。"""
        data = payload.get('data') if isinstance(payload, dict) else None
        diff = data.get('diff') if isinstance(data, dict) else None
        return diff if diff else []

    def _fetch_etf_spot_em_direct(self, timeout: int = 8, retries: int = 1) -> Optional[pd.DataFrame]:
        """
        职责:
            通过东方财富 push2 全推接口直接拉取 ETF 全市场行情（全分页）。

        策略:
            - 在 ``_ETF_EM_HOSTS`` 配置的多 CDN host 间轮换；
            - 每个 host 最多请求 ``retries + 1`` 次，遇 HTTP 非 200、JSON 解析失败、
              ``data is None`` 等可恢复异常即切下一次/下一 host；
            - ``pz=100`` 分页直至 ``total`` 覆盖完毕（接口单页上限约 100 条）；
            - 增加 UA / Referer 头规避初级风控。

        参数:
            timeout (int): 单次 HTTP 超时秒数。
            retries (int): 单 host 重试次数（不含首次）。

        返回:
            Optional[pd.DataFrame]: 成功返回标准化行情表，否则 None。
        """
        params_base = {
            'pn': '1', 'pz': '100', 'po': '1', 'np': '1',
            'ut': 'bd1d9ddb04089700cf9c27f6f7426281',
            'fltt': '2', 'invt': '2', 'fid': 'f3',
            'fs': 'b:MK0021,b:MK0022,b:MK0023,b:MK0024',
            'fields': 'f12,f14,f2,f4,f3,f5,f6,f17,f15,f16,f8,f10',
        }
        col_rename = {
            'f12': '代码', 'f14': '名称', 'f2': '最新价', 'f4': '涨跌额',
            'f3': '涨跌幅', 'f5': '成交量', 'f6': '成交额',
            'f17': '开盘价', 'f15': '最高价', 'f16': '最低价',
            'f8': '换手率', 'f10': '量比',
        }
        last_err: Optional[str] = None
        for host in _ETF_EM_HOSTS:
            url = f'https://{host}/api/qt/clist/get'
            for attempt in range(retries + 1):
                try:
                    all_diff: list = []
                    params = params_base.copy()
                    r = requests.get(url, params=params, headers=_ETF_EM_HEADERS, timeout=timeout)
                    if r.status_code != 200:
                        last_err = f'HTTP {r.status_code}'
                        raise RuntimeError(last_err)
                    payload = r.json()
                    data = payload.get('data') if isinstance(payload, dict) else None
                    if not isinstance(data, dict):
                        last_err = 'missing data'
                        raise RuntimeError(last_err)
                    diff = self._em_clist_diff(payload)
                    if not diff:
                        last_err = 'empty diff'
                        raise RuntimeError(last_err)
                    all_diff.extend(diff)
                    per_page = len(diff)
                    total = int(data.get('total') or 0)
                    total_page = max(1, math.ceil(total / per_page)) if per_page else 1
                    for page in range(2, total_page + 1):
                        time.sleep(0.3)
                        page_params = {**params_base, 'pn': str(page)}
                        r_page = requests.get(
                            url, params=page_params, headers=_ETF_EM_HEADERS, timeout=timeout)
                        if r_page.status_code != 200:
                            last_err = f'HTTP {r_page.status_code} page={page}'
                            raise RuntimeError(last_err)
                        page_diff = self._em_clist_diff(r_page.json())
                        if not page_diff:
                            last_err = f'empty diff page={page}'
                            raise RuntimeError(last_err)
                        all_diff.extend(page_diff)
                    df = pd.DataFrame(all_diff).rename(columns=col_rename)
                    logger.debug(
                        '东财 push2 host={} 分页完成: {} 页, {} 条 (total={})',
                        host, total_page, len(df), total)
                    return self._normalize_etf_spot_df(df)
                except Exception as e:
                    last_err = str(e) or last_err
                    logger.debug('东财 push2 host={} attempt={} 失败: {}', host, attempt, last_err)
                    if attempt < retries:
                        time.sleep(0.3)
        logger.debug('东财 push2 全部 host 均失败，最近错误: {}', last_err)
        return None

    def _fetch_etf_spot_em_akshare(self) -> Optional[pd.DataFrame]:
        """
        职责:
            通过 AkShare 的 ``fund_etf_spot_em`` 拉取 ETF 全市场行情。

        说明:
            底层仍是东方财富，但 akshare 内部维护接口变更并对 host / 解析做了
            兼容处理；当直连 push2 全部 host 受阻时，此通道仍常能命中。

        返回:
            Optional[pd.DataFrame]: 标准化后的行情表；akshare 不可用或失败返回 None。
        """
        if not _HAS_AK:
            return None
        try:
            df = ak.fund_etf_spot_em()
        except Exception as e:
            logger.debug('akshare.fund_etf_spot_em 调用失败: {}', e)
            return None
        if df is None or df.empty:
            return None
        return self._normalize_etf_spot_df(df)

    def _fetch_etf_spot_qmt(self) -> Optional[pd.DataFrame]:
        """
        职责:
            通过 QMT/xtquant 获取 ETF 全市场实时行情。

        流程:
            1) ``xtdata.get_stock_list_in_sector('沪深基金')`` 拿场内基金代码；
               若空则按 ``沪深ETF/上证ETF/深证ETF`` 顺序兜底；首次失败时尝试
               ``download_sector_data()`` 后重试一次。
            2) 按代码前缀过滤出 ETF（沪 5xx，深 159/15x/16x）。
            3) ``xtdata.get_instrument_detail_list`` 批量取合约名称、前收盘价。
            4) ``xtdata.get_full_tick`` 批量拿最新价 / 开高低 / 量额。
            5) 由前收 / 最新价推算涨跌额、涨跌幅；换手率 / 量比留 NaN。

        说明:
            完全走本机 QMT 客户端，**不经公网**，可在东财全域 502 时兜底；
            缺点：需 QMT 已登录、首次需下载板块数据。

        返回:
            Optional[pd.DataFrame]: 标准化后的行情表；QMT 不可用或失败返回 None。
        """
        if not _HAS_XTQ:
            return None
        try:
            code_list = self._qmt_list_etf_sector()
        except Exception as e:
            logger.debug('QMT 获取板块成分股异常: {}', e)
            return None
        if not code_list:
            logger.debug('QMT 板块成分股为空，可能需要先 download_sector_data')
            return None
        etf_codes = [c for c in code_list if self._is_etf_xtcode(c)]
        if not etf_codes:
            logger.debug('QMT 板块返回了 {} 个标的，但无 ETF 前缀代码', len(code_list))
            return None
        try:
            ticks = _xtdata.get_full_tick(code_list=etf_codes) or {}
        except Exception as e:
            logger.debug('QMT get_full_tick 异常: {}', e)
            return None
        if not ticks:
            return None
        try:
            details = _xtdata.get_instrument_detail_list(stock_list=etf_codes, iscomplete=False) or {}
        except Exception as e:
            logger.debug('QMT get_instrument_detail_list 异常: {}', e)
            details = {}
        rows = []
        for code in etf_codes:
            tick = ticks.get(code) or {}
            if not tick:
                continue
            detail = details.get(code) or {}
            last_price = _safe_price(tick.get('lastPrice'))
            # QMT 部分字段未填充时返回 1.79e308 占位，必须用 _safe_price 过滤；
            # tick.lastClose 无效时回退 detail.PreClose，仍无效则置 NaN。
            last_close = _safe_price(tick.get('lastClose'))
            if pd.isna(last_close):
                last_close = _safe_price(detail.get('PreClose'))
            valid_close = (not pd.isna(last_close)) and last_close > 0
            valid_last = (not pd.isna(last_price)) and last_price > 0
            if valid_close and valid_last:
                change = last_price - last_close
                change_pct = change / last_close * 100
            else:
                change = float('nan')
                change_pct = float('nan')
            rows.append({
                '代码': code[:6],
                '名称': detail.get('InstrumentName') or '',
                '最新价': last_price,
                '涨跌额': change,
                '涨跌幅': change_pct,
                '成交量': _safe_nonneg(tick.get('volume')),
                '成交额': _safe_nonneg(tick.get('amount')),
                '开盘价': _safe_price(tick.get('open')),
                '最高价': _safe_price(tick.get('high')),
                '最低价': _safe_price(tick.get('low')),
                '换手率': float('nan'),
                '量比': float('nan'),
            })
        if not rows:
            return None
        return self._normalize_etf_spot_df(pd.DataFrame(rows))

    @staticmethod
    def _qmt_list_etf_sector() -> list:
        """
        职责:
            在 QMT 板块体系中按优先级尝试取 ETF / 场内基金的代码列表。

        策略:
            依次尝试 ``沪深基金`` → ``沪深ETF`` → ``上证ETF`` → ``深证ETF``；
            任一板块返回非空即返回；全部为空时尝试 ``download_sector_data()``
            后再用 ``沪深基金`` 重试一次。

        返回:
            list: 代码列表，可能为空。
        """
        candidates = ('沪深基金', '沪深ETF', '上证ETF', '深证ETF')
        for sector in candidates:
            try:
                ret = _xtdata.get_stock_list_in_sector(sector)
                if ret:
                    return list(ret)
            except Exception as e:
                logger.debug('QMT get_stock_list_in_sector({}) 异常: {}', sector, e)
        try:
            _xtdata.download_sector_data()
            ret = _xtdata.get_stock_list_in_sector('沪深基金')
            if ret:
                return list(ret)
        except Exception as e:
            logger.debug('QMT download_sector_data 重试失败: {}', e)
        return []

    @staticmethod
    def _is_etf_xtcode(code: str) -> bool:
        """
        职责:
            判断 QMT 风格代码（如 ``510300.SH`` / ``159915.SZ``）是否为 ETF。

        规则:
            沪市：5xx 段；深市：15x / 16x 段。
        """
        if not code or len(code) < 8:
            return False
        sym6 = code[:6]
        prefix2 = sym6[:2]
        return prefix2 in ('51', '52', '56', '58', '15', '16')

    def _fetch_etf_spot_ths_akshare(self) -> Optional[pd.DataFrame]:
        """
        职责:
            通过 AkShare 的 ``fund_etf_spot_ths`` 拉取同花顺 ETF 列表，作为
            完全独立于东财基础设施的最终兜底。

        说明:
            同花顺接口返回的是「基金代码 / 基金名称 / 单位净值 / 增长率」等
            净值类字段，**没有盘中行情字段**，因此本方法仅保证 ``代码`` /
            ``名称`` 可用，其余行情列以 NaN 填充。适用于仅依赖 ETF 标的列表
            的上层调用方（如 ``_jq_get_all_securities``）。

        返回:
            Optional[pd.DataFrame]: 含代码 / 名称的最小行情表；失败返回 None。
        """
        if not _HAS_AK:
            return None
        try:
            df = ak.fund_etf_spot_ths()
        except Exception as e:
            logger.debug('akshare.fund_etf_spot_ths 调用失败: {}', e)
            return None
        if df is None or df.empty:
            return None
        rename_map = {'基金代码': '代码', '基金名称': '名称'}
        df = df.rename(columns=rename_map)
        return self._normalize_etf_spot_df(df)

    @staticmethod
    def _normalize_etf_spot_df(df: pd.DataFrame) -> pd.DataFrame:
        """
        职责:
            将不同数据源返回的 ETF 行情 DataFrame 对齐到统一列与类型。

        参数:
            df (pd.DataFrame): 上游原始或部分重命名后的行情表。

        返回:
            pd.DataFrame: 含 ``_ETF_SPOT_COLUMNS`` 列、缺失列以 NaN 填充、
            数值列经过 ``to_numeric`` 强转后的标准化表。
        """
        if df is None or df.empty:
            return pd.DataFrame(columns=_ETF_SPOT_COLUMNS)
        out = df.copy()
        for col in _ETF_SPOT_COLUMNS:
            if col not in out.columns:
                out[col] = pd.NA
        out['代码'] = out['代码'].astype(str).str.zfill(6)
        out['名称'] = out['名称'].astype(str)
        for col in _ETF_SPOT_NUMERIC_COLUMNS:
            out[col] = pd.to_numeric(out[col], errors='coerce')
        out = out.dropna(subset=['代码'])
        out = out[out['代码'].str.match(r'^\d{6}$')]
        out = out.drop_duplicates(subset=['代码'], keep='first').reset_index(drop=True)
        return out[_ETF_SPOT_COLUMNS]

    def get_jisilu_funds(
        self,
        cookie: Optional[str] = None,
        kinds: Union[str, List[str], None] = 'all',
        index_id: str = '',
        min_volume: float = 0,
        max_discount: Optional[Union[str, float]] = None,
        min_discount: Optional[Union[str, float]] = None,
        exclude_bad_notes: bool = True,
        as_dict: bool = False,
    ):
        """
        返回集思录实时数据 https://www.jisilu.cn/data/etf/#index
        集思录场内基金统一接口（ETF + QDII 亚洲/欧美），统一 DataFrame 列格式。

        kinds: 'all'（默认）| 'etf' | 'qdii' | 'qdii_a' | 'qdii_e' 或列表
        index_id: 仅 ETF 按跟踪指数过滤
        Cookie: 参数 cookie 或环境变量 JISILU_COOKIE
        """
        return fetch_jisilu_funds(
            cookie = "kbz_newcookie=1; kbzw__Session=1otmkpb3qvrnvu068jdve560r2; Hm_lvt_164fe01b1433a19b507595a43bf58262=1779352044,1779426989; HMACCOUNT=57A12819F284E306; kbzw__user_login=7Obd08_P1ebax9aXwZenlK-opK2Yo4KvpuXK7N_u0ejF1dSesJLSlafeqaDepNmZ18Kw29XdxaGVqN2umtqksJKrx9yYrqXW2cXS1qCasaKslKqUmLKgubXOvp-qrKCyoKmZppmvmK6ltrG_0aTC2PPV487XkKylo5iJx8ri3eTg7IzFtpaSp6Wjs4HHyuKvqaSZ5K2Wn4G45-PkxsfG1sTe3aihqpmklK2Xm8OpxK7ApZXV4tfcgr3G2uLioYGzyebo4s6onauapJGlp6GogcPC2trn0qihqpmklK0.; Hm_lpvt_164fe01b1433a19b507595a43bf58262=1779427750",
            kinds=kinds,
            index_id=index_id,
            min_volume=min_volume,
            max_discount=max_discount,
            min_discount=min_discount,
            exclude_bad_notes=exclude_bad_notes,
            as_dict=as_dict,
        )

