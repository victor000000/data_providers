from datetime import date
import pandas as pd
import pytest
from data_providers.jobs.history.build_iv_history_cn_bulk import series_from_year
from data_providers.jobs.data_ops.download_cn_option_bars import num


def frame(rows):
    return pd.DataFrame(rows, columns=['合约代码','成交量(手)','今结算','隐含波动率']).assign(交易日期='2026-10-22')


def test_comma_volume_preserves_dominant_month_and_native_percent():
    data=frame([['CF611C100',10,10,60],['CF611P100',10,11,60],
                ['CF701C100','1,000','1,000.00','20.00'],
                ['CF701P100','2,000','1,001.00','22.00']])
    result=series_from_year(data)
    assert result[0][0]==date(2026,10,22)
    assert result[0][1]==pytest.approx(0.21)
    numeric=frame([['CF611C100',10,10,60],['CF611P100',10,11,60],
                   ['CF701C100',1000,1000,20],['CF701P100',2000,1001,22]])
    assert series_from_year(data)==series_from_year(numeric)


def test_comma_settlement_preserves_nearest_parity_strike():
    data=frame([['CF701C100',100,'1,005.00',20],['CF701P100',100,'1,006.00',22],
                ['CF701C110',100,5,60],['CF701P110',100,12,60]])
    result=series_from_year(data)
    assert result[0][0]==date(2026,10,22)
    assert result[0][1]==pytest.approx(0.21)


def test_native_bar_numeric_values_are_finite_and_not_silently_missing():
    assert num(' 10,123.50 ')==10123.5
    assert num('0')==0
    for value in (None,'','--','NaN','inf','-inf'):
        assert num(value) is None
