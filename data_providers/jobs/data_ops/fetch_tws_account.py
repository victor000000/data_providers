import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""fetch_tws_account.py — the two things only TWS can give: live account state, and the
contract specifications behind the contracts we actually trade.

  accountSummary  NetLiquidation · ExcessLiquidity · AvailableFunds · Init/MaintMarginReq ·
                  GrossPositionValue -> trades.account_daily (source 'ibkr_tws_snapshot',
                  one row per account per day, replaced wholesale on re-run)

  reqContractDetails per distinct US option contract in `fills` -> reference.option_contracts
                  (the real multiplier, the real expiration date, and the exchange's
                  liquidHours — which encode session times and holidays for that contract).
                  These are exactly the structural fields our data cannot derive.

Read-only connection: this script can look but never trade.

    uv run python apps/data_ops/fetch_tws_account.py \
        [--limit-specs 40] [--host host.docker.internal] [--port 4001]
"""
import argparse
import datetime as dt
import pathlib
import sys

from data_providers.data import clock  # noqa: E402
from data_providers.providers import tws  # noqa: E402
from data_providers.shared.net import throttle  # noqa: E402
from data_providers.data import db  # noqa: E402
from ib_async import IB, Option  # noqa: E402

ROOT = bootstrap.ROOT
TODAY = clock.today()
WANT = ("NetLiquidation", "ExcessLiquidity", "AvailableFunds", "InitMarginReq",
        "MaintMarginReq", "GrossPositionValue", "TotalCashValue")


def occ_ib(sym, expiry, strike, cp):
    """IBKR wants the local symbol; its own spelling is fine here since we send it back."""
    return f"{sym} {expiry.strftime('%y%m%d')}{cp}{int(round(strike * 1000)):08d}"


def main():
    ap = argparse.ArgumentParser()
    # Default None, not tws.PORT: an explicit --port still pins one, but with nothing
    # given we let tws.connect() try every TWS the user might be running. Defaulting to
    # a single port put the same wrong assumption in the CLI, where it is harder to see
    # -- the run failed on "connection refused" and looked like TWS was down.
    ap.add_argument("--host", default=tws.HOST)
    ap.add_argument("--port", type=int, default=None,
                    help="pin a port; omit to try Gateway (4001/4002) and desktop (7496/7497)")
    ap.add_argument("--client-id", type=int, default=73)
    ap.add_argument("--limit-specs", type=int, default=40)
    args = ap.parse_args()

    con = db.connect(log="rw", trades_ro=True, history="ro", reference="rw" if args.limit_specs else None)
    ib = IB()
    ib.RequestTimeout = 60
    if args.port:
        ib.connect(args.host, args.port, clientId=args.client_id, timeout=20, readonly=True)
    else:
        tws.connect(ib, client_id=args.client_id, timeout=20)
    print(f"connected to TWS {args.host}:{args.port} (read-only)")

    # ---- 1) account state
    try:
        rows = ib.accountSummary()
        by_acct = {}
        for r in rows:
            if r.tag in WANT:
                by_acct.setdefault(r.account, {})[r.tag] = (float(r.value), r.currency)
        for acct, tags in by_acct.items():
            g = lambda t: (tags.get(t, (None, None))[0])
            ccy = tags.get("NetLiquidation", (None, "USD"))[1]
            con.execute("DELETE FROM account_daily WHERE day=? AND account=? AND source='ibkr_tws_snapshot'",
                        [TODAY, acct])
            con.execute("""INSERT INTO account_daily
                (day, account, currency, net_liq, margin_used, available, realized_pnl,
                 unrealized_pnl, fees, risk_ratio, source, notes, cash)
                VALUES (?,?,?,?,?,?,NULL,NULL,NULL,?,?,?,?)""",
                        [TODAY, acct, ccy, g("NetLiquidation"), g("MaintMarginReq"),
                         g("AvailableFunds"),
                         g("ExcessLiquidity") / g("NetLiquidation")
                         if g("ExcessLiquidity") and g("NetLiquidation") else None,
                         "ibkr_tws_snapshot",
                         f"init_margin={g('InitMarginReq')} gross_pos={g('GrossPositionValue')} "
                         f"excess={g('ExcessLiquidity')}",
                         g("TotalCashValue")])
            print(f"  {acct}: net_liq {g('NetLiquidation'):,.0f} · excess {g('ExcessLiquidity'):,.0f} · "
                  f"available {g('AvailableFunds'):,.0f} · maint {g('MaintMarginReq'):,.0f}")
    except Exception as e:
        print(f"  accountSummary failed: {repr(e)[:80]}")

    # ---- 2) contract specs for what we trade
    specs = con.execute("""
        SELECT DISTINCT underlying, expiry, strike, call_put FROM fills
        WHERE call_put IS NOT NULL AND broker='IBKR' AND expiry >= DATE '2026-01-01'
        ORDER BY expiry DESC LIMIT ?""", [args.limit_specs]).fetchall()
    ok = 0
    for sym, expiry, strike, cp in specs:
        try:
            throttle('ibkr')   # 60 historical requests / 10 min
            cd = ib.reqContractDetails(Option(sym, expiry.strftime("%Y%m%d"), strike, cp, "SMART"))
        except Exception as e:
            print(f"  {sym} {expiry} {strike}{cp}: {repr(e)[:60]}"); continue
        if not cd:
            print(f"  {sym} {expiry} {strike}{cp}: no contract details"); continue
        d = cd[0]
        local = getattr(d.contract, "localSymbol", occ_ib(sym, expiry, strike, cp)).replace(" ", "")
        liquid = (d.liquidHours or "")[-260:]
        con.execute("""INSERT OR REPLACE INTO ref.option_contracts
                       (symbol, underlying, expiry, strike, call_put, multiplier, exercise_style,
                        settlement, first_seen, last_seen, source)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    [local, sym, expiry, float(strike), cp,
                     int(d.contract.multiplier or 100),   # multiplier lives on the contract
                     None, None, TODAY, TODAY, "ibkr_contractdetails"])
        # realExpirationDate arrives as 'YYYYMMDD' — the real last-trade date, which is the
        # field our own data cannot know (settlement vs expiration differ for some products)
        real = getattr(d, "realExpirationDate", None)
        real_d = None
        if real and len(str(real)) == 8 and str(real).isdigit():
            real_d = dt.date(int(str(real)[:4]), int(str(real)[4:6]), int(str(real)[6:8]))
        con.execute("""INSERT OR REPLACE INTO ref.expiries VALUES (?,?,?,?,?)""",
                    [sym, expiry, "monthly", real_d, "ibkr_contractdetails"])
        ok += 1
        print(f"  {sym} {expiry} {strike:g}{cp}: multiplier {d.contract.multiplier} · "
              f"liquidHours tail {liquid[-60:] if liquid else '(none)'}")
    print(f"contract specs stored: {ok}/{len(specs)}")
    ib.disconnect()
    con.close()


if __name__ == "__main__":
    main()
