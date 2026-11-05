"""Default two-slot Theta transport with a bounded four-slot opt-in.

This is scheduling, not permission or a semantic coverage audit. Callers must
allocate disjoint untouched windows across all retained receipt folders first.
"""
import fcntl
import os
import time
from contextlib import ExitStack, contextmanager


def slot_capacity():
    """Explicit measured opt-in; legacy/default workers retain two shared slots."""
    value = os.environ.get('THETA_HISTORY_SLOTS', '2')
    if value not in ('2', '4'):
        raise ValueError('THETA_HISTORY_SLOTS must be 2 or 4 (verified terminal ceiling)')
    return int(value)


@contextmanager
def request_slot(lockfile, key, parallel):
    capacity = slot_capacity()
    with ExitStack() as stack:
        gate = stack.enter_context(lockfile.open('a'))
        # Hold admission while waiting for the transport gate. Once an exclusive
        # caller gets admission, subsequent shared callers cannot overtake it.
        # Keep admission until a slot is held: at most one unslotted caller
        # may hold the shared gate, rather than an unbounded waiting backlog.
        # Retain gate -> key -> slot ordering for compatibility with old workers.
        # Older processes still honor the transport gate, but do not participate
        # in this fairness protocol until their next normal restart.
        with (lockfile.parent / 'theta-admission.lock').open('a') as admission:
            fcntl.flock(admission, fcntl.LOCK_EX)
            fcntl.flock(gate, fcntl.LOCK_SH if parallel else fcntl.LOCK_EX)
            if parallel:
                # Same-key callers wait and then reuse the first durable receipt.
                keys = lockfile.parent / 'theta-request-keys'
                keys.mkdir(exist_ok=True)
                identity = stack.enter_context((keys / (key + '.lock')).open('a'))
                fcntl.flock(identity, fcntl.LOCK_EX)
                slots = [stack.enter_context((lockfile.parent / f'theta-slot-{n}.lock').open('a'))
                         for n in range(capacity)]
                while True:
                    acquired = False
                    for slot in slots:
                        try:
                            fcntl.flock(slot, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError:
                            continue
                        acquired = True
                        break
                    if acquired:
                        break
                    time.sleep(0.025)
        yield


def pace_parallel(lockfile):
    # Paid four-slot mode is concurrency-limited, not requests/second-limited.
    # Legacy two-slot mode keeps its conservative shared start spacing.
    spacing = 0.0 if slot_capacity() == 4 else 0.5
    with (lockfile.parent / 'theta-parallel-pacing.lock').open('a+') as pace:
        fcntl.flock(pace, fcntl.LOCK_EX)
        pace.seek(0)
        previous = pace.read().strip()
        now = time.monotonic()
        last = float(previous) if previous else 0.0
        # A host reboot resets monotonic time; old boot values are not deadlines.
        if 0 <= now - last < spacing:
            time.sleep(spacing - (now - last))
        pace.seek(0)
        pace.truncate()
        pace.write(str(time.monotonic()))
        pace.flush()
