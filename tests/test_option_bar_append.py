from datetime import date as D
import json
import pytest
from data_providers.jobs.history.option_bar_append import plan_tail, claim_request


def contract(symbol='TEST261120C00100000'):
    return dict(source='alpaca', symbol=symbol, underlying='TEST', strike=100., call_put='C', expiry=D(2026,11,20), last_stored_day=D(2026,10,16))


def test_existing_expiry_appends_only_new_completed_dates():
    tasks=plan_tail([contract()], [D(2026,10,16),D(2026,10,19),D(2026,10,20)], attempted_through=D(2026,10,16))
    assert [t['params']['start'] for t in tasks]==['2026-10-19','2026-10-20']


@pytest.mark.parametrize('status',['downloaded','empty','failed','in_flight','uncertain'])
def test_any_receipt_status_blocks_overlapping_windows(status):
    c=contract();r=dict(status=status,params=dict(source='alpaca',symbol=c['symbol'],start='2026-10-17',end_exclusive='2026-10-21'))
    assert not plan_tail([c],[D(2026,10,19),D(2026,10,20)],attempted_through=D(2026,10,16),receipts=[r])


def test_denial_blocks_later_dates_and_global_frontier_protects_old_unknown_attempts():
    c=contract();r=dict(status='denied',params=dict(source='alpaca',symbol=c['symbol'],start='2026-10-17',end_exclusive='2026-10-20'))
    assert not plan_tail([c],[D(2026,10,21)],attempted_through=D(2026,10,16),receipts=[r])
    assert not plan_tail([c],[D(2026,10,21)],attempted_through=D(2026,10,23))


def test_no_past_expiry_or_duplicate_task_and_claim_is_exclusive(tmp_path):
    c=contract();tasks=plan_tail([c,c],[D(2026,10,19),D(2026,10,19),D(2026,11,21)],attempted_through=D(2026,10,16));assert len(tasks)==1
    path=claim_request(tmp_path,tasks[0]);assert json.loads(path.read_text())['status']=='in_flight'
    with pytest.raises(FileExistsError):claim_request(tmp_path,tasks[0])
    assert not plan_tail([c],[D(2026,10,19)],attempted_through=D(2026,10,16),receipts=[json.loads(path.read_text())])

from data_providers.jobs.history.option_bar_append import acquire_task


def task():
    return plan_tail([contract()],[D(2026,10,19)],attempted_through=D(2026,10,16))[0]


def test_request_intent_precedes_fetch_and_response_is_retained(tmp_path):
    calls=[]
    def fetch(params):
        intent=json.loads((tmp_path/(task()['key']+'.json')).read_text())
        assert intent['status']=='in_flight'
        calls.append(params)
        return {'bars': {params['symbol']: [{'t':'2026-10-19T04:00:00Z','o':1,'h':2,'l':1,'c':2,'v':3}]}}
    r=acquire_task(tmp_path,task(),fetch)
    assert r['status']=='downloaded' and r['rows']==1 and len(calls)==1
    with pytest.raises(FileExistsError):acquire_task(tmp_path,task(),fetch)
    assert len(calls)==1


@pytest.mark.parametrize('payload',[
    {}, {'bars': {}, 'next_page_token':'another'}, {'bars': {'WRONG': []}},
    {'bars': {contract()['symbol']: [{'t':'2026-10-20T04:00:00Z','o':1,'h':2,'l':1,'c':2,'v':3}]}},
    {'bars': {contract()['symbol']: [{'t':'2026-10-19T04:00:00Z','o':3,'h':2,'l':1,'c':2,'v':3}]}}
])
def test_invalid_response_preserved_and_never_replayed(tmp_path,payload):
    r=acquire_task(tmp_path,task(),lambda _:payload)
    assert r['status']=='failed_no_replay' and r['raw_sha256']
    assert not plan_tail([contract()],[D(2026,10,19)],attempted_through=D(2026,10,16),receipts=[r])


def test_empty_denied_and_transport_failure_are_distinct(tmp_path):
    assert acquire_task(tmp_path/'empty',task(),lambda _:{'bars':None})['status']=='empty'
    class Denied(Exception):
        response=type('Response',(),{'status_code':403})()
    def deny(_):raise Denied()
    assert acquire_task(tmp_path/'denied',task(),deny)['status']=='denied'
    def timeout(_):raise TimeoutError()
    assert acquire_task(tmp_path/'timeout',task(),timeout)['status']=='failed_no_replay'

from data_providers.jobs.history.option_bar_append import stage_receipt, merge_stage
import duckdb


