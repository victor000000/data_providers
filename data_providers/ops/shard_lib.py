"""shard_lib.py — shard process management and log-derived health signals.

Split out of adaptive_sweep.py for the 100-line cap. Holds the two things that are about
RUNNING a shard rather than deciding how many to run: the Shard process wrapper, and the
regexes that turn a shard's log into the (done, errors) counts the controller scales on.

The health signals read from those logs, and the scaling law that consumes them, live in
shard_control.py.
"""
import argparse
import atexit
import os
import pathlib
import signal
import subprocess
import sys
import time

from data_providers.runtime import ROOT


def downloader_module(script):
    name = str(script).removesuffix(".py").replace("/", ".")
    for old, new in [("apps.screener.", "data_providers.jobs.history."),
                     ("apps.data_ops.", "data_providers.jobs.data_ops.")]:
        if name.startswith(old):
            return new + name[len(old):]
    if not name.startswith("data_providers."):
        raise ValueError("Use a canonical data_providers downloader module")
    return name


class Shard:
    """One downloader process over one disjoint universe slice.

    Disjoint slices + a private --staging file (DuckDB allows one writer per file) + a private
    --client-id are what make scaling SAFE: a shard can be killed at any point and its symbols
    are simply re-fetched later, while ON CONFLICT means a repeated fetch cannot duplicate a row.
    """

    def __init__(self, idx, universe, script, extra_env):
        self.idx = idx
        self.universe = universe
        self.script = script
        self.extra_env = extra_env
        self.log = ROOT / "logs" / f"adaptive_shard{idx}.log"
        self.proc = None

    def start(self):
        env = dict(os.environ, **self.extra_env)
        self.log.parent.mkdir(exist_ok=True)
        fh = open(self.log, "a")
        fh.write(f"\n=== shard {self.idx} start {time.strftime('%FT%TZ', time.gmtime())} "
                 f"universe={self.universe.name} ===\n")
        fh.flush()
        self.proc = subprocess.Popen(
            [sys.executable, "-u", "-m", downloader_module(self.script),
             "--universe", str(self.universe),
             "--client-id", str(71 + self.idx),
             "--staging", f"ibkr_s{self.idx}"],
            cwd=str(ROOT), env=env, stdout=fh, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, start_new_session=True)

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        if not self.alive():
            return
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(os.getpgid(self.proc.pid), sig)
            except Exception:
                pass
            time.sleep(4)
            if not self.alive():
                return


def stop_on_exit(shards):
    """Make the shards die with this process, however this process ends.

    `start_new_session` is what makes stopping one shard safe, and also what means NOTHING
    stops them when the supervisor is killed: on 2026-10-10 two were still running at PPID 1
    six hours later, fetching a dead gateway and appending to the logs the controller counts —
    so the sweep looked busy while nothing was downloading. SIGTERM becomes SystemExit so the
    atexit hook runs on `kill` and Ctrl-C, not only on a clean return.
    """
    atexit.register(lambda: [s.stop() for s in shards])
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shards-dir", required=True)
    ap.add_argument("--script", required=True,
                    help="repo-relative downloader taking --universe/--client-id/--staging")
    ap.add_argument("--min", type=int, default=1)
    ap.add_argument("--max", type=int, default=4)
    ap.add_argument("--interval", type=int, default=60, help="control-loop period, seconds")
    ap.add_argument("--low", type=float, default=0.06, help="error ratio below which to scale UP")
    ap.add_argument("--high", type=float, default=0.18, help="error ratio above which to scale DOWN")
    ap.add_argument("--gain", type=float, default=0.15, help="throughput gain a scale-up must beat")
    ap.add_argument("--env", action="append", default=[], metavar="K=V",
                    help="extra env for shards, e.g. IBKR_LIMIT=3600/600")
    ap.add_argument("--max-runtime", type=int, default=0, help="0 = run to completion")
    ap.add_argument("--stall-intervals", type=int, default=10,
                    help="warn after this many intervals with NO progress at all; stop at 6x")
    return ap.parse_args()
