"""Offline web control tests; never start real maintenance or use live credentials."""
import fcntl
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from data_providers.web_ops import jobs
from data_providers.web_ops.state import root, write, held
from data_providers.web_ops.status import status
from data_providers.maintenance import Step, execute


def test_preview_rejects_shell_or_arbitrary_module():
    for action in ('publish','rm -rf /','../all',None):
        with pytest.raises(ValueError):
            jobs.preview(action)
    spec=jobs.preview('all')
    assert spec['steps'][0]['group']=='trade_inputs'
    assert {'us','cn','trades'} <= {s['group'] for s in spec['steps']}


def test_launch_idempotence_and_private_receipt(tmp_path):
    spec=jobs.preview('trades'); key='a'*32
    with patch.object(jobs.subprocess,'Popen',return_value=SimpleNamespace(wait=lambda:0)) as popen:
        first=jobs.start('trades',key,spec['digest'],tmp_path)
        assert jobs.start('trades',key,spec['digest'],tmp_path)==first
    assert popen.call_count==1
    assert popen.call_args.kwargs['start_new_session']
    assert 'shell' not in popen.call_args.kwargs
    assert (root(tmp_path)/(key+'.json')).stat().st_mode & 0o777 == 0o600
    assert jobs.history(tmp_path)[0]['status']=='interrupted'


def test_plan_change_and_invalid_id_rejected(tmp_path):
    for key,digest in [('a'*32,'old'),('../unsafe',jobs.preview('all')['digest'])]:
        with pytest.raises(ValueError):
            jobs.start('all',key,digest,tmp_path)
    assert not root(tmp_path).exists()


def test_active_provider_lock_blocks_launch_but_not_plan(tmp_path):
    folder=tmp_path/'var/maintenance';folder.mkdir(parents=True)
    with (folder/'writer.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        assert held(folder/'writer.lock')
        with pytest.raises(ValueError,match='active'):
            jobs.start('all','a'*32,jobs.preview('all')['digest'],tmp_path)
        assert status(tmp_path)['blockers']
    assert not held(folder/'writer.lock')


def test_job_lock_rejects_duplicate_concurrent_work(tmp_path):
    root(tmp_path).mkdir(parents=True)
    with (root(tmp_path)/'job.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        with pytest.raises(ValueError,match='already active'):
            jobs.start('trades','b'*32,jobs.preview('trades')['digest'],tmp_path)


def test_wrong_id_does_not_look_active_under_another_job_lock(tmp_path):
    write(root(tmp_path)/('a'*32+'.json'),dict(id='a'*32,action='all',status='running'))
    with (root(tmp_path)/'job.lock').open('w') as lock:
        lock.write('b'*32);lock.flush();fcntl.flock(lock,fcntl.LOCK_EX)
        assert jobs.history(tmp_path)[0]['status']=='interrupted'


def test_spawn_failure_persists_failed_state(tmp_path):
    with patch.object(jobs.subprocess,'Popen',side_effect=OSError('SECRET')):
        result=jobs.start('all','a'*32,jobs.preview('all')['digest'],tmp_path)
    assert result['status']=='failed' and 'SECRET' not in str(result)


def test_progress_reports_running_and_failed_then_blocked_steps(tmp_path):
    events=[]
    steps=[Step('us','first','first'),Step('us','second','second')]
    with patch('data_providers.maintenance.subprocess.run',return_value=SimpleNamespace(returncode=1)):
        result=execute(steps,home=tmp_path,progress=events.append)
    assert events[0]['current']=='first' and events[0]['steps']==[]
    assert [s['status'] for s in events[-1]['steps']]==['failed','blocked']
    assert not result['ok']


def test_status_unknown_sources_and_no_runtime_created(tmp_path):
    result=status(tmp_path)
    assert len(result['sources'])>=21
    assert all(s['transport']=='not checked' for s in result['sources'])
    assert not list(tmp_path.iterdir())


def test_concurrent_replayed_id_launches_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    spec=jobs.preview('health')
    with patch.object(jobs.subprocess,'Popen',return_value=SimpleNamespace(wait=lambda:0)) as popen:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _:jobs.start('health','d'*32,spec['digest'],tmp_path),range(8)))
    assert popen.call_count==1
    assert all(r['id']=='d'*32 for r in results)
