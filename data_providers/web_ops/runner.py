"""Detached native worker. Continues independently of webpage/server lifetime."""
import os
import sys
from dataclasses import asdict
from ..maintenance import execute, plan, publish_result
from ..runtime import data_home
from .jobs import preview
from .state import blockers, now, read, root, write


def run(job_id, lock_fd):
    os.fstat(lock_fd)  # Inherited kernel lock must remain open until this worker exits.
    home = data_home()
    path = root(home) / (job_id+'.json')
    job = read(path)
    def update(**fields):
        job.update(fields, updated_at=now())
        write(path, job)
    def progress(event):
        # Only structured safe fields; raw subprocess output stays in provider private logs.
        update(provider_run=event['run_id'], completed=len(event['steps']),
               total=event['total'], current=event.get('current'), steps=event['steps'])
    try:
        update(status='running', started_at=now())
        action = job['action']
        if job.get('plan_digest') != preview(action)['digest']:
            update(status='failed', error='Reviewed plan changed before execution; preview the operation again')
            return
        if action == 'health':
            from .health import probe_all
            from .health_sources import SOURCES
            update(total=len(SOURCES))
            probe_all(lambda rows:update(sources=rows, completed=len(rows)), home)
            update(status='completed')
        elif action == 'verify':
            from ..lake import verify
            result = verify(home)
            update(status='completed' if result.get('ok') else 'failed', verification=result)
        else:
            if blockers(home):
                raise RuntimeError('Another provider worker owns a lock')
            steps = plan(action)
            result = execute(steps, home=home, progress=progress)
            if result['ok']:
                update(status='publishing', current='Verify and publish canonical release')
                result = publish_result(result, home)
            update(status='completed' if result['ok'] else 'failed',
                   publication=result.get('publication'), current=None)
    except Exception as exc:
        update(status='failed', error=type(exc).__name__ + ': operation failed; inspect provider receipt')


if __name__ == '__main__':
    run(sys.argv[1], int(sys.argv[2]))
