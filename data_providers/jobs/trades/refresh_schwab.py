"""Refresh supported Schwab exports when present; source files stay authoritative."""
import argparse
import subprocess
import sys

from data_providers.runtime import trading_logs_dir


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    source = trading_logs_dir()
    if not source.is_dir():
        raise FileNotFoundError(f"Trading export source is unavailable: {source}")
    commands = []
    if list(source.glob('*AccountStatement.csv')):
        commands.append(('load_schwab_account', ['--dir', str(source)]))
    if list(source.glob('*PositionStatement.csv')) or list(source.glob('*TradeActivity.csv')):
        commands.append(('load_schwab_csv', ['--dir', str(source)]))
    orders = list(source.glob('*OrderBook*.csv'))
    if orders:
        latest = max(orders, key=lambda p: (p.stat().st_mtime_ns, p.name))
        commands.append(('load_schwab_orders', [str(latest)]))
    failures = 0
    for module, args in commands:
        result = subprocess.run([sys.executable, '-m', 'data_providers.jobs.trades.' + module, *args])
        failures += result.returncode != 0
    if not commands:
        print('No supported Schwab exports in the mounted source')
    return int(bool(failures))


if __name__ == '__main__':
    raise SystemExit(main())
