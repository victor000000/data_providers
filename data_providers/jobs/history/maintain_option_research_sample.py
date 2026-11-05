"""Bounded SPY capability/sample collector. Never launches an all-market download."""
import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb

from data_providers.data.schema.option_research import DDL, ensure
from data_providers.lake import ident, literal
from data_providers.providers.theta_history import HistoryClient, contract_id, number, quote, utc, validate_ohlcv
from data_providers.runtime import data_home


def merge(home, stage):
    import fcntl
    lockfile=home/'var/maintenance/writer.lock'
    lockfile.parent.mkdir(parents=True,exist_ok=True)
    with lockfile.open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        with duckdb.connect(str(home/'market_history.duckdb')) as c:
            ensure(c)
            c.execute(f'ATTACH {literal(stage)} AS sample (READ_ONLY)')
            c.execute('BEGIN')
            try:
                for table in DDL:
                    c.execute(f'INSERT OR REPLACE INTO {ident(table)} SELECT * FROM sample.{ident(table)}')
                    if c.execute(f'SELECT count(*) FROM (SELECT * FROM sample.{ident(table)} EXCEPT SELECT * FROM {ident(table)})').fetchone()[0]:
                        raise ValueError('Research stage merge mismatch: '+table)
                c.execute('COMMIT')
            except Exception:
                c.execute('ROLLBACK');raise
    return {'verified':True,'retained':str(stage)}


