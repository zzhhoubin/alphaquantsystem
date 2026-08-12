"""
集思录场内基金统一接口 — ETF + QDII（亚洲/欧美）一次拉取、统一表结构。

数据源:
  - 指数 ETF: https://www.jisilu.cn/data/etf/
  - QDII 亚洲: https://www.jisilu.cn/data/qdii/#qdiia  -> qdii_list/A
  - QDII 欧美: https://www.jisilu.cn/data/qdii/#qdiie  -> qdii_list/E

Cookie: 参数 cookie 或环境变量 JISILU_COOKIE

对外主入口:
  fetch_jisilu_funds()  — 拉取并返回统一格式 DataFrame / dict
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Sequence, Union

import pandas as pd

from .jisilu_common import (
    JSL_COOKIE_ENV,
    fetch_jsl_cells,
    percentage2float,
    safe_float,
)

# --- 数据源配置 ---
JSL_ETF_URL = 'https://www.jisilu.cn/data/etf/etf_list/'
JSL_ETF_REFERER = 'https://www.jisilu.cn/data/etf/'
JSL_QDII_REFERER = 'https://www.jisilu.cn/data/qdii/'
JSL_QDII_A_URL = 'https://www.jisilu.cn/data/qdii/qdii_list/A'
JSL_QDII_E_URL = 'https://www.jisilu.cn/data/qdii/qdii_list/E'

FundKind = Literal['etf', 'qdii', 'qdii_a', 'qdii_e', 'all']
_KIND_ALIASES = {
    'etf': 'etf',
    '指数etf': 'etf',
    '指数ETF': 'etf',
    'qdii': 'qdii',
    'qdii_a': 'qdii_a',
    'qdii_e': 'qdii_e',
    'qdiia': 'qdii_a',
    'qdiie': 'qdii_e',
    '亚洲': 'qdii_a',
    '欧美': 'qdii_e',
    'all': 'all',
    '全部': 'all',
}

# 统一输出列（ETF / QDII 共用）
JISILU_FUND_COLUMNS = [
    '品种', '市场', '代码', '名称', '子类型', '现价', '涨幅', '成交额',
    '场内份额', '场内新增', '净值', '净值日期', '估值', '估值日期', '溢价率',
    '跟踪标的', '指数代码', '标的涨幅', '规模', '份额',
    'PE', 'PB', '最小申赎单位', '管理费', '申购费', '赎回费', '托管费', '基金公司', 'T+0',
]
_JISILU_NUMERIC_COLUMNS = [
    '现价', '成交额', '场内份额', '场内新增', '净值', '估值', '规模', '份额',
    'PE', 'PB', '最小申赎单位', '管理费', '申购费', '赎回费', '托管费',
]


def _normalize_kinds(kinds: Union[str, Sequence[str], None]) -> List[str]:
    if kinds is None or kinds == 'all' or kinds == ('all',):
        return ['etf', 'qdii_a', 'qdii_e']
    if isinstance(kinds, str):
        kinds = [kinds]
    out: List[str] = []
    for k in kinds:
        key = _KIND_ALIASES.get(str(k).strip(), str(k).strip().lower())
        if key == 'all':
            return ['etf', 'qdii_a', 'qdii_e']
        if key == 'qdii':
            out.extend(['qdii_a', 'qdii_e'])
        elif key in ('etf', 'qdii_a', 'qdii_e'):
            out.append(key)
    return list(dict.fromkeys(out))


def _tag_cell(cell: dict, variety: str, market: str) -> dict:
    row = dict(cell)
    row['_variety'] = variety
    row['_market'] = market
    return row


def _fetch_etf_cells(
    cookie: Optional[str],
    page_size: int,
    timeout: int,
) -> List[dict]:
    cells = fetch_jsl_cells(
        JSL_ETF_URL,
        JSL_ETF_REFERER,
        cookie=cookie,
        page_size=page_size,
        timeout=timeout,
        source_name='集思录ETF',
    )
    return [_tag_cell(c, 'ETF', '指数ETF') for c in cells]


def _fetch_qdii_cells(
    market_code: Literal['A', 'E'],
    cookie: Optional[str],
    page_size: int,
    timeout: int,
    exclude_bad_notes: bool,
) -> List[dict]:
    label = '亚洲' if market_code == 'A' else '欧美'
    url = JSL_QDII_A_URL if market_code == 'A' else JSL_QDII_E_URL
    cells = fetch_jsl_cells(
        url,
        JSL_QDII_REFERER,
        cookie=cookie,
        page_size=page_size,
        timeout=timeout,
        source_name=f'集思录QDII-{label}',
    )
    out = []
    for c in cells:
        if exclude_bad_notes and str(c.get('notes', '') or '') == '估值有问题':
            continue
        out.append(_tag_cell(c, 'QDII', label))
    return out


def fetch_jisilu_fund_cells(
    cookie: Optional[str] = None,
    kinds: Union[str, Sequence[str], None] = 'all',
    exclude_bad_notes: bool = True,
    page_size: int = 2000,
    timeout: int = 30,
) -> List[dict]:
    """
    拉取集思录场内基金原始 cell（已打标 _variety / _market）。

    kinds: 'all' | 'etf' | 'qdii' | 'qdii_a' | 'qdii_e' 或列表组合
    """
    resolved = _normalize_kinds(kinds)
    all_cells: List[dict] = []
    if 'etf' in resolved:
        all_cells.extend(_fetch_etf_cells(cookie, page_size, timeout))
    if 'qdii_a' in resolved:
        all_cells.extend(_fetch_qdii_cells('A', cookie, page_size, timeout, exclude_bad_notes))
    if 'qdii_e' in resolved:
        all_cells.extend(_fetch_qdii_cells('E', cookie, page_size, timeout, exclude_bad_notes))
    return all_cells


def _cell_to_row(cell: dict) -> dict:
    variety = cell.get('_variety', '')
    is_etf = variety == 'ETF'
    return {
        '品种': variety,
        '市场': cell.get('_market', ''),
        '代码': str(cell.get('fund_id', '') or '').zfill(6),
        '名称': cell.get('fund_nm', ''),
        '子类型': '' if is_etf else cell.get('qtype', ''),
        '现价': cell.get('price', ''),
        '涨幅': cell.get('increase_rt', ''),
        '成交额': cell.get('volume', ''),
        '场内份额': cell.get('amount', ''),
        '场内新增': '' if is_etf else cell.get('amount_incr', ''),
        '净值': cell.get('fund_nav', ''),
        '净值日期': cell.get('nav_dt', ''),
        '估值': cell.get('estimate_value', ''),
        '估值日期': '' if is_etf else cell.get('last_est_dt', ''),
        '溢价率': cell.get('discount_rt', ''),
        '跟踪标的': cell.get('index_nm', ''),
        '指数代码': cell.get('index_id', '') if is_etf else '',
        '标的涨幅': cell.get('index_increase_rt', '') if is_etf else cell.get('ref_increase_rt', ''),
        '规模': cell.get('unit_total', '') if is_etf else '',
        '份额': cell.get('amount', '') if is_etf else '',
        'PE': cell.get('pe', '') if is_etf else '',
        'PB': cell.get('pb', '') if is_etf else '',
        '最小申赎单位': cell.get('creation_unit', '') if is_etf else '',
        '管理费': cell.get('m_fee', '') if is_etf else '',
        '申购费': '' if is_etf else cell.get('apply_fee', ''),
        '赎回费': '' if is_etf else cell.get('redeem_fee', ''),
        '托管费': cell.get('t_fee', '') if is_etf else cell.get('mt_fee', ''),
        '基金公司': cell.get('issuer_nm', ''),
        'T+0': '' if is_etf else cell.get('t0', ''),
        '_raw': cell,
    }


def cells_to_dataframe(cells: List[dict]) -> pd.DataFrame:
    """原始 cell 列表 -> 统一列 DataFrame。"""
    if not cells:
        return pd.DataFrame(columns=JISILU_FUND_COLUMNS)

    df = pd.DataFrame([_cell_to_row(c) for c in cells])
    for col in _JISILU_NUMERIC_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    df['溢价率数值'] = df['溢价率'].map(percentage2float) if '溢价率' in df.columns else float('nan')

    df = df.dropna(subset=['代码'])
    df = df[df['代码'].str.match(r'^\d{6}$', na=False)]
    df = df.drop_duplicates(subset=['品种', '市场', '代码'], keep='first').reset_index(drop=True)

    extra = [c for c in df.columns if c not in JISILU_FUND_COLUMNS and c not in ('_raw', '溢价率数值')]
    ordered = [c for c in JISILU_FUND_COLUMNS if c in df.columns]
    ordered += ['溢价率数值'] if '溢价率数值' in df.columns else []
    ordered += extra
    if '_raw' in df.columns:
        ordered.append('_raw')
    return df[ordered]


def _normalize_discount_threshold(value: Union[str, float, int]) -> float:
    if isinstance(value, str):
        if value.endswith('%'):
            return percentage2float(value)
        return float(value) / 100.0
    v = float(value)
    return v / 100.0 if abs(v) > 1 else v


def _apply_filters(
    cells: List[dict],
    index_id: str = '',
    min_volume: float = 0,
    max_discount: Optional[Union[str, float]] = None,
    min_discount: Optional[Union[str, float]] = None,
) -> List[dict]:
    out = cells
    if index_id:
        out = [
            c for c in out
            if c.get('_variety') == 'ETF' and str(c.get('index_id', '')) == str(index_id)
        ]
    if min_volume:
        out = [c for c in out if safe_float(c.get('volume')) >= float(min_volume)]
    if min_discount is not None:
        thr = _normalize_discount_threshold(min_discount)
        out = [c for c in out if percentage2float(c.get('discount_rt')) >= thr]
    if max_discount is not None:
        thr = _normalize_discount_threshold(max_discount)
        out = [c for c in out if percentage2float(c.get('discount_rt')) <= thr]
    return out


def _fund_key(cell: dict) -> str:
    return f"{cell.get('_variety')}:{cell.get('_market')}:{cell.get('fund_id')}"


def fetch_jisilu_funds(
    cookie: Optional[str] = None,
    kinds: Union[str, Sequence[str], None] = 'all',
    index_id: str = '',
    min_volume: float = 0,
    max_discount: Optional[Union[str, float]] = None,
    min_discount: Optional[Union[str, float]] = None,
    exclude_bad_notes: bool = True,
    page_size: int = 2000,
    timeout: int = 30,
    as_dict: bool = False,
) -> Union[pd.DataFrame, Dict[str, dict]]:
    """
    集思录场内基金统一入口（ETF + QDII 全量，统一表结构）。

    参数:
        cookie: 集思录 Cookie；默认读环境变量 JISILU_COOKIE
        kinds: 'all'（默认）| 'etf' | 'qdii' | 'qdii_a' | 'qdii_e' 或组合列表
        index_id: 仅对 ETF 按跟踪指数代码过滤
        min_volume: 成交额下限（万元）
        min_discount / max_discount: 溢价率区间
        exclude_bad_notes: QDII 剔除「估值有问题」
        as_dict: True 返回 {品种:市场:代码: cell}

    返回:
        统一列 DataFrame（见 JISILU_FUND_COLUMNS）或 dict
    """
    cells = fetch_jisilu_fund_cells(
        cookie=cookie,
        kinds=kinds,
        exclude_bad_notes=exclude_bad_notes,
        page_size=page_size,
        timeout=timeout,
    )
    cells = _apply_filters(
        cells,
        index_id=index_id,
        min_volume=min_volume,
        max_discount=max_discount,
        min_discount=min_discount,
    )
    if as_dict:
        return {_fund_key(c): c for c in cells if c.get('fund_id')}
    return cells_to_dataframe(cells)
