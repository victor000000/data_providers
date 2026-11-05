import datetime as dt
import duckdb
import pytest
from data_providers.jobs.trades.load_schwab_csv import ACCOUNT, load_positions
from data_providers.jobs.trades.schwab_export_snapshot import store
from data_providers import MarketData


def test_old_export_reimport_does_not_become_new_observation(tmp_path):
    p=tmp_path/'PositionStatement.csv'
    p.write_text(f'Position Statement for {ACCOUNT} (Individual) on 10/20/26 21:39:12\nCRWD\n100 20 NOV 26 225 PUT,-3\n100 20 NOV 26 260 CALL,0\n')
    with duckdb.connect(str(tmp_path/'trades.duckdb')) as c:
        positions=load_positions(p,None,True)
        digest,added=store(c,p,positions,ACCOUNT)
        assert added
        first=c.execute('SELECT * FROM broker_snapshot_receipts').fetchone()
        assert first[5]==dt.datetime(2026,10,20,21,39,12)
        assert first[6:8]==(None,None)
        assert first[9:12]==(None,False,'parsed_nonzero_option_rows')
        assert store(c,p,load_positions(p,None,True),ACCOUNT)==(digest,False)
        assert c.execute('SELECT * FROM broker_snapshot_receipts').fetchone()==first
        assert c.execute('SELECT count(*) FROM broker_snapshot_positions').fetchone()[0]==1
    client=MarketData(home=tmp_path,private=True)
    assert len(client.read('broker_snapshot_receipts',start='2026-10-20',end='2026-10-20'))==1


def test_missing_or_wrong_account_header_is_rejected(tmp_path):
    p=tmp_path/'PositionStatement.csv';p.write_text('CRWD\n100 20 NOV 26 225 PUT,-3\n')
    with duckdb.connect() as c:
        with pytest.raises(ValueError):store(c,p,[],ACCOUNT)
