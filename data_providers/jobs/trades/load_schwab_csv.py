import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401
from data_providers.shared import helpguard
helpguard.handle_help(__file__)
"""load_schwab_csv.py — Schwab CSV exports into fills and dated export snapshots.

Schwab has no API route here (TWS covers IBKR, nothing covers Schwab), so the user
exports by hand. The exports come in two flavours and only one is usable: the PDFs are
images with no text layer, and reading them by eye produced real errors -- a Dec-18 43
PUT was read as a 43 CALL four times, which inverts the exposure from downside to
upside. The .csv export of the SAME report is text. Always prefer it.

  *PositionStatement.csv -> broker_snapshot_receipts / broker_snapshot_positions
  *TradeActivity.csv     -> fills, source='schwab_csv'

    uv run python apps/trade_log/load_schwab_csv.py [--dir PATH] [--dry-run]
"""
import argparse, csv, datetime as dt, pathlib, re, sys
import duckdb

ROOT = bootstrap.ROOT
DB = ROOT / "trades.duckdb"
SOURCE = "schwab_csv"
ACCOUNT = "40342049SCHW"
MON = {m: i+1 for i, m in enumerate(
    ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"])}


def money(v):
    """A number out of a statement cell. Sub-dollar prices have no leading zero (`.27`),
    and a pattern needing a digit before the point matched at the `2` and returned 27.0."""
    if v in (None, "", "-"): return None
    s = str(v)
    # Parentheses are the minus sign: buying power prints as `($233.33)`, positive it
    # reads as headroom the account does not have.
    neg = s.strip().startswith("(")
    m = re.search(r"[-+]?(?:\d+\.?\d*|\.\d+)", s.replace("$", "").replace(",", ""))
    if not m: return None
    x = float(m.group().replace(",", ""))
    return -abs(x) if neg else x


def parse_expiry(s):
    m = re.match(r"(\d{1,2})\s+([A-Z]{3})\s+(\d{2})", (s or "").strip().upper())
    return dt.date(2000+int(m.group(3)), MON[m.group(2)], int(m.group(1))) if m else None


def session_offset(path, header):
    """The exporting session's UTC offset, recovered from the CSV rather than assumed.

    Read the header: it states the wall-clock time at which the report was generated,
    and the file's mtime is the true instant it was written. Their difference IS the
    session's offset -- no timezone needs to be guessed, and it stays right if the box
    or the user moves. Rounded to 15 min because every real offset is a multiple of it.

    Why not `datetime.now().astimezone()`: bootstrap forces TZ=UTC in-process, so that
    returns UTC and any conversion written with it becomes a SILENT NO-OP. That is not
    hypothetical -- it is what this function replaces, and it is why the obvious version
    of this code stored local wall-clock time as if it were UTC.
    """
    m = re.search(r"on (\d{1,2}/\d{1,2}/\d{2} \d{1,2}:\d{2}:\d{2})", header or "")
    if not m:
        return dt.timedelta(0)
    try:
        said = dt.datetime.strptime(m.group(1), "%m/%d/%y %H:%M:%S")
        written = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc).replace(tzinfo=None)
        raw = round((said - written).total_seconds() / 900.0) * 900
        return dt.timedelta(seconds=raw)
    except Exception as e:
        print(f"  ! could not recover the export's clock ({e!r}); assuming UTC")
        return dt.timedelta(0)


def load_positions(path, con, dry):
    rows = list(csv.reader(open(path, encoding="utf-8-sig")))
    stamp = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    out, underlying = [], None
    for r in rows:
        if not r or not r[0].strip():
            continue
        head = r[0].strip()
        # The statement groups each underlying as `CRWD` (bare ticker), then its long
        # name, then the option rows. Carrying the ticker down is the only way the option
        # rows learn what they belong to -- the CSV does not repeat it on the leg line.
        if re.fullmatch(r"[A-Z]{1,6}", head):
            underlying = head
            continue
        # 2026-10-13 export dropped 3 columns (16 fields) and added "(EOM)" expiry prefixes
        # after the multiplier; both silently skipped every row. Guard needs only head+qty.
        if len(r) < 2 or not (head[0].isdigit() or head.upper().startswith("(EOM)")):
            continue
        m = re.match(r"(\d+)\s+(?:\((?:EOM|Weeklys)\)\s+)?(\d{1,2} [A-Z]{3} \d{2})\s+([\d.]+)\s+(PUT|CALL)", head, re.I)
        if not m or not underlying:
            continue
        qty = money(r[1]) or 0.0
        if qty == 0:            # closed today; TWS reports these as residual zero rows too
            continue
        exp = parse_expiry(m.group(2))
        cp = m.group(4).upper()[0]
        # OCC, so the key JOINS with `positions` and with IBKR. Storing the statement's
        # human string ("100 20 NOV 26 220 PUT") as the symbol made every Schwab leg look
        # like a mismatch -- 18 phantom differences that were one bad key, not a book.
        occ = f"{underlying}{exp:%y%m%d}{cp}{int(round(float(m.group(3)) * 1000)):08d}"
        out.append([stamp, ACCOUNT, "Schwab", underlying, "OPT", occ,
                    exp, float(m.group(3)), cp, "USD", qty])
    print(f"  positions: {len(out)} live")
    for x in out: print(f"    {x[5]:26} {x[10]:>6.0f}  exp {x[6]}")
    return out


def load_trades(path, con, dry):
    rows = list(csv.reader(open(path, encoding="utf-8-sig")))
    offset = session_offset(path, rows[0][0] if rows and rows[0] else "")
    print(f"  export clock: UTC{offset.total_seconds()/3600:+.0f} "
          f"(recovered from the header vs the file's mtime)")
    out, section = [], None
    for r in rows:
        if not r: continue
        head = r[0].strip()
        if head.endswith("Orders"): section = head; continue
        if section != "Filled Orders" or len(r) < 13: continue
        t = r[2].strip()
        if not re.match(r"\d{1,2}/\d{1,2}/\d{2} \d{1,2}:\d{2}:\d{2}", t): continue
        # The export is stamped on the SESSION's clock (here CST, UTC+8), NOT ET. Reading
        # it as ET shifted every fill +4h and put all 12 of today's fills in the future,
        # which is what selfcheck's "no timestamp in the future" caught.
        ts = dt.datetime.strptime(t, "%m/%d/%y %H:%M:%S") - offset
        sym, exps, K, typ, px = r[7], r[8], r[9], r[10].upper(), r[11]
        out.append([ts, t, ACCOUNT, "Schwab", sym, f"{sym} {exps} {K} {typ}".strip(),
                    "OPT" if typ in ("PUT", "CALL") else "STOCK",
                    parse_expiry(exps), money(K),
                    # FIRST LETTER, like the positions loader above and every other source:
                    # 'PUT'/'CALL' here against 'P'/'C' in `positions`, `ibkr_flex`,
                    # `ibkr_tws_exec`, `journal` and `schwab_transactions` meant a Schwab
                    # option fill could never join to the position it opened. selfcheck's
                    # "positions == net of fills" surfaced it as the EWZ Dec-18 43P reading
                    # (net 0, position -50) -- the position was real, the key was not.
                    # The same shape as the symbol bug this loader already fixed once.
                    typ[0] if typ in ("PUT", "CALL") else None,
                    r[4].upper(),                      # BUY / SELL
                    "CLOSE" if "CLOSE" in r[6].upper() else "OPEN",
                    abs(int(money(r[5]) or 0)), money(px),
                    None, None, None, "USD", SOURCE,
                    f"{r[3]} {t}; net {r[12]}"])
    print(f"  fills: {len(out)}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(bootstrap.trading_logs_dir()))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    d = pathlib.Path(a.dir)
    newest = lambda p: (p.stat().st_mtime_ns, p.name)
    pos_f = sorted(d.glob("*PositionStatement.csv"), key=newest)
    trd_f = sorted(d.glob("*TradeActivity.csv"), key=newest)
    if not pos_f and not trd_f:
        print(f"no Schwab CSVs in {d}"); return
    con = duckdb.connect(str(DB))
    con.execute("""CREATE TABLE IF NOT EXISTS broker_positions (
        as_of TIMESTAMP NOT NULL, account TEXT NOT NULL, broker TEXT NOT NULL,
        underlying TEXT, sec_type TEXT, symbol TEXT, expiry DATE, strike DOUBLE,
        call_put TEXT, currency TEXT, qty DOUBLE,
        PRIMARY KEY (as_of, account, symbol, expiry, strike, call_put))""")
    pos = load_positions(pos_f[-1], con, a.dry_run) if pos_f else []
    trd = load_trades(trd_f[-1], con, a.dry_run) if trd_f else []
    if a.dry_run:
        print("  dry run — nothing written"); con.close(); return
    # executemany raises on an empty list rather than doing nothing -- the same crash
    # that destroyed MA's flow-days in build_option_flow. Guard both writes.
    if pos_f:
        from data_providers.jobs.trades.schwab_export_snapshot import store
        digest, added = store(con, pos_f[-1], pos, ACCOUNT)
        print(f"  export snapshot {digest[:12]}: {'saved' if added else 'already retained'}; "
              "source timezone/completeness unknown; legacy as_of not refreshed")
    have = {r[0] for r in con.execute("SELECT notes FROM fills WHERE source=?", [SOURCE]).fetchall()}
    new = [t for t in trd if not any(t[19] == (h or "") for h in have)]
    if new:
        con.executemany("""INSERT INTO fills (ts_utc, ts_orig, account, broker, underlying, symbol,
            security_type, expiry, strike, call_put, action, open_close, qty, price, fee,
            realized_pnl, exchange, currency, source, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", new)
    con.close()
    print(f"  processed {len(pos)} export positions, wrote {len(new)} fills")


if __name__ == "__main__":
    main()