def test_response_to_stage_to_canonical_and_conflict_preservation(tmp_path):
    c={**contract(),'underlying':'TEST','strike':100.,'call_put':'C'}
    payload={'bars':{c['symbol']:[{'t':'2026-10-19T04:00:00Z','o':1,'h':2,'l':1,'c':2,'v':3}]}}
    r=acquire_task(tmp_path/'receipts',task(),lambda _:payload)
    stage=tmp_path/'stage.duckdb';assert stage_receipt(stage,r,c)==1
    (tmp_path/'var/maintenance').mkdir(parents=True)
    with duckdb.connect(str(stage),read_only=True) as db:
        ddl=db.execute("select sql from duckdb_tables() where table_name='option_bars'").fetchone()[0]
    with duckdb.connect(str(tmp_path/'market_history.duckdb')) as db:db.execute(ddl)
    assert merge_stage(tmp_path,stage)['new_rows']==1
    assert merge_stage(tmp_path,stage)['new_rows']==0
    with duckdb.connect(str(stage)) as db:db.execute('update option_bars set close=1.5')
    with pytest.raises(ValueError,match='conflict'):merge_stage(tmp_path,stage)
    with duckdb.connect(str(tmp_path/'market_history.duckdb'),read_only=True) as db:
        assert db.execute('select close from option_bars').fetchone()[0]==2

from data_providers.jobs.history.option_bar_append import run_append
import hashlib


def test_runner_requires_audited_history_and_stops_on_first_refusal(tmp_path):
    with pytest.raises(ValueError,match='audit'):
        run_append(tmp_path,{'market':'US','source':'alpaca'})
    evidence=tmp_path/'audit.json';evidence.write_text('{}');proof={'path':str(evidence),'sha256':hashlib.sha256(evidence.read_bytes()).hexdigest()}
    admission={'market':'US','source':'alpaca','legacy_request_audit_complete':True,
               'request_audit':proof,'completed_session_evidence':proof,
               'contracts':[{**contract(),'expiry':'2026-11-20','last_stored_day':'2026-10-16'}],
               'completed_sessions':['2026-10-19','2026-10-20'],'attempted_through':'2026-10-16','blocked_symbols':[]}
    with duckdb.connect(str(tmp_path/'market_history.duckdb')) as db:
        db.execute('CREATE TABLE option_bars(source VARCHAR,symbol VARCHAR,underlying VARCHAR,expiry DATE,strike DOUBLE,call_put VARCHAR,day DATE)')
        db.execute('INSERT INTO option_bars VALUES (?,?,?,?,?,?,?)',['alpaca',contract()['symbol'],'TEST',D(2026,11,20),100.,'C',D(2026,10,16)])
    calls=[]
    class Denied(Exception):response=type('Response',(),{'status_code':403})()
    def fetch(p):calls.append(p);raise Denied()
    result=run_append(tmp_path,admission,execute=True,fetch=fetch)
    assert result['requests']==1 and result['status']=='stopped_source_response'
    result=run_append(tmp_path,admission,execute=True,fetch=fetch)
    assert result['status']=='blocked_prior_refusal' and len(calls)==1


def test_successful_response_recovers_after_crash_before_stage_without_network(tmp_path):
    # Seed the receipt as though the process stopped just after receiving data.
    ledger=tmp_path/'var/maintenance/option-daily-append'
    acquire_task(ledger,task(),lambda p:{'bars':{p['symbol']:[{'t':'2026-10-19T04:00:00Z','o':1,'h':2,'l':1,'c':2,'v':3}]}})
    c=contract();seed=tmp_path/'seed.duckdb'
    receipt=json.loads((ledger/(task()['key']+'.json')).read_text())
    stage_receipt(seed,receipt,c)
    with duckdb.connect(str(seed),read_only=True) as db:
        ddl=db.execute("select sql from duckdb_tables() where table_name='option_bars'").fetchone()[0]
    with duckdb.connect(str(tmp_path/'market_history.duckdb')) as db:
        db.execute(ddl)
        db.execute('INSERT INTO option_bars VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',['alpaca',c['symbol'],'TEST',c['expiry'],100.,'C',D(2026,10,16),1,2,1,2,3,None])
    evidence=tmp_path/'audit.json';evidence.write_text('{}');proof={'path':str(evidence),'sha256':hashlib.sha256(evidence.read_bytes()).hexdigest()}
    admission={'market':'US','source':'alpaca','legacy_request_audit_complete':True,'request_audit':proof,'completed_session_evidence':proof,
               'contracts':[{**c,'expiry':'2026-11-20','last_stored_day':'2026-10-16'}],
               'completed_sessions':['2026-10-19'],'attempted_through':'2026-10-16','blocked_symbols':[]}
    def forbidden(_):raise AssertionError('Successful request was replayed')
    result=run_append(tmp_path,admission,execute=True,fetch=forbidden)
    assert result['requests']==0 and result['recovered_cached_rows']==1 and result['merge']['new_rows']==1
    assert run_append(tmp_path,admission,execute=True,fetch=forbidden)['merge']['new_rows']==0
