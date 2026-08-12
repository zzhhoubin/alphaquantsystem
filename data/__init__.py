from .data_engine import DataEngine

from .hist_source import HIST_SOURCE_DISPLAY, LEGACY_CHAIN, ORDERED_CHAIN, resolve_hist_source_chain

from .jisilu import JISILU_FUND_COLUMNS, JSL_COOKIE_ENV, fetch_jisilu_fund_cells, fetch_jisilu_funds

_TDX_EXPORTS = frozenset({
    'TdxLocalReader',
    'classify_tdx_symbol',
    'clear_tdx_scan_cache',
    'filter_tdx_symbols',
    'get_tdx_local_reader',
    'load_tdx_bars',
    'resolve_tdxdir',
    'scan_tdx_vipdoc',
})

__all__ = [
    'DataEngine', 'HIST_SOURCE_DISPLAY', 'LEGACY_CHAIN', 'ORDERED_CHAIN',
    'resolve_hist_source_chain',
    'JISILU_FUND_COLUMNS', 'JSL_COOKIE_ENV', 'fetch_jisilu_funds', 'fetch_jisilu_fund_cells',
    'TdxLocalReader', 'classify_tdx_symbol', 'clear_tdx_scan_cache', 'filter_tdx_symbols',
    'get_tdx_local_reader', 'load_tdx_bars', 'resolve_tdxdir', 'scan_tdx_vipdoc',
]


def __getattr__(name: str):
    if name in _TDX_EXPORTS:
        from . import tdx_local

        value = getattr(tdx_local, name)
        globals()[name] = value
        return value
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


def __dir__():
    return sorted(set(globals()) | set(_TDX_EXPORTS))
