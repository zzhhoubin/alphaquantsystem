"""
通达信本地数据接口 — 基于 mootdx Reader，支持批量、多线程与内存缓存。

数据类型:
  - day / daily : 日线 (.day)
  - 1m / minute : 1 分钟线 (minline)
  - 5m / fzline : 5 分钟线 (fzline，通达信本地分时/五分钟数据)

目录自动解析顺序: 参数 ``tdxdir`` → 环境变量 ``TDXDIR`` → mootdx 配置 → 常见安装路径。

对外主入口:
  TdxLocalReader.load()  — 单只/批量加载，返回 ``dict[代码, DataFrame]``
  load_tdx_bars()        — 模块级便捷函数（复用默认 Reader 实例）
  scan_tdx_vipdoc()      — 扫描 vipdoc 下全部标的并区分 ETF / 股票等
"""
from __future__ import annotations

import os
import struct
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock
from typing import Dict, List, Literal, Optional, Sequence, Tuple, Union

import pandas as pd
from loguru import logger

try:
    from mootdx.config import get as _mootdx_get
    from mootdx.config import setup as _mootdx_setup
    from mootdx.reader import Reader as _MootdxReader

    _HAS_MOOTDX = True
except ImportError:
    _HAS_MOOTDX = False
    _MootdxReader = None  # type: ignore[misc, assignment]

TdxFreq = Literal['day', 'daily', '1m', 'minute', '5m', 'fzline']
TdxAssetKind = Literal['etf', 'stock', 'index', 'bond', 'other', 'all']
TdxKind = Literal['etf', 'stock', 'index', 'bond', 'other']
_DEFAULT_VIPDOC_MARKETS: Tuple[str, ...] = ('sh', 'sz', 'bj')
_SH_ETF_PREFIX2 = frozenset({'50', '51', '52', '56', '58'})
_SH_STOCK_PREFIX2 = frozenset({'60', '68', '90'})
_SH_INDEX_PREFIX2 = frozenset({'00', '88', '99'})
_SH_BOND_PREFIX2 = frozenset({
    '01', '02', '10', '11', '12', '13', '14', '15', '16', '17', '18', '19', '20',
})
_SZ_ETF_PREFIX2 = frozenset({'15', '16', '18'})
_SZ_STOCK_PREFIX2 = frozenset({'00', '30', '20'})
_SZ_INDEX_PREFIX2 = frozenset({'39'})
_SZ_BOND_PREFIX2 = frozenset({'10', '11', '12', '13', '14'})
_BJ_STOCK_PREFIX2 = frozenset({'43', '83', '87', '88', '92'})
_FREQ_ALIASES: Dict[str, str] = {
    'day': 'day', 'daily': 'day', 'd': 'day',
    '1m': '1m', '1': '1m', 'minute': '1m', 'min': '1m',
    '5m': '5m', '5': '5m', 'fzline': '5m', 'fz': '5m',
}
_COMMON_TDX_DIRS = (
    'C:/new_tdx', 'D:/new_tdx', 'C:/通达信', 'D:/通达信', 'C:/tdx', 'D:/tdx',
)
_EMPTY_DF = pd.DataFrame()
_SCAN_COLUMNS = ['code', 'market', 'tdx_code', 'kind', 'is_etf', 'is_stock']
_scan_cache: Dict[tuple, tuple[float, pd.DataFrame]] = {}
_scan_cache_lock = Lock()


def _require_mootdx() -> None:
    if not _HAS_MOOTDX:
        raise ImportError('未安装 mootdx，请执行: pip install "mootdx~=0.11.7"')


def resolve_tdxdir(tdxdir: Optional[str] = None) -> str:
    """解析通达信安装目录（含 vipdoc 数据）。"""
    if tdxdir:
        p = Path(tdxdir).expanduser()
        if p.is_dir():
            return str(p.resolve())

    env = os.environ.get('TDXDIR', '').strip()
    if env and Path(env).is_dir():
        return str(Path(env).resolve())

    if _HAS_MOOTDX:
        try:
            _mootdx_setup()
            cfg = _mootdx_get('TDXDIR')
            if cfg and Path(cfg).is_dir():
                return str(Path(cfg).resolve())
        except Exception:
            pass

    for candidate in _COMMON_TDX_DIRS:
        p = Path(candidate)
        if p.is_dir() and (p / 'vipdoc').is_dir():
            return str(p.resolve())

    raise FileNotFoundError(
        '未找到通达信数据目录。请设置环境变量 TDXDIR、配置 ~/.mootdx/config.json，或传入 tdxdir=',
    )


