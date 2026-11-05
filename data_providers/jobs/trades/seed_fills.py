import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""seed_fills.py — load trading_log_2026_parsed.csv into fills (source='journal'). Idempotent.
Parses strike / call-put / expiry out of the instrument text (US frame "META Nov20 650 PUT",
China contract codes "MA701P2650"), so downstream tools (backfill, risk work) can use them.
"""
import argparse
import csv
import datetime as dt
import pathlib
import re

from zoneinfo import ZoneInfo

import duckdb

ROOT = bootstrap.ROOT
DB = ROOT / "trades.duckdb"
CSV = ROOT / "var" / "derived" / "trading_log_2026_parsed.csv"
UA_AC = {"IBKR": ("REPLACE_WITH_IBKR_ACCOUNT", "IBKR"), "Schwab": ("40342049SCHW", "Schwab"),
         "浙商期货": ("20101589", "浙商期货"), "IBKR/Schwab": ("", "IBKR/Schwab")}
MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
CN_EXPIRY = {"MA": dt.date(2026, 12, 11), "SC": dt.date(2026, 11, 12)}


def num(v):
    if v in (None, "", "n/a", "-"):
        return None
    m = re.search(r"-?\d+\.?\d*", str(v).replace(",", ""))
    return float(m.group()) if m else None


def parse_option(instrument):
    up = instrument.upper()
    m = re.search(r"(\d+(?:\.\d+)?)\s*(PUT|CALL)\b", up) or re.search(r"(\d+(?:\.\d+)?)([CP])\b", up)  # US frame
    if m:
        strike = float(m.group(1))
        cp = "P" if "P" in m.group(2) else "C"
        mo = re.search(r"(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s*(\d{1,2})(?!\d)", up)
        expiry = dt.date(2026, MON.index(mo.group(1)) + 1, int(mo.group(2))) if mo else None
        return strike, cp, expiry
    m = re.match(r"^([A-Z]+\d+)-?([CP])-?(\d+(?:\.\d+)?)$", up)    # China code
    if m:
        return float(m.group(3)), m.group(2), CN_EXPIRY.get(re.match(r"^[A-Z]+", m.group(1)).group())
    return None, None, None


# A bare CN contract code: product+month, then C/P, then strike, e.g. MA701C3350,
# MO2610-P-7400, sc2612P570. Anchored and space-free on purpose: "SPY 261016P00745000"
# and "BRK.B Nov20 400P" must not match, and they cannot, because digits must follow the
# letters immediately.
CN_CODE = re.compile(r"^([A-Za-z]+\d{3,4})-?([CP])-?(\d+(?:\.\d+)?)$")


def underlying_of(instrument):
    """The UNDERLYING, which for a CN code is not the first token of the contract.

    For the US frame the first whitespace token is the underlying ("META Nov20 850C" ->
    META). For a CN code it is the whole contract: "MA701C3350".split()[0] is
    "MA701C3350", so 13 journal rows were stored under a contract code while `positions`
    keys the same leg as "MA701" -- two names for one contract that can never meet, which
    is why selfcheck's reconcile saw MA701 traded and MA701 held as unrelated rows.
    """
    m = CN_CODE.match((instrument or "").upper())
    if m:
        return m.group(1)
    return instrument.split()[0].rstrip('"') if instrument else None


def main():
    ap = argparse.ArgumentParser(
        description="Load the parsed journal CSV into fills (source='journal').")
    ap.add_argument("--dry-run", action="store_true",
                    help="parse and report, write nothing")
    args = ap.parse_args()

    con = duckdb.connect(str(DB))
    # NOT idempotent-by-upsert: this is a DELETE-then-INSERT refresh of one source.
    # That is deliberate (the CSV is the authority for source='journal'), but it means
    # ANY invocation rewrites those rows -- which is why it now takes an argument.
    # Without argparse, `python seed_fills.py --help` ran the whole refresh: an
    # apparently read-only flag performed a destructive write. That actually happened
    # on 2026-10-05 during a script sweep and rewrote 23 journal fills as 34.
    # ── Carry over the columns the CSV does NOT contain ──────────────────────────
    # The refresh is DELETE-then-INSERT, so every column this loader does not write is
    # destroyed on those rows -- including ones another loader filled in. That is not
    # hypothetical: load_cn_settlements.py sets `margin_local` on CN fills from the
    # 结算单 持仓明细, and a re-seed of the journal wiped it. reference.duckdb then lost
    # MA701 and SC2612 from futures_contracts (3 rows -> 1), because that table's only
    # input is `median(margin_local/qty)` over fills -- observed margins the broker
    # actually charged, which no re-download can recover. The CSV remains the authority
    # for everything it HAS; the refresh just stops being a demolition of everything it
    # does not.
    carried = []
    if not args.dry_run:
        con.execute("""CREATE OR REPLACE TEMP TABLE _carry AS
            SELECT CAST(ts_utc AS DATE) AS d, lower(symbol) AS s, qty, price,
                   margin_local, margin_currency
            FROM fills WHERE source='journal' AND margin_local IS NOT NULL""")
        carried = con.execute("SELECT count(*) FROM _carry").fetchone()[0]
        con.execute("DELETE FROM fills WHERE source='journal'")
    # Executions the Schwab transaction history already holds. The journal records the
    # user's own notes, and those notes include Schwab trades -- so the same execution
    # arrived twice under two source labels (CRWD Nov20 330C and META Nov20 850C, same
    # account, qty, price and timestamp). Every sum over `fills` then counted it twice,
    # and `positions` -- built from broker snapshots -- disagreed with the book it was
    # meant to reconcile against. The transaction history is the authority for Schwab
    # (FIFO-reconciled, complete), so an identical journal row is redundant.
    #
    # The key is exact on all five fields: a journal row that merely resembles a Schwab
    # one, or that records a trade the history missed, is still inserted.
    schwab = set(con.execute("""SELECT account, symbol, qty, price, ts_utc, action
                                FROM fills WHERE source='schwab_transactions'""").fetchall())
    n, unknown_date, dup_of_schwab = 0, 0, 0
    with open(CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            acct, broker = UA_AC.get(row["account"], (row["account"], row["account"]))
            ts = None
            if row["date"] and row["date"].startswith("2026"):
                t = (row["time"] or "00:00:00").split()[0]
                if len(t) == 5:
                    t += ":00"
                try:
                    ts = dt.datetime.strptime(row["date"][:10] + " " + t, "%Y-%m-%d %H:%M:%S")
                    # A real timezone, not a fixed offset. The old code added a flat
                    # +4h for US brokers: correct in EDT, one hour wrong from
                    # 2026-11-01 when EST begins, silently mis-timing every fill.
                    if broker == "浙商期货":
                        ts -= dt.timedelta(hours=8)          # CST
                    elif broker in ("IBKR", "Schwab"):
                        ts = ts.replace(tzinfo=ZoneInfo("America/New_York")) \
                               .astimezone(dt.timezone.utc).replace(tzinfo=None)
                except ValueError:
                    ts = None
            else:
                unknown_date += 1
            instrument = row["instrument"] or ""
            if instrument.upper().startswith("TOTAL") or row["action"] in ("ROLL", "-"):
                continue
            strike, cp, expiry = parse_option(instrument)
            key = (acct, instrument or None, int(num(row["lots"]) or 0),
                   num(row["price"]), ts, row["action"])
            if key in schwab:
                dup_of_schwab += 1
                continue
            con.execute("""INSERT INTO fills (ts_utc, ts_orig, account, broker, underlying, symbol,
            security_type, expiry, strike, call_put, action, open_close, qty, price, fee,
            realized_pnl, exchange, currency, source, notes)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [ts, (row["date"] + " " + (row["time"] or "")).strip(), acct, broker,
                 underlying_of(instrument), instrument or None,
                 "COMBO" if "/" in instrument else "OPT", expiry, strike, cp,
                 row["action"], row["open_close"], int(num(row["lots"]) or 0),
                 num(row["price"]), num(row["fee"]), num(row["realized_pnl"]),
                 row["exchange"] or None, "CNY" if broker == "浙商期货" else "USD",
                 "journal", row["note"] or None])
            n += 1
    # Re-attach the carried columns to the freshly inserted rows. Matching on the natural
    # key (day, symbol, qty, price) rather than fill_id, which the delete invalidated.
    # `margin_local IS NULL` so a value the CSV somehow supplies still wins.
    if carried:
        con.execute("""UPDATE fills SET margin_local=cc.margin_local,
                                         margin_currency=cc.margin_currency
                       FROM (SELECT DISTINCT ON (d, s, qty, price) d, s, qty, price,
                                    margin_local, margin_currency FROM _carry) cc
                       WHERE fills.source='journal' AND fills.margin_local IS NULL
                         AND CAST(fills.ts_utc AS DATE)=cc.d AND lower(fills.symbol)=cc.s
                         AND fills.qty=cc.qty AND fills.price=cc.price""")
        back = con.execute("""SELECT count(*) FROM fills WHERE source='journal'
                              AND margin_local IS NOT NULL""").fetchone()[0]
        print(f"  carried margin_local across the refresh on {back} of {carried} row(s)"
              + ("" if back == carried else "  !! SOME WERE NOT RE-ATTACHED — the fill key "
                 "(day, symbol, qty, price) no longer matches; inspect before trusting "
                 "futures_contracts"))
    con.close()
    verb = "would seed" if args.dry_run else "seeded"
    print(f"{verb} {n} fills from journal -> trades.duckdb")
    if dup_of_schwab:
        print(f"  skipped {dup_of_schwab} row(s) the Schwab transaction history already "
              f"holds (same account, symbol, qty, price and timestamp) — inserting them "
              f"would count the same execution twice")
    if unknown_date:
        print(f"  {unknown_date} row(s) have no source date; stored with ts_utc NULL and the "
              f"original text kept in ts_orig (the journal itself records 'unknown'). "
              f"selfcheck treats those as explained, not missing.")


# Guard added by the data/application split: unguarded, importing this module ran it.
if __name__ == "__main__":
    main()
