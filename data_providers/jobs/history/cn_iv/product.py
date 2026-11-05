"""product.py — one product's months fetched, solved, and stitched into a daily series."""
import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed

from data_providers.jobs.history.cn_iv import chains, contracts, fetch, solve
from data_providers.shared.net import status

WINDOW_DAYS = 75     # how much of a month's life to sample: the stretch where it is liquid.
# A contract's whole two-year life would need a strike for every level the future visits.


def fetch_product(args, ak, venue, prod, name, path, months, end, workers=4,
                  collect_bars=False, known_contracts=None):
    """Fetch (and solve) every month of one product -> ({day: iv}, quote calls, bars).

    With collect_bars the same requests also yield the option bars -- asking Sina twice for one
    contract would double the wall clock and the rate-limit risk on a venue that refused us once.
    """
    per_month, bars, calls, empty_streak, skipped, failed = {}, {}, 0, 0, [], []
    for y, m, e in months:
        ymd = f"{prod.lower()}{y % 100:02d}{m:02d}"
        lo, hi = e - dt.timedelta(days=args.window), min(e, end)
        step = (args.synthetic or {}).get(prod)
        try:
            grid = chains.month_grid(ak, venue, name, ymd, step, path, lo, hi)
        except fetch.Unlisted:
            skipped.append(ymd)      # not listed -- neighbours' windows cover it anyway
            continue
        except Exception:                                       # noqa: BLE001
            failed.append(ymd)       # unanswered after retries: a HOLE, not a skip
            continue
        if not grid:
            continue
        keep = solve.wanted_strikes(grid, path, lo, hi, getattr(args, "pad", None),
                                    getattr(args, "max_strikes", None))
        if not keep:
            continue
        quotes, fails = {}, 0
        codes = [c for _, c, p in keep for c in (c, p)]
        if known_contracts:
            # Never request what is stored: a bars backfill widens a ladder we already hold,
            # so only the new strikes are the point, and re-buying the rest spends quota.
            codes = [c for c in codes if c not in known_contracts]
            if not codes:
                continue
        # NOT named `fetch`: that is this package's network-hygiene module, and a local of the
        # same name shadows it -- which is exactly what broke `fetch.report` at the end of a
        # product whose first months were all unlisted (the local is never bound).
        reader = contracts.contract_bars if collect_bars else contracts.contract_hist
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(reader, ak, venue, c): c for c in codes}
            for fut in as_completed(futs):
                calls += 1
                try:
                    h = fut.result()
                except Exception:                               # noqa: BLE001
                    fails += 1
                    continue
                if collect_bars:
                    for row in h:
                        bars[(futs[fut], row[0])] = row[1:]
                    h = {row[0]: (row[4], row[5]) for row in h}
                for d, (cl, v) in h.items():
                    if lo <= d <= e:
                        quotes[(d, futs[fut])] = (cl, v)
        pts = solve.solve_month(venue, keep, quotes, e, path)
        if pts:
            per_month[ymd] = pts
            empty_streak = 0
        else:
            # A LISTED month whose contracts all came back empty is the signal that matters:
            # three in a row means the source is refusing us, so stop rather than grind
            # through the remaining twenty. Counting SKIPPED months here would abort on an
            # ordinary product -- unlisted months come in runs.
            #
            # ONLY A MONTH ASKED FOR IN FULL COUNTS: with `known_contracts` narrowing the
            # request, the months reaching the solver are the ones with holes, so empty is
            # expected, not a block (DCE:C was abandoned this way 2026-10-07, source fine).
            if len(codes) == 2 * len(keep):
                empty_streak += 1
            if empty_streak >= 3 and fails:
                print(f"  {venue}:{prod:<3} GIVING UP after 3 empty months "
                      f"({fails} contract(s) failed on {ymd}) — source looks blocked",
                      flush=True)
                break
        status("build_iv_history_cn_solver", venue=venue, product=prod, month=ymd,
               months_done=len(per_month), months_total=len(months))
        if fails:
            print(f"    {ymd}: {fails}/{len(codes)} contracts failed", flush=True)
    fetch.report(skipped, failed, months)

    # Stitch: per day the most-traded month wins (the CZCE rule) — it matters at the handover.
    points = {}
    for pts in per_month.values():
        for d, (iv, activity) in pts.items():
            if end and d > end:
                continue
            if d not in points or activity > points[d][1]:
                points[d] = (iv, activity)
    return {d: iv for d, (iv, _a) in points.items()}, calls, bars