def _split_tdx_symbol(symbol: str) -> tuple[str, str]:
    """解析 ``sh600000`` / ``600000.SH`` / ``600000`` → (market, code6)。"""
    s = str(symbol).strip().lower().replace('.', '')
    for prefix in ('sh', 'sz', 'bj'):
        if s.startswith(prefix) and len(s) > len(prefix):
            return prefix, s[len(prefix):]
    if s.endswith(('sh', 'sz', 'bj')) and len(s) > 2:
        return s[-2:], s[:-2]
    return '', s


def _norm_symbol(symbol: str) -> str:
    _, code = _split_tdx_symbol(symbol)
    return code


def _infer_market(code: str) -> str:
    c = _norm_symbol(code)
    # 避免将异常代码交给 mootdx，触发 tdxpy "Unknown security type" 噪声日志。
    if len(c) != 6 or not c.isdigit():
        if c[:1] in ('4', '8'):
            return 'bj'
        return 'sz'
    if _HAS_MOOTDX:
        try:
            from mootdx.utils import get_stock_market

            return str(get_stock_market(c, string=True)).lower()
        except Exception:
            pass
    if c[:2] in _SH_ETF_PREFIX2 | _SH_STOCK_PREFIX2 | _SH_INDEX_PREFIX2 | _SH_BOND_PREFIX2:
        return 'sh'
    if c[:2] in _SZ_ETF_PREFIX2 | _SZ_STOCK_PREFIX2 | _SZ_INDEX_PREFIX2 | _SZ_BOND_PREFIX2:
        return 'sz'
    if c[:2] in _BJ_STOCK_PREFIX2 or c[:1] in ('4', '8'):
        return 'bj'
    return 'sz'


def _classify_kind(market: str, code: str) -> TdxKind:
    """按交易所 + 代码段判定资产类别（与 DataEngine ETF 规则对齐）。"""
    m = market.lower()
    p2 = code[:2]
    if m == 'sh':
        if p2 in _SH_ETF_PREFIX2:
            return 'etf'
        if p2 in _SH_STOCK_PREFIX2:
            return 'stock'
        if p2 in _SH_INDEX_PREFIX2:
            return 'index'
        if p2 in _SH_BOND_PREFIX2:
            return 'bond'
        return 'other'
    if m == 'sz':
        if p2 in _SZ_ETF_PREFIX2:
            return 'etf'
        if p2 in _SZ_STOCK_PREFIX2:
            return 'stock'
        if p2 in _SZ_INDEX_PREFIX2:
            return 'index'
        if p2 in _SZ_BOND_PREFIX2:
            return 'bond'
        return 'other'
    if m == 'bj':
        if p2 in _BJ_STOCK_PREFIX2 or code[:1] in ('4', '8'):
            return 'stock'
        return 'other'
    return 'other'


def classify_tdx_symbol(code: str, market: Optional[str] = None) -> Dict[str, object]:
    """
    单只标的资产类别判定（O(1)，不读本地文件）。

    :param code: 6 位代码或 ``sh600000`` / ``600000.SH`` 形式
    :param market: 交易所 ``sh`` / ``sz`` / ``bj``；省略时按代码段推断
    :return: 含 ``code`` / ``market`` / ``tdx_code`` / ``kind`` / ``is_etf`` / ``is_stock``
    """
    m, sym = _split_tdx_symbol(code)
    if not sym:
        raise ValueError(f'无效代码: {code!r}')
    if not m:
        m = (market or _infer_market(sym)).lower()
    else:
        m = m.lower()
    if market and m != market.lower():
        m = market.lower()
    kind = _classify_kind(m, sym)
    return {
        'code': sym,
        'market': m,
        'tdx_code': f'{m}{sym}',
        'kind': kind,
        'is_etf': kind == 'etf',
        'is_stock': kind == 'stock',
    }


def _vipdoc_fingerprint(tdxdir: str, markets: Sequence[str]) -> float:
    root = Path(tdxdir) / 'vipdoc'
    mtimes = [lday.stat().st_mtime for m in markets if (lday := root / m / 'lday').is_dir()]
    return max(mtimes) if mtimes else 0.0


