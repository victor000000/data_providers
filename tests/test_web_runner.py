from unittest.mock import patch
from data_providers.web_ops import runner
from data_providers.web_ops.jobs import preview
from data_providers.web_ops.state import root, read, write


def test_failed_update_never_publishes(tmp_path):
    job='a'*32;path=root(tmp_path)/(job+'.json')
    write(path,dict(id=job,action='trades',plan_digest=preview('trades')['digest']))
    with patch.object(runner,'data_home',return_value=tmp_path), patch.object(runner.os,'fstat'), patch.object(
            runner,'execute',return_value={'ok':False}), patch.object(runner,'publish_result') as publish:
        runner.run(job,99)
    publish.assert_not_called()
    assert read(path)['status']=='failed'


def test_publication_failure_not_reported_as_success(tmp_path):
    job='b'*32;path=root(tmp_path)/(job+'.json')
    write(path,dict(id=job,action='all',plan_digest=preview('all')['digest']))
    with patch.object(runner,'data_home',return_value=tmp_path), patch.object(runner.os,'fstat'), patch.object(
            runner,'execute',return_value={'ok':True}), patch.object(runner,'publish_result',
            return_value={'ok':False,'publication':{'status':'failed'}}):
        runner.run(job,99)
    assert read(path)['status']=='failed'
    assert read(path)['publication']['status']=='failed'


def test_plan_changed_after_launch_never_executes(tmp_path):
    job='c'*32;path=root(tmp_path)/(job+'.json')
    write(path,dict(id=job,action='all',plan_digest='obsolete'))
    with patch.object(runner,'data_home',return_value=tmp_path), patch.object(runner.os,'fstat'), patch.object(
            runner,'execute') as execute:
        runner.run(job,99)
    execute.assert_not_called()
    assert read(path)['status']=='failed'
