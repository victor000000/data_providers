import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""fetch_ibkr_flex.py — auto-pull IBKR Activity Flex Query (fills, orders, margin, P&L) to var/raw/.

Setup (one time, on IBKR website):
  1. Performance & Reports -> Flex Queries -> create an **Activity Flex Query** with sections:
     Trades / Executions / Orders / Account Information / Cash Report / Net Asset Value.
     Note the Query ID.
  2. Settings -> Reporting -> Flex Web Service -> generate a token.
Then, when you trade:
  uv run --no-project python fetch_ibkr_flex.py --token <TOKEN> --query-id <ID>
Output XML is saved to var/raw/ibkr_flex_<queryid>_<timestamp>.xml and can be loaded
into trades.duckdb by a loader (to be added) or parsed on request.

Note: Flex data is end-of-day; the intraday 盘口/greeks at fill time come from
record_trade.py / snapshot_chain.py run around the execution.
"""
import argparse
import datetime as dt
from data_providers.data import clock
import pathlib
import urllib.request

UA = "Mozilla/5.0"
BASE = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService"


def get(url, tries=6):
    for i in range(tries):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        xml = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
        if "<ErrorCode>1019" in xml:  # statement still generating
            import time; time.sleep(5)
            continue
        return xml
    return xml


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", required=True)
    ap.add_argument("--query-id", required=True)
    ap.add_argument("--outdir", default="var/raw/ibkr_flex")
    args = ap.parse_args()

    outdir = bootstrap.ROOT / args.outdir
    outdir.mkdir(parents=True, exist_ok=True)

    r1 = get(f"{BASE}/SendRequest?t={args.token}&q={args.query_id}&v=3")
    if "<ReferenceCode>" not in r1:
        print("SendRequest failed:\n", r1[:500]); return
    ref = r1.split("<ReferenceCode>")[1].split("</ReferenceCode>")[0]
    print("request accepted, ref:", ref)
    r2 = get(f"{BASE}/GetStatement?t={args.token}&q={ref}&v=3")
    stamp = clock.now().strftime("%Y%m%d_%H%M%S")
    path = outdir / f"ibkr_flex_{args.query_id}_{stamp}.xml"
    path.write_text(r2, encoding="utf-8")
    print("saved:", path, f"({len(r2)} bytes)")


if __name__ == "__main__":
    main()
