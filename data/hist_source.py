"""
历史 K 线数据来源：解析用户配置与生成尝试顺序（含环形降级）。
"""
from __future__ import annotations
from typing import Dict, List, Optional

ORDERED_CHAIN: List[str] = ['qmt', 'eastmoney', 'akshare', 'tdx']
LEGACY_CHAIN: List[str] = ['eastmoney', 'qmt', 'akshare', 'tdx']
HIST_SOURCE_DISPLAY: Dict[str, str] = {'qmt': 'QMT', 'eastmoney': '东方财富', 'akshare': 'AkShare', 'tdx': '通达信'}


def _normalize_token(raw: str) -> str:
    s = raw.strip()
    if not s:
        return ''
    lower = s.lower()
    aliases = {'qmt': 'qmt', 'eastmoney': 'eastmoney', 'em': 'eastmoney', '东方财富': 'eastmoney', 'akshare': 'akshare',
               'tdx': 'tdx', '通达信': 'tdx', '按顺序': 'ordered', 'sequential': 'ordered', 'ordered': 'ordered',
               'auto': 'legacy', 'default': 'legacy', 'legacy': 'legacy', 'none': 'legacy'}
    if s in aliases:
        return aliases[s]
    if lower in aliases:
        return aliases[lower]
    if lower in ORDERED_CHAIN:
        return lower
    return lower


def resolve_hist_source_chain(source: Optional[str]) -> List[str]:
    """
    根据 source 参数返回本次应依次尝试的数据源键列表。

    - None / 空 / auto / legacy：LEGACY_CHAIN（东财优先）。
    - 按顺序 / ordered / sequential：ORDERED_CHAIN（QMT 优先）。
    - qmt / eastmoney / akshare / tdx：从该源起环形遍历 ORDERED_CHAIN。
    """
    if source is None:
        return list(LEGACY_CHAIN)
    token = _normalize_token(str(source))
    if not token or token == 'legacy':
        return list(LEGACY_CHAIN)
    if token == 'ordered':
        return list(ORDERED_CHAIN)
    if token not in ORDERED_CHAIN:
        raise ValueError(
            f'未知 hist_data_source: {source!r}，可选: qmt / eastmoney / akshare / tdx / 按顺序，或不传（东财优先链路）')
    idx = ORDERED_CHAIN.index(token)
    return ORDERED_CHAIN[idx:] + ORDERED_CHAIN[:idx]


__all__ = ['ORDERED_CHAIN', 'LEGACY_CHAIN', 'HIST_SOURCE_DISPLAY', 'resolve_hist_source_chain']


def test_hist_source_chain() -> None:
    assert resolve_hist_source_chain(None) == list(LEGACY_CHAIN)
    assert resolve_hist_source_chain('ordered') == list(ORDERED_CHAIN)
    assert resolve_hist_source_chain('eastmoney')[0] == 'eastmoney'