def run(tape, *, home=None, base_url=None, run_id='theta-spy-20261018'):
    home=Path(home or data_home());folder=home/'var/maintenance'/run_id
    folder.mkdir(parents=True,exist_ok=True)
    executions=list(csv.DictReader(Path(tape).open()))
    recent=[x for x in executions if x['underlying']=='SPY' and '2024-02-01'<=x['timestamp'][:10]<='2026-09-30']
    selected={(x['symbol'],x['timestamp'][:10]):x for x in recent}
    if len(selected)>36:raise ValueError('Sample exceeds 36 contract-day budget; review estimates before scaling')
    stage=home/'staging'/('research_'+run_id+'.duckdb');stage.parent.mkdir(exist_ok=True)
    client=HistoryClient(folder,base_url);blocked={};coverage=[];rejected=[];quotes=[]
    def fetch(endpoint,params):
        if endpoint in blocked:return blocked[endpoint],[]
        receipt,rows=client.request(endpoint,params)
        if receipt['status']=='not_entitled':blocked[endpoint]=receipt
        return receipt,rows
    def insert(c,table,rows):
        if rows:c.executemany(f"INSERT OR REPLACE INTO {ident(table)} VALUES ({','.join('?' for _ in rows[0])})",rows)
    def record(c,kind,symbol,cid,session,receipt,n,invalid=0):
        status='invalid_quarantined' if invalid else 'validated' if receipt['status']=='downloaded' and n else receipt['status']
        reason=('Provider observations rejected by strict normalization' if invalid else
                'Same endpoint entitlement refusal; no repeated requests' if receipt['endpoint'] in blocked else '')
        row=(run_id,'thetadata_v3',kind,symbol,cid,session,status,n,reason,receipt.get('body_sha256'),datetime.now(UTC))
        insert(c,'research_coverage',[row]);coverage.append(dict(dataset=kind,symbol=symbol,contract_id=cid,session=session,status=status,rows=n,invalid=invalid,receipt=receipt.get('body_sha256')))
    with duckdb.connect(str(stage)) as c:
        ensure(c)
        for (_,session),x in sorted(selected.items()):
            cid=contract_id('SPY',x['expiry'],x['call_put'],x['strike'])
            base=dict(symbol='SPY',expiration=x['expiry'],strike=x['strike'],right='call' if x['call_put']=='C' else 'put',date=session.replace('-',''))
            requests=[('option_nbbo','option/history/quote',dict(base,interval='1m',start_time='09:30:00',end_time='16:15:00')),
                      ('option_oi','option/history/open_interest',base),
                      ('option_greeks','option/history/greeks/eod',dict(symbol='SPY',expiration=x['expiry'],strike=x['strike'],right=base['right'],start_date=base['date'],end_date=base['date'],version='1'))]
            for kind,endpoint,params in requests:
                receipt,rows=fetch(endpoint,params);normalized=[];bad=0
                for row in rows:
                    try:
                        if kind=='option_nbbo':
                            if contract_id(row.get('symbol','SPY'),row['expiration'],row['right'],row['strike'])!=cid:raise ValueError('Contract mismatch')
                            value=quote(row,cid=cid,symbol='SPY',session=session,receipt=receipt['body_sha256'])
                            normalized.append(value);quotes.append(value)
                        else:
                            if kind=='option_greeks':
                                iv=number(row.get('implied_vol'));oi=None
                                if iv is not None and iv<=0:raise ValueError('Invalid IV')
                            else:iv=None;oi=number(row.get('open_interest'))
                            stamp=utc(row.get('timestamp') or row['created'])
                            value=('thetadata_v3',cid,'SPY',session,kind,stamp,None,oi,iv,
                                   number(row.get('delta')),number(row.get('gamma')),number(row.get('theta')),number(row.get('vega')),
                                   'thetadata_eod_version_1' if kind=='option_greeks' else None,json.dumps(row),receipt['body_sha256'])
                            normalized.append(value)
                    except (ValueError,KeyError,TypeError) as exc:
                        bad+=1;rejected.append(dict(kind=kind,contract_id=cid,session=session,error=str(exc),row=row))
                insert(c,'option_nbbo' if kind=='option_nbbo' else 'option_research_observations',normalized)
                record(c,kind,'SPY',cid,session,receipt,len(normalized),bad)
        for session in sorted({k[1] for k in selected}):
            for kind,endpoint,params in [
                ('option_contract_history','option/list/contracts/quote',dict(symbol='SPY',date=session.replace('-',''))),
                ('underlying_minutes','stock/history/ohlc',dict(symbol='SPY',date=session.replace('-',''),interval='1m',venue='utp_cta',start_time='09:30:00',end_time='16:15:00')),
                ('underlying_daily','stock/history/eod',dict(symbol='SPY',start_date=session.replace('-',''),end_date=session.replace('-','')))]:
                receipt,rows=fetch(endpoint,params);out=[];bad=0
                for row in rows:
                    try:
                        sha=receipt['body_sha256']
                        if kind=='option_contract_history':
                            if row['symbol']!='SPY':raise ValueError('Underlying mismatch')
                            cid=contract_id(row['symbol'],row['expiration'],row['right'],row['strike'])
                            value=('thetadata_v3',cid,'SPY',session,row['expiration'],row['strike'],cid.split(':')[-2],None,None,'OPRA',None,None,None,None,None,datetime.now(UTC),'quoted_on_session;terms_and_availability_unverified',sha,json.dumps(row))
                        elif kind=='underlying_minutes':
                            start=utc(row['timestamp']);end=start+timedelta(minutes=1)
                            value=('thetadata_v3','SPY',session,start,end,None,*validate_ohlcv(row),'raw','shares','America/New_York',sha)
                        else:
                            value=('thetadata_v3','SPY',session,utc(row['created']),None,*validate_ohlcv(row),'raw','provider_eod_includes_extended_hours','shares',sha,json.dumps(row))
                        out.append(value)
                    except (ValueError,KeyError,TypeError) as exc:
                        bad+=1;rejected.append(dict(kind=kind,session=session,error=str(exc),row=row))
                table={'underlying_daily':'underlying_research_daily','underlying_minutes':'underlying_research_bars'}.get(kind,kind)
                insert(c,table,out);record(c,kind,'SPY','',session,receipt,len(out),bad)
        counts={t:c.execute(f'SELECT count(*) FROM {ident(t)}').fetchone()[0] for t in DDL}
    (folder/'quarantine.json').write_text(json.dumps(rejected,indent=2))
    (folder/'coverage.json').write_text(json.dumps(coverage,indent=2))
    quality={'counts':{k:sum(x[-2]==k for x in quotes) for k in ['one_sided','locked','crossed','two_sided_age_unknown']},'quote_rows':len(quotes),'known_quote_age_rows':0,
             'quote_age_distribution_ms':None,'policy':'Unknown originating timestamp => unknown age, never fresh; exclude locked/crossed/one-sided from executable comparisons'}
    (folder/'quote-quality.json').write_text(json.dumps(quality,indent=2))
    comparisons=[]
    for x in executions:
        cid=contract_id(x['underlying'],x['expiry'],x['call_put'],x['strike']);decision=utc(x['timestamp'])
        candidates=[q for q in quotes if q[1]==cid and q[4]<=decision]
        q=max(candidates,key=lambda q:q[4]) if candidates else None
        comparisons.append(dict(event_id=x['event_id'],contract_id=cid,decision_utc=x['timestamp'],quantity=x['quantity'],qc_fill=x['qc_price'],
            bid=q[7] if q else None,ask=q[8] if q else None,quote_age_ms=None,
            interval_offset_ms=int((decision-q[4]).total_seconds()*1000) if q else None,
            status='sampled_quote_age_unknown' if q else 'outside_source_history' if x['timestamp'][:10]<'2012-06-01' else 'not_in_bounded_sample' if x not in recent else 'not_entitled',
            source='thetadata_v3',direction='buy' if float(x['quantity'])>0 else 'sell',
            spread=(q[8]-q[7]) if q and q[8] is not None and q[7] is not None else None,
            outside_spread=None,mismatch_reason='No licensed NBBO or original-update timestamp to establish executable comparison',execution_eligible=False))
    (folder/'execution-comparison.json').write_text(json.dumps(comparisons,indent=2))
    proof=merge(home,stage)
    report=dict(run_id=run_id,status='partial' if counts['option_nbbo'] else 'blocked_quotes',counts=counts,merge=proof,
                input_sha256=hashlib.sha256(Path(tape).read_bytes()).hexdigest(),contract_days=len(selected),sessions=len({k[1] for k in selected}),
                earliest_observed_quote=None,first_complete_504_iv_window=None,
                estimates=dict(sample_option_quote_requests=len(selected),sample_upper_rows=len(selected)*406,
                               broad_quote_rows_formula='listed contract-days * session minute boundaries (normally 391 or 406)',
                               uncompressed_bytes_per_quote_estimate=300,
                               sample_uncompressed_quote_bytes_upper=len(selected)*406*300,
                               full_universe_estimate='unknown until historical inventory entitlement is available'),
                limitation='No purchase or export of QC restricted reference data; blocked datasets remain typed and empty, not manufactured')
    (folder/'report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--execution-tape',required=True);p.add_argument('--base-url');p.add_argument('--run-id',default='theta-spy-20261018')
    a=p.parse_args();run(a.execution_tape,base_url=a.base_url,run_id=a.run_id)


if __name__=='__main__':main()
