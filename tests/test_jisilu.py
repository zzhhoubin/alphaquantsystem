"""集思录统一场内基金接口测试。"""
import os

import pytest

from alphaQuantSystem.data import DataEngine, JISILU_FUND_COLUMNS, fetch_jisilu_funds


def test_jisilu_unified_columns():
    assert '品种' in JISILU_FUND_COLUMNS
    assert '市场' in JISILU_FUND_COLUMNS
    assert '溢价率' in JISILU_FUND_COLUMNS


def test_jisilu_without_cookie_limited():
    df = fetch_jisilu_funds(cookie='', kinds='etf')
    assert 1 <= len(df) <= 30
    assert list(df.columns[:3]) == ['品种', '市场', '代码']


@pytest.mark.skipif(not os.environ.get('JISILU_COOKIE'), reason='需要 JISILU_COOKIE')
def test_jisilu_all_with_cookie():
    df = fetch_jisilu_funds()
    assert len(df) > 1400
    assert set(df['品种'].unique()) >= {'ETF', 'QDII'}
    assert (df['品种'] == 'ETF').sum() > 500
    assert (df['品种'] == 'QDII').sum() > 200


@pytest.mark.skipif(not os.environ.get('JISILU_COOKIE'), reason='需要 JISILU_COOKIE')
def test_jisilu_data_engine():
    df = DataEngine().get_jisilu_funds()
    assert '溢价率数值' in df.columns
    assert 'ETF' in df['品种'].values