def scan_tdx_vipdoc(
    tdxdir: Optional[str] = None,
    markets: Sequence[str] = _DEFAULT_VIPDOC_MARKETS,
) -> pd.DataFrame:
    """
    扫描 ``{tdxdir}/vipdoc/*/lday/*.day``，返回全部本地标的及资产类别。

    仅解析文件名，不读取 K 线内容；结果按目录 mtime 内存缓存。
    """
    root_dir = resolve_tdxdir(tdxdir)
    mkt_tuple = tuple(markets)
    cache_key = (root_dir, mkt_tuple)
    fp = _vipdoc_fingerprint(root_dir, mkt_tuple)
    with _scan_cache_lock:
        hit = _scan_cache.get(cache_key)
        if hit and hit[0] == fp:
            return hit[1].copy()

    rows: List[Dict[str, object]] = []
    vipdoc = Path(root_dir) / 'vipdoc'
    for market in mkt_tuple:
        lday = vipdoc / market / 'lday'
        if not lday.is_dir():
            continue
        for path in lday.glob('*.day'):
            sym = path.stem
            if len(sym) <= 2:
                continue
            code = sym[2:] if sym[:2] == market else sym
            info = classify_tdx_symbol(code, market=market)
            rows.append(info)

    if rows:
        df = pd.DataFrame(rows)[_SCAN_COLUMNS]
        df.sort_values(['market', 'code'], inplace=True)
        df.reset_index(drop=True, inplace=True)
    else:
        df = pd.DataFrame(columns=_SCAN_COLUMNS)

    with _scan_cache_lock:
        _scan_cache[cache_key] = (fp, df.copy())
    return df


def filter_tdx_symbols(
    asset_kind: Union[str, TdxAssetKind] = 'all',
    tdxdir: Optional[str] = None,
    markets: Sequence[str] = _DEFAULT_VIPDOC_MARKETS,
    *,
    group_by_market: bool = False,
) -> Union[List[str], Dict[str, List[str]]]:
    """
    从本地 vipdoc 筛选指定类别标的代码。

    :param asset_kind: ``etf`` / ``stock`` / ``index`` / ``bond`` / ``other`` / ``all``
    :param group_by_market: True 时返回 ``{market: [codes]}``
    """
    kind = str(asset_kind).strip().lower()
    df = scan_tdx_vipdoc(tdxdir=tdxdir, markets=markets)
    if df.empty:
        return {} if group_by_market else []
    if kind != 'all':
        if kind not in {'etf', 'stock', 'index', 'bond', 'other'}:
            raise ValueError(f'未知 asset_kind: {asset_kind!r}')
        df = df[df['kind'] == kind]
    if group_by_market:
        return {m: g['code'].tolist() for m, g in df.groupby('market', sort=True)}
    return df['code'].tolist()


def clear_tdx_scan_cache() -> None:
    """清空 vipdoc 扫描缓存。"""
    with _scan_cache_lock:
        _scan_cache.clear()


def _norm_symbols(symbols: Union[str, Sequence[str]]) -> List[str]:
    if isinstance(symbols, str):
        symbols = [symbols]
    return list(dict.fromkeys(_norm_symbol(s) for s in symbols if str(s).strip()))


def _norm_freq(freq: str) -> str:
    key = _FREQ_ALIASES.get(str(freq).strip().lower())
    if not key:
        raise ValueError(f'未知频率: {freq!r}，可选: day / 1m / 5m')
    return key


def _source_path(reader: object, symbol: str, freq: str) -> Optional[Path]:
    """借助 mootdx Reader 定位本地数据文件（用于缓存失效检测）。"""
    if freq == 'day':
        return reader.find_path(symbol=symbol, subdir='lday', suffix='day')  # type: ignore[union-attr]
    if freq == '1m':
        return reader.find_path(symbol=symbol, subdir='minline', suffix=['lc1', '1'])  # type: ignore[union-attr]
    return reader.find_path(symbol=symbol, subdir='fzline', suffix=['lc5', '5'])  # type: ignore[union-attr]


def _normalize_day_volume(df: pd.DataFrame) -> pd.DataFrame:
    """
    校正日线成交量单位，使 amount ≈ volume × close。

    mootdx 对深市 ETF / A 股会将 volume 多除 100；通达信 .day 直读历史上也有
    相同系数。按行检测 amount/(volume*close)≈100 时，将 volume 放大 100 倍。
    """
    if df is None or df.empty:
        return df
    vol_col = 'volume' if 'volume' in df.columns else ('vol' if 'vol' in df.columns else None)
    if vol_col is None or 'close' not in df.columns or 'amount' not in df.columns:
        return df
    out = df.copy()
    vol = pd.to_numeric(out[vol_col], errors='coerce')
    close = pd.to_numeric(out['close'], errors='coerce')
    amt = pd.to_numeric(out['amount'], errors='coerce')
    denom = vol * close
    ratio = amt / denom.where(denom > 0)
    mask = (vol > 0) & (close > 0) & (amt > 0) & ratio.between(50, 150)
    if mask.any():
        out.loc[mask, vol_col] = vol[mask] * 100.0
    return out


