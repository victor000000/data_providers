"""Derive ETF ingestion scope from the maintained reference rosters, without strategy filters."""
import argparse
import csv
import json
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

from data_providers.lake import connect_store
from data_providers.runtime import data_home


def build(home):
    home=Path(home)
    with connect_store(home,'universe',backend='duckdb') as con:
        rows=con.execute("SELECT DISTINCT symbol FROM index_membership WHERE index_slug IN ('etf311','etf500') AND status='Current' ORDER BY symbol").fetchall()
    symbols=[r[0] for r in rows]
    if not symbols:
        raise ValueError('ETF source rosters are empty; existing universe retained')
    supplement=list(csv.DictReader(files('data_providers.seed').joinpath('volatility_etps.csv').read_text().splitlines()))
    symbols=sorted(set(symbols)|{r['symbol'] for r in supplement})
    path=home/'plans/universe_etf_options.txt'
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp')
    temporary.write_text('# Current ETF311 + ETF500 + explicit volatility ETP reference supplement\n'+'\n'.join(symbols)+'\n')
    temporary.replace(path)
    manifest={'symbols':len(symbols),'rosters':['etf311','etf500'],'built_utc':datetime.now(UTC).isoformat(),'supplement':supplement}
    path.with_suffix('.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return path,symbols


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    path,symbols=build(data_home())
    print(json.dumps({'path':str(path),'symbols':len(symbols)}))


if __name__=='__main__':
    main()
