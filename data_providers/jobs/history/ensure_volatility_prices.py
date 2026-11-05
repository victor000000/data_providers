"""Seed missing volatility ETP histories before the normal price refresh and IV jobs."""
import argparse
import csv
import subprocess
import sys
from importlib.resources import files

from data_providers.warehouse import config


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    symbols=[r['symbol'] for r in csv.DictReader(files('data_providers.seed').joinpath('volatility_etps.csv').read_text().splitlines())]
    from pathlib import Path
    missing=[s for s in symbols if any(not (Path(folder)/(s+'.csv')).is_file() for folder in (config.RAW,config.ADJUSTED))]
    if not missing:
        print('Volatility ETP price files exist; normal price maintenance refreshes them')
        return 0
    result=subprocess.run([sys.executable,'-m','data_providers.warehouse.36_refetch_symbols',*missing],check=False)
    if result.returncode:return result.returncode
    unresolved=[s for s in missing if any(not (Path(folder)/(s+'.csv')).is_file() for folder in (config.RAW,config.ADJUSTED))]
    if unresolved:
        print('Missing source price histories remain:',','.join(unresolved))
        return 2
    return subprocess.run([sys.executable,'-m','data_providers.warehouse.37_fetch_actions',*missing],check=False).returncode


if __name__=='__main__':raise SystemExit(main())