def _read_raw(reader: object, symbol: str, freq: str) -> pd.DataFrame:
    if freq == 'day':
        # mootdx/tdxpy 对沪市部分 ETF 前缀(如 52/56/58)分类规则偏旧，主动走本地 .day 直读分支。
        market = _infer_market(symbol)
        p2 = symbol[:2]
        if market == 'sh' and p2 in {'52', '56', '58'}:
            df = _read_day_file_direct(reader, symbol, market=market)
        else:
            df = reader.daily(symbol=symbol)  # type: ignore[union-attr]
    elif freq == '1m':
        df = reader.minute(symbol=symbol, suffix=1)  # type: ignore[union-attr]
    else:
        df = reader.fzline(symbol=symbol)  # type: ignore[union-attr]
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return _EMPTY_DF.copy()
    out = df.copy()
    if freq == 'day':
        out = _normalize_day_volume(out)
    out['symbol'] = symbol
    return out


def _day_coefficients(market: str, kind: TdxKind) -> tuple[float, float]:
    if kind == 'index':
        return 0.01, 1.0
    if kind in {'etf', 'bond'}:
        # 通达信 .day 原始 volume 为股数；沪/深 ETF 均不再额外缩放。
        return 0.001, 1.0
    if kind == 'stock':
        return 0.01, 1.0
    return 0.01, 1.0


def _read_day_file_direct(reader: object, symbol: str, *, market: str) -> pd.DataFrame:
    path = reader.find_path(symbol=symbol, subdir='lday', suffix='day')  # type: ignore[union-attr]
    if path is None or not Path(path).is_file():
        return _EMPTY_DF.copy()
    blob = Path(path).read_bytes()
    rec_size = struct.calcsize('<IIIIIfII')
    if rec_size <= 0 or len(blob) < rec_size:
        return _EMPTY_DF.copy()
    n = len(blob) // rec_size
    if n <= 0:
        return _EMPTY_DF.copy()

    kind = _classify_kind(market, symbol)
    px_coef, vol_coef = _day_coefficients(market, kind)
    rows = []
    for rec in struct.iter_unpack('<IIIIIfII', blob[: n * rec_size]):
        t_date = str(rec[0])
        datestr = f'{t_date[:4]}-{t_date[4:6]}-{t_date[6:]}'
        rows.append(
            (
                datestr,
                rec[1] * px_coef,
                rec[2] * px_coef,
                rec[3] * px_coef,
                rec[4] * px_coef,
                rec[5],
                rec[6] * vol_coef,
            ),
        )
    if not rows:
        return _EMPTY_DF.copy()
    df = pd.DataFrame(rows, columns=['date', 'open', 'high', 'low', 'close', 'amount', 'volume'])
    df.index = pd.to_datetime(df['date'], errors='coerce')
    return df[['open', 'high', 'low', 'close', 'amount', 'volume']]


