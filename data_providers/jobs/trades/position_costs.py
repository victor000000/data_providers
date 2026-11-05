"""Persist entry prices that the broker's holdings export actually reports."""
import datetime as dt
import math
import csv
import pathlib
import re

from data_providers.data.schema import broker_costs


def save_tws(con, positions, stamp):
    rows = []
    for p in positions:
        c = p.contract
        if c.secType != "OPT" or not p.position:
            continue
        text = (c.lastTradeDateOrContractMonth or "").strip()
        if len(text) < 8 or not text[:8].isdigit():
            continue
        multiplier = float(c.multiplier or 100)
        if multiplier <= 0:
            continue
        price = float(p.avgCost) / multiplier
        if price <= 0 or not math.isfinite(price):
            continue
        expiry = dt.datetime.strptime(text[:8], "%Y%m%d").date()
        rows.append([stamp,p.account,"IBKR",c.symbol,expiry,float(c.strike),
                     c.right,float(p.position),price,"TWS avgCost / multiplier"])
    broker_costs.write(con, rows)


def statement_day(path):
    with open(path, encoding="utf-8-sig") as handle:
        title = next(csv.reader(handle), [""])[0]
    match = re.search(r"\bon (\d{1,2}/\d{1,2}/\d{2})\b", title)
    return (dt.datetime.strptime(match[1], "%m/%d/%y").date() if match
            else dt.date.fromisoformat(pathlib.Path(path).name[:10]))


def write_schwab(con, fills, orders, positions, summary, option_rows, source, day=None):
    from data_providers.jobs.trades.schwab_writes import write_account
    con.execute("BEGIN")
    try:
        result = write_account(con, fills, orders, positions, summary, day=day)
        save_schwab(con, option_rows, positions, source)
        con.execute("COMMIT")
        return result
    except BaseException:
        con.execute("ROLLBACK")
        raise


def save_schwab(con, option_rows, positions, source):
    from data_providers.jobs.trades import load_schwab_csv as sw
    prices = {}
    for row in option_rows:
        if len(row) < 7:
            continue
        expiry, strike, price = sw.parse_expiry(row[2]),sw.money(row[3]),sw.money(row[6])
        cp = row[4].strip().upper()[:1]
        if expiry and strike and price and math.isfinite(price) and price > 0:
            prices[(row[0].strip(),expiry,strike,cp)] = price
    rows = []
    for p in positions:
        price = prices.get((p[3],p[6],p[7],p[8]))
        if price is not None:
            rows.append([p[0],p[1],p[2],p[3],p[6],p[7],p[8],p[10],price,source])
    broker_costs.write(con, rows)
