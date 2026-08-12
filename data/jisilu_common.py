"""集思录 jqGrid 列表接口共用逻辑（Cookie、翻页、解析）。"""
from __future__ import annotations

import math
import os
import time
from typing import Any, List, Optional, Union

import requests
from loguru import logger

JSL_COOKIE_ENV = 'JISILU_COOKIE'
_DEFAULT_PAGE_SIZE = 2000
_MAX_PAGES = 200


def resolve_cookie(cookie: Optional[str]) -> Optional[str]:
    if cookie:
        return cookie.strip() or None
    env = os.environ.get(JSL_COOKIE_ENV, '').strip()
    return env or None


def jsl_headers(cookie: Optional[str], referer: str) -> dict:
    headers = {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
        ),
        'Referer': referer,
        'Origin': 'https://www.jisilu.cn',
        'X-Requested-With': 'XMLHttpRequest',
        'Accept': 'application/json, text/javascript, */*; q=0.01',
    }
    if cookie:
        headers['Cookie'] = cookie.strip()
    return headers


def fetch_jsl_list_page(
    list_url: str,
    referer: str,
    page: int,
    page_size: int,
    cookie: Optional[str],
    timeout: int = 30,
) -> dict:
    params = {
        '___jsl': f'LST___t={int(time.time() * 1000)}',
        'rp': str(page_size),
        'page': str(page),
    }
    r = requests.get(
        list_url,
        params=params,
        headers=jsl_headers(cookie, referer),
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()


def fetch_jsl_cells(
    list_url: str,
    referer: str,
    cookie: Optional[str] = None,
    page_size: int = _DEFAULT_PAGE_SIZE,
    timeout: int = 30,
    id_key: str = 'fund_id',
    source_name: str = '集思录',
) -> List[dict]:
    """
    拉取集思录 jqGrid 列表全部 cell（尽量全量）。

    id_key: cell 内用作去重的字段，默认 fund_id。
    """
    ck = resolve_cookie(cookie)
    all_cells: List[dict] = []
    seen: set = set()
    expected_all: Optional[int] = None
    prev_first_id: Optional[str] = None

    for page in range(1, _MAX_PAGES + 1):
        try:
            payload = fetch_jsl_list_page(
                list_url, referer, page, page_size, ck, timeout=timeout,
            )
        except Exception as e:
            logger.error('{} 第 {} 页请求失败: {}', source_name, page, e)
            break

        rows = payload.get('rows') or []
        if not rows:
            break

        if expected_all is None and payload.get('all') is not None:
            try:
                expected_all = int(payload['all'])
            except (TypeError, ValueError):
                pass

        new_count = 0
        first_id = None
        for row in rows:
            cell = row.get('cell') or {}
            fid = str(cell.get(id_key, '') or row.get('id', '') or '').strip()
            if not fid:
                continue
            if first_id is None:
                first_id = fid
            if fid in seen:
                continue
            seen.add(fid)
            all_cells.append(cell)
            new_count += 1

        if page > 1 and (new_count == 0 or first_id == prev_first_id):
            break
        prev_first_id = first_id

        if expected_all and len(all_cells) >= expected_all:
            break
        if not ck:
            break
        if expected_all is None and len(rows) >= 50:
            break
        pages_needed = math.ceil(expected_all / max(page_size, 1)) if expected_all else 1
        if page >= pages_needed:
            break

    if not ck and len(all_cells) <= 30:
        logger.warning(
            '未设置集思录 Cookie（{}），{} 可能仅返回 {} 条',
            JSL_COOKIE_ENV,
            source_name,
            len(all_cells),
        )
    else:
        logger.info('{} 共获取 {} 条', source_name, len(all_cells))

    return all_cells


def percentage2float(per: Union[str, float, int, None]) -> float:
    if per is None:
        return float('nan')
    if isinstance(per, (int, float)):
        return float(per)
    s = str(per).strip()
    if not s or s in ('-', '--', '---'):
        return float('nan')
    if s.endswith('%'):
        return float(s[:-1]) / 100.0
    v = float(s)
    return v / 100.0 if abs(v) > 1 else v


def safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