class TdxLocalReader:
    """
    通达信本地行情读取器。

    - 路径、市场前缀由 mootdx 自动处理
    - 单标的读取失败时返回空 DataFrame，不向外抛异常
    - 默认开启内存缓存，重复读取同一文件时 O(1) 返回
    """

    def __init__(
        self,
        tdxdir: Optional[str] = None,
        market: str = 'std',
        workers: Optional[int] = None,
        cache: bool = True,
        cache_size: int = 4096,
    ):
        _require_mootdx()
        self.tdxdir = resolve_tdxdir(tdxdir)
        self.market = market
        self.workers = workers
        self.cache_enabled = cache
        self.cache_size = max(64, cache_size)
        self._reader = _MootdxReader.factory(market=market, tdxdir=self.tdxdir)
        self._cache: Dict[tuple, tuple[float, pd.DataFrame]] = {}
        self._cache_lock = Lock()

    def _cache_get(self, key: tuple, src: Optional[Path]) -> Optional[pd.DataFrame]:
        if not self.cache_enabled:
            return None
        mtime = src.stat().st_mtime if src and src.is_file() else -1.0
        with self._cache_lock:
            hit = self._cache.get(key)
            if hit and hit[0] == mtime:
                return hit[1]
        return None

    def _cache_put(self, key: tuple, src: Optional[Path], df: pd.DataFrame) -> None:
        if not self.cache_enabled:
            return
        mtime = src.stat().st_mtime if src and src.is_file() else -1.0
        with self._cache_lock:
            if len(self._cache) >= self.cache_size:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = (mtime, df)

    def read_one(self, symbol: str, freq: Union[str, TdxFreq] = 'day') -> pd.DataFrame:
        """读取单只标的，失败或缺失时返回空 DataFrame。"""
        sym = _norm_symbol(symbol)
        kind = _norm_freq(freq)
        key = (self.tdxdir, self.market, kind, sym)
        src = _source_path(self._reader, sym, kind)
        cached = self._cache_get(key, src)
        if cached is not None:
            return cached
        try:
            df = _read_raw(self._reader, sym, kind)
        except Exception as e:
            logger.error(
                "TDX本地读取失败 symbol={} freq={} raw_symbol={} err={}",
                sym,
                kind,
                symbol,
                e,
            )
            df = _EMPTY_DF.copy()
        self._cache_put(key, src, df)
        return df

    def load(
        self,
        symbols: Union[str, Sequence[str]],
        freq: Union[str, TdxFreq] = 'day',
        *,
        parallel: bool = True,
    ) -> Dict[str, pd.DataFrame]:
        """
        批量加载本地 K 线。

        :param symbols: 单只代码或代码列表（可带 sh/sz 前缀）
        :param freq: day / 1m / 5m
        :param parallel: 多标的时是否多线程（默认 True）
        :return: ``{代码: DataFrame}``，缺失数据对应空表
        """
        syms = _norm_symbols(symbols)
        if not syms:
            return {}
        if len(syms) == 1 or not parallel:
            return {s: self.read_one(s, freq) for s in syms}

        n_workers = self.workers or min(32, max(4, (os.cpu_count() or 4) * 2), len(syms))
        out: Dict[str, pd.DataFrame] = {}
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            futs = {pool.submit(self.read_one, s, freq): s for s in syms}
            for fut in as_completed(futs):
                sym = futs[fut]
                try:
                    out[sym] = fut.result()
                except Exception:
                    out[sym] = _EMPTY_DF.copy()
        return out

    def daily(self, symbols: Union[str, Sequence[str]], **kwargs) -> Dict[str, pd.DataFrame]:
        return self.load(symbols, 'day', **kwargs)

    def minute(self, symbols: Union[str, Sequence[str]], **kwargs) -> Dict[str, pd.DataFrame]:
        return self.load(symbols, '1m', **kwargs)

    def fzline(self, symbols: Union[str, Sequence[str]], **kwargs) -> Dict[str, pd.DataFrame]:
        return self.load(symbols, '5m', **kwargs)

    def scan_universe(
        self,
        markets: Sequence[str] = _DEFAULT_VIPDOC_MARKETS,
    ) -> pd.DataFrame:
        """扫描本 Reader 对应通达信目录下的全部本地标的。"""
        return scan_tdx_vipdoc(tdxdir=self.tdxdir, markets=markets)

    def clear_cache(self) -> None:
        with self._cache_lock:
            self._cache.clear()


_default_reader: Optional[TdxLocalReader] = None
_default_lock = Lock()


def get_tdx_local_reader(**kwargs) -> TdxLocalReader:
    """获取（懒加载）默认 TdxLocalReader 单例。"""
    global _default_reader
    if _default_reader is not None and not kwargs:
        return _default_reader
    with _default_lock:
        if _default_reader is None or kwargs:
            _default_reader = TdxLocalReader(**kwargs)
        return _default_reader


def load_tdx_bars(
    symbols: Union[str, Sequence[str]],
    freq: Union[str, TdxFreq] = 'day',
    *,
    tdxdir: Optional[str] = None,
    parallel: bool = True,
    **reader_kwargs,
) -> Dict[str, pd.DataFrame]:
    """
  便捷批量入口。首次调用可传 ``tdxdir``；后续复用同一 Reader 实例与内存缓存。

  >>> load_tdx_bars(['600000', '000001'], freq='day')
  """
    if tdxdir or reader_kwargs:
        reader = TdxLocalReader(tdxdir=tdxdir, **reader_kwargs)
    else:
        reader = get_tdx_local_reader()
    return reader.load(symbols, freq, parallel=parallel)


__all__ = [
    'TdxAssetKind', 'TdxFreq', 'TdxKind', 'TdxLocalReader',
    'classify_tdx_symbol', 'clear_tdx_scan_cache', 'filter_tdx_symbols',
    'get_tdx_local_reader', 'load_tdx_bars', 'resolve_tdxdir', 'scan_tdx_vipdoc',
]
