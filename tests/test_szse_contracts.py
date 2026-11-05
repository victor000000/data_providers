from datetime import date, datetime

import duckdb
import pytest

from data_providers.data import symbols
from data_providers.data.schema.cn_option_contracts import ensure
from data_providers.providers.szse_contracts import parse
from data_providers.provenance import source_descriptor, tag_row


@pytest.fixture
def record(monkeypatch):
    monkeypatch.setattr(symbols, '_MAP', {'CN:SZSE:159901': {'szse': '159901'}})
    monkeypatch.setattr(symbols, '_REVERSE', None)
    return {'合约编码':'90007393','合约代码':'159901C2612M003500',
            '标的证券简称(代码)':'深证100ETF(159901)','交易日期':'2026-11-02',
            '到期日':'2026-12-23','最后交易日':'2026-12-23','行权日':'2026-12-23',
            '交收日':'2026-12-24','合约类型':'认购','行权价':'3.5000',
            '合约单位':'10000','原合约代码':'159901C2612M003500','原行权价格':'3.5000',
            '原合约单位':'10000','合约调整':'否','前结算价':'0.0054','合约总持仓':'403.00'}


def read(records):
    return parse(records, receipt_path='receipt.json', raw_sha256='a'*64,
                 received_at=datetime(2026,10,30,23,18))


def test_report_date_native_expiry_and_unknown_clocks_remain_distinct(record):
    row = read([record])[0]
    assert row['report_date'] == date(2026,11,2)
    assert row['received_at'].date() == date(2026,10,30)
    assert row['expiry'] == date(2026,12,23)
    assert row['previous_settlement'] == .0054
    assert row['previous_settlement_date'] is None
    assert row['source_event_utc'] is row['available_at'] is None
    with duckdb.connect() as c:
        ensure(c)
        columns = [r[0] for r in c.execute('describe option_contract_snapshots').fetchall()]
        c.execute('insert into option_contract_snapshots values ('+','.join('?' for _ in columns)+')',
                  [row[key] for key in columns])
        stored = dict(zip(columns,c.execute('select * from option_contract_snapshots').fetchone()))
    tagged=tag_row(stored,descriptor=source_descriptor('cn','option_contract_snapshots',columns))
    assert tagged['datasource_tag']=='szse_current_contract_report_v1'
    assert tagged['source_receipt_reference']=='receipt.json'


def test_adjusted_identity_and_actual_strike_not_decoded_from_name(record):
    record.update({'合约代码':'159901C2612A003500','行权价':'3.421','合约单位':'10231','合约调整':'是'})
    row=read([record])[0]
    assert row['contract_code'].endswith('A003500')
    assert row['original_contract_code'].endswith('M003500')
    assert row['strike']==3.421 and row['original_strike']==3.5
    assert row['multiplier']==10231


@pytest.mark.parametrize('change',[
    {'标的证券简称(代码)':'未知(159999)','合约代码':'159999C2612M003500'},
    {'行权价':'nan'}, {'合约单位':'1.5'}, {'到期日':'2026-10-01'},
    {'合约代码':'159915C2612M003500'}, {'合约类型':'未知'},
])
def test_bad_or_unmapped_specs_fail_before_write(record,change):
    record.update(change)
    with pytest.raises((ValueError,symbols.SymbolError)):
        read([record])


def test_duplicates_fail_closed(record):
    with pytest.raises(ValueError,match='Duplicate'):
        read([record,record])
