#!/usr/bin/env python
"""adaptive_sweep.py — run a shardable download with an adaptively tuned shard count.

A fixed worker count is wrong both ways and nobody knows which until it has run: the 4-worker
IBKR sweep was bound by LATENCY, not its budget — one process ran 0.40/s of a 6/s allowance.
`--gain` guards the scale-up (shard_control.decide); `--stall-intervals` bounds a stall the
LOGS CANNOT SEE (shard_link.py asks the socket); a finished shard is not re-walked.
"""
import pathlib
import sys
import time


from data_providers.ops.shard_control import DOWN, UP, counts, decide, progress, say  # noqa: E402
from data_providers.ops.shard_lib import Shard, parse_args, stop_on_exit  # noqa: E402
from data_providers.ops.shard_state import finished  # noqa: E402
from data_providers.ops.shard_link import link_up  # noqa: E402


def main():
    args = parse_args()
    extra_env = dict(kv.split("=", 1) for kv in args.env)
    # `shard[0-9]*`: a plain `shard*.txt` would also match `shards_notes.txt` as a universe.
    universes = sorted(pathlib.Path(args.shards_dir).glob("shard[0-9]*.txt"))
    if not universes:
        say(f"no shard files in {args.shards_dir}")
        return 2
    say(f"{len(universes)} shards; starting {args.min}, max {args.max}; env={extra_env}")
    shards = [s for s in (Shard(i, u, args.script, extra_env) for i, u in enumerate(universes))
              if not finished(s.log)]      # a shard that finished is NOT re-walked
    stop_on_exit(shards)      # else a killed supervisor leaves them fetching forever
    running = shards[:args.min]
    for s in running:
        s.start()
    say(f"started {[s.idx for s in running]}")
    prev_done = prev_err = idle = 0
    rate_before, saturated, t0, events = None, False, time.time(), []

    while True:
        time.sleep(args.interval)
        if args.max_runtime and time.time() - t0 > args.max_runtime:
            say("max runtime reached — stopping shards")
            break
        started = [s for s in shards if s.proc is not None]   # started THIS run: an unstarted
        tot_done = tot_err = 0                                # shard still holds a previous
        for s in started:                                     # run's log, whose count made the
            d, e, _ = counts(s.log)                           # total FALL (processed=-145)
            tot_done, tot_err = tot_done + d, tot_err + e
        d_done, d_err = tot_done - prev_done, tot_err - prev_err
        up = link_up()          # a dead gateway still logs "no bars": the socket cannot lie,
        denom = (d_done + d_err) if up else 0      # so with no port answering NOTHING counts
        ratio = (d_err / denom) if denom else 0.0
        idle, stalled = progress(idle, denom, args.stall_intervals)
        if stalled:
            break

        for s in [x for x in running if not x.alive()]:
            say(f"shard {s.idx} EXITED (rc={s.proc.returncode})")
            running.remove(s)
        say(f"shards={[s.idx for s in running]} processed={d_done} errors={d_err} "
            f"ratio={ratio:.1%}" + ("" if up else "  !! GATEWAY DOWN — no port answers"))
        if not running:
            # An early rc=1 exit used to empty `running` with 0 < started < total, firing
            # neither branch: the loop spun forever, silently. Starting the rest closes it.
            running = [s for s in shards if s.proc is None]
            if not running:
                say("all shards have run — finishing")
                break
            for s in running:
                s.start()
            say(f"no shard alive — started the remaining {[s.idx for s in running]}")

        action, why, rate_before, saturated = decide(
            n_running=len(running), n_started=len(started), n_total=len(shards), ratio=ratio,
            d_done=d_done, rate_before=rate_before, saturated=saturated or not up, low=args.low,
            high=args.high, gain=args.gain, min_shards=args.min, max_shards=args.max)
        if action == DOWN:
            victim = running.pop()
            say(f"  ADAPT: {why} -> STOP shard {victim.idx}")
            victim.stop()
            events.append(("down", victim.idx, round(ratio, 3)))
        elif action == UP:
            nxt = next(s for s in shards if s.proc is None)
            nxt.start()
            running.append(nxt)
            say(f"  ADAPT: {why} -> START shard {nxt.idx}")
            events.append(("up", nxt.idx, round(ratio, 3)))
        elif saturated and why:
            say(f"  ADAPT: {why}")
        prev_done, prev_err = tot_done, tot_err

    for s in shards:
        s.stop()
    say(f"scale events: {events or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
