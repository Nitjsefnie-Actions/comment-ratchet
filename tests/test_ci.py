from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LEGS = ('suites', 'shellcheck', 'comment-share', 'codeql', 'secrets', 'audit')


def fold(results):
    return subprocess.run([sys.executable, str(ROOT / 'scripts/ci/aggregate_gate.py')],
                          env=dict(os.environ, NEEDS_JSON=json.dumps(results)),
                          capture_output=True, text=True, check=False)


def test_aggregate_all_success():
    result = fold({name: {'result': 'success'} for name in LEGS})
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('leg', LEGS)
@pytest.mark.parametrize('state', ['failure', 'cancelled', 'skipped', 'missing'])
def test_aggregate_fails_closed(leg, state):
    results = {name: {'result': 'success'} for name in LEGS}
    if state == 'missing':
        del results[leg]
    else:
        results[leg]['result'] = state
    result = fold(results)
    assert result.returncode == 1
    assert leg in result.stderr


@pytest.mark.parametrize('payload', ['null', '[]', '{}', 'not json'])
def test_aggregate_bad_payload_fails(payload):
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/ci/aggregate_gate.py')],
                            env=dict(os.environ, NEEDS_JSON=payload), capture_output=True,
                            text=True, check=False)
    assert result.returncode == 1


def workflow(name):
    import yaml
    return yaml.load((ROOT / '.github/workflows' / name).read_text(), Loader=yaml.BaseLoader)


def condition(expression, *, successful=True, cancelled=False, **contexts):
    import re
    from types import SimpleNamespace
    def namespace(value):
        return SimpleNamespace(**{key: namespace(item) for key, item in value.items()}) if isinstance(value, dict) else value
    source = expression.strip().removeprefix('${{').removesuffix('}}').strip()
    source = source.replace('&&', ' and ').replace('||', ' or ')
    source = re.sub(r'!(?!=)', ' not ', source)
    return bool(eval(source, {'__builtins__': {}, 'success': lambda: successful,
                             'cancelled': lambda: cancelled,
                             'always': lambda: True},
                     {key: namespace(value) for key, value in contexts.items()}))


@pytest.mark.parametrize('event,ref,result,successful,want', [
    ('push', 'refs/heads/main', 'success', True, True),
    ('pull_request', 'refs/heads/main', 'success', True, False),
    ('push', 'refs/heads/topic', 'success', True, False),
    ('push', 'refs/tags/main', 'success', True, False),
    ('workflow_dispatch', 'refs/heads/main', 'success', True, False),
    ('push', 'refs/heads/main', 'failure', True, False),
    ('push', 'refs/heads/main', 'cancelled', True, False),
    ('push', 'refs/heads/main', 'skipped', True, False),
    ('push', 'refs/heads/main', 'success', False, False),
])
def test_ratchet_staging_requires_green_main(event, ref, result, successful, want):
    job = workflow('tests.yml')['jobs']['ratchet']
    assert job['needs'] == 'aggregate'
    assert condition(job['if'], successful=successful,
                     github={'event_name': event, 'ref': ref},
                     needs={'aggregate': {'result': result}}) is want


@pytest.mark.parametrize('event,branch,result,want', [
    ('push', 'main', 'success', True),
    ('pull_request', 'main', 'success', False),
    ('workflow_dispatch', 'main', 'success', False),
    ('push', 'topic', 'success', False),
    ('push', 'main', 'failure', False),
    ('push', 'main', 'cancelled', False),
    ('push', 'main', 'skipped', False),
])
def test_ratchet_push_trigger_and_secret_boundary(event, branch, result, want):
    config = workflow('ratchet-push.yml')
    trigger = config['on']['workflow_run']
    assert trigger['workflows'] == [workflow('tests.yml')['name']]
    assert trigger['types'] == ['completed']
    job = config['jobs']['push']
    assert job['environment'] == 'main-push'
    assert config['permissions'] == {}
    assert job['permissions'] == {'actions': 'read'}
    assert config['concurrency']['cancel-in-progress'] == 'false'
    assert condition(job['if'], github={'event': {'workflow_run': {
        'event': event, 'head_branch': branch, 'conclusion': result}}}) is want


def push_script():
    return next(step['run'] for step in workflow('ratchet-push.yml')['jobs']['push']['steps']
                if 'MASTER_PUSH_DEPLOY_KEY' in step.get('env', {}))


def shell(script, directory, **env):
    return subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', script], cwd=directory,
                          env=dict(os.environ, **{key: str(value) for key, value in env.items()}),
                          capture_output=True, text=True)


def test_ratchet_empty_key_refuses_before_any_push(tmp_path):
    result = shell(push_script(), tmp_path, MASTER_PUSH_DEPLOY_KEY='', RUNNER_TEMP=tmp_path)
    assert result.returncode == 1
    assert 'MASTER_PUSH_DEPLOY_KEY' in result.stderr and 'main-push' in result.stderr
    assert not (tmp_path / 'ratchet-repo').exists()


def test_tighten_records_commit_stops_the_artifact_loop(tmp_path):
    from tests.test_ci_comment_baseline import init_repo, commit, git, cli, write_records
    repo = tmp_path / 'consumer'
    repo.mkdir()
    init_repo(repo)
    (repo / 'x.py').write_text('# hi\nx=1\n')
    write_records(repo, {'x.py': [4, 6]})
    commit(repo, 'old records')
    steps = workflow('tests.yml')['jobs']['ratchet']['steps']
    stage = next(step for step in steps if step.get('id') == 'stage')
    upload = next(step for step in steps if step.get('with', {}).get('name') == 'ratchet-push')
    for number, want in [(1, True), (2, False)]:
        runtime = tmp_path / f'run-{number}'
        runtime.mkdir()
        output = runtime / 'output'
        assert cli(repo, '--mode', 'tighten').returncode == 0
        result = shell(stage['run'], repo, RUNNER_TEMP=runtime, GITHUB_OUTPUT=output,
                       BASE_SHA=git(repo, 'rev-parse', 'HEAD'))
        assert result.returncode == 0, result.stderr
        changed = dict(line.split('=', 1) for line in output.read_text().splitlines())['changed']
        assert condition(upload['if'], steps={'stage': {'outputs': {'changed': changed}}}) is want
        if want:
            artifact = runtime / 'ratchet-push'
            assert (artifact / 'base.txt').read_text().strip() == git(repo, 'rev-parse', 'HEAD')
            assert json.loads((artifact / 'comment-share.json').read_text()) == {'x.py': [3, 6]}
            commit(repo, 'records only')
        else:
            assert not (runtime / 'ratchet-push').exists()


@pytest.mark.parametrize('movement', ['unchanged', 'unrelated', 'records', 'conflict', 'rejected', 'race'])
def test_ratchet_push_data_with_real_git(tmp_path, movement):
    from tests.test_ci_comment_baseline import init_repo, commit, git, write_records
    origin = tmp_path / 'origin'
    origin.mkdir()
    init_repo(origin)
    git(origin, 'branch', '-M', 'main')
    write_records(origin, {'x.py': [4, 6], 'y.py': [4, 6]}).write_text(
        json.dumps({'x.py': [4, 6], 'y.py': [4, 6]}, indent=2)+'\n')
    (origin / 'source.py').write_text('raise RuntimeError("must never execute")\n')
    base = commit(origin, 'measured base')
    remote = tmp_path / 'remote.git'
    git(tmp_path, 'clone', '-q', '--bare', str(origin), str(remote))
    runtime = tmp_path / 'run'
    data = runtime / 'ratchet-data'
    data.mkdir(parents=True)
    (data / 'base.txt').write_text(base+'\n')
    (data / 'comment-share.json').write_text(json.dumps({'x.py': [3, 6], 'y.py': [4, 6]}, indent=2)+'\n')
    if movement in ('unrelated', 'records', 'conflict'):
        if movement == 'unrelated':
            (origin / 'source.py').write_text('raise RuntimeError("new source must not execute")\n')
        elif movement == 'records':
            write_records(origin, {'x.py': [4, 6], 'y.py': [3, 6]}).write_text(
                json.dumps({'x.py': [4, 6], 'y.py': [3, 6]}, indent=2)+'\n')
        else:
            write_records(origin, {'x.py': [2, 6], 'y.py': [4, 6]})
        commit(origin, 'main moved')
        git(origin, 'push', str(remote), 'HEAD:main')
    fetched = git(remote, 'rev-parse', 'main')
    if movement in ('rejected', 'race'):
        hook = remote / 'hooks/pre-receive'
        if movement == 'rejected':
            hook.write_text('#!/bin/sh\nexit 1\n')
        else:
            (origin / 'race.txt').write_text('new main\n')
            race = commit(origin, 'concurrent main')
            git(remote, 'fetch', str(origin), race)
            # receive-pack holds a quarantine; update the remote after the
            # rejection, when the workflow performs its second fetch.
            wrapper = tmp_path / 'bin'
            wrapper.mkdir()
            import shutil
            real_git = shutil.which('git')
            (wrapper / 'git').write_text(f'''#!/bin/bash
if [[ "$*" == *" push "* ]]; then
  "{real_git}" --git-dir="{remote}" update-ref refs/heads/main {race}
  exit 1
fi
exec "{real_git}" "$@"
''')
            (wrapper / 'git').chmod(0o755)
        if hook.exists():
            hook.chmod(0o755)
    env = {'RUNNER_TEMP': runtime, 'PUSH_REMOTE': remote, 'REPO': 'test/repo',
           'GITHUB_STEP_SUMMARY': runtime / 'summary'}
    if movement == 'race':
        env['PATH'] = str(wrapper)+os.pathsep+os.environ['PATH']
    script = push_script()
    # Authentication is outside this local-transport integration. The exact
    # data-handling suffix still runs Git fetch/merge/tree/commit/push.
    script = 'remote=' + script.split('remote=', 1)[1]
    result = shell(script, tmp_path, **env)
    assert result.returncode == (1 if movement == 'rejected' else 0), result.stderr
    tip = git(remote, 'rev-parse', 'main')
    if movement in ('conflict', 'rejected'):
        assert tip == fetched
    elif movement == 'race':
        assert tip == race
    else:
        assert git(remote, 'diff-tree', '--no-commit-id', '--name-only', '-r', tip) == '.github/comment-share.json'
        assert json.loads(git(remote, 'show', f'{tip}:.github/comment-share.json')) == {
            'x.py': [3, 6], 'y.py': [3 if movement == 'records' else 4, 6]}
        assert git(remote, 'show', '-s', '--format=%an', tip) == 'github-actions[bot]'
        assert not (runtime / 'ratchet-repo/source.py').exists()
    if movement in ('conflict', 'race'):
        assert 'Dropped' in (runtime / 'summary').read_text()


@pytest.mark.parametrize('event,result,cancelled,want', [
    ('pull_request', 'success', False, True),
    ('push', 'success', False, False),
    ('workflow_dispatch', 'success', False, False),
    ('pull_request', 'failure', False, False),
    ('pull_request', 'cancelled', False, False),
    ('pull_request', 'skipped', False, False),
    ('pull_request', 'success', True, False),
])
def test_coverage_artifact_only_from_measured_green_pr(event, result, cancelled, want):
    config = workflow('tests.yml')
    job = config['jobs']['diff-coverage']
    assert job['needs'] == 'suites'
    assert condition(job['if'], cancelled=cancelled,
                     needs={'suites': {'result': result}},
                     github={'event_name': event}) is want
    assert job['permissions'] == {'contents': 'read'}
    upload = next(step for step in job['steps']
                  if step.get('with', {}).get('name') == 'python-coverage-comment')
    # No step-level bypass can turn a failed report into a posted number.
    assert all('if' not in step for step in job['steps'])
    consumer = workflow('coverage-comment.yml')
    assert consumer['on']['workflow_run']['workflows'] == [config['name']]
    download = next(step for step in consumer['jobs']['comment']['steps'] if 'uses' in step)
    assert download['with']['name'] == upload['with']['name']


@pytest.mark.parametrize('os_name,want', [('ubuntu-latest', True), ('macos-latest', False)])
def test_coverage_xml_has_one_uploading_cell(os_name, want):
    jobs = workflow('tests.yml')['jobs']
    # Artifact names share one run-wide namespace; exactly one matrix cell
    # supplies XML and exactly one job supplies the rendered comment.
    uploads = [(name, step) for name, job in jobs.items() for step in job.get('steps', [])
               if step.get('uses', '').startswith('actions/upload-artifact@')
               and step['with']['name'] in ('coverage-xml', 'python-coverage-comment')]
    xml = uploads[0][1]
    assert condition(xml['if'], matrix={'os': os_name}) is want
    writer = next(step for step in jobs['suites']['steps']
                  if step.get('name') == 'Write the coverage XML')
    assert condition(writer['if'], matrix={'os': os_name}) is want
    download = next(step for step in jobs['diff-coverage']['steps']
                    if step.get('uses', '').startswith('actions/download-artifact@'))
    assert download['with']['name'] == xml['with']['name']


def test_coverage_measures_isolated_action_subprocess(tmp_path):
    from tests.test_ci_comment_baseline import init_repo, commit
    repo = tmp_path / 'consumer'
    repo.mkdir()
    init_repo(repo)
    (repo / 'x.py').write_text('# hi\nx=1\n')
    commit(repo, 'source')
    driver = tmp_path / 'driver.py'
    driver.write_text('import os, subprocess, sys\n'
                      f'env=dict(os.environ, GITHUB_WORKSPACE={str(repo)!r}, MODE="tighten")\n'
                      f'subprocess.run([sys.executable,"-I",{str(ROOT / "run.py")!r}],env=env,check=True)\n')
    config = ROOT / '.coveragerc'
    env = dict(os.environ, COVERAGE_SOURCE_ROOT=str(ROOT), COVERAGE_FILE=str(tmp_path / '.coverage'))
    commands = [
        [sys.executable, '-m', 'coverage', 'run', '--rcfile', str(config), str(driver)],
        [sys.executable, '-m', 'coverage', 'combine', '--rcfile', str(config)],
        [sys.executable, '-m', 'coverage', 'json', '--rcfile', str(config), '-o', str(tmp_path / 'coverage.json')],
    ]
    for command in commands:
        result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout+result.stderr
    files = json.loads((tmp_path / 'coverage.json').read_text())['files']
    for name in ('run.py', 'scripts/ci/comment_baseline.py', 'scripts/ci/comment_share.py'):
        assert files[name]['executed_lines'], name


def comment_step(name):
    return next(step['run'] for step in workflow('coverage-comment.yml')['jobs']['comment']['steps']
                if step['name'] == name)


@pytest.mark.parametrize('state', ['post', 'update', 'stale', 'wrong-pr', 'missing'])
def test_coverage_comment_posts_only_to_current_event_pr(tmp_path, state):
    (tmp_path / 'body.md').write_text('Python coverage: 80%. $(touch executed)\n')
    (tmp_path / 'pr-number.txt').write_text('2' if state == 'wrong-pr' else '1')
    binary = tmp_path / 'bin'
    binary.mkdir()
    stub = binary / 'gh'
    stub.write_text(f'''#!{sys.executable}
import json, os, pathlib, sys
args=sys.argv[1:]
endpoint=next((arg for arg in args if arg.startswith('repos/')), '')
if '-X' in args:
    record={{'endpoint':endpoint,'method':args[args.index('-X')+1],
             'body':pathlib.Path('comment.md').read_text()}}
    with open('writes.jsonl','a') as out: out.write(json.dumps(record)+'\\n')
elif endpoint.endswith('/comments'):
    if os.environ['COMMENT_STATE'] in ('update','missing'):
        print(json.dumps({{'id':7,'user':{{'login':'github-actions[bot]'}},
                           'body':'<!-- comment-ratchet-python-coverage -->\\nold coverage'}}))
elif endpoint.endswith('/pulls/1'):
    print('old-head' if os.environ['COMMENT_STATE']=='stale' else 'current-head')
else:
    sys.exit('unexpected gh request: '+repr(args))
''')
    stub.chmod(0o755)
    name = 'Mark missing Python coverage' if state == 'missing' else 'Post or update the pull request comment'
    result = shell(comment_step(name), tmp_path, GH_TOKEN='test-token', REPO='test/repo',
                   PR_NUMBER='1', HEAD_SHA='current-head', COMMENT_STATE=state,
                   PATH=str(binary)+os.pathsep+os.environ['PATH'])
    assert result.returncode == (1 if state == 'wrong-pr' else 0), result.stderr
    writes = tmp_path / 'writes.jsonl'
    if state in ('stale','wrong-pr'):
        assert not writes.exists()
    else:
        sent = json.loads(writes.read_text())
        assert sent['endpoint'] == ('repos/test/repo/issues/1/comments' if state == 'post'
                                     else 'repos/test/repo/issues/comments/7')
        assert sent['method'] == ('POST' if state == 'post' else 'PATCH')
        assert 'current-head' in sent['body']
        if state == 'missing':
            assert 'not measured' in sent['body']
        else:
            assert 'Python coverage: 80%.' in sent['body']
    assert not (tmp_path / 'executed').exists()


def test_ratchet_artifact_listing_does_not_swallow_api_failure(tmp_path):
    binary = tmp_path / 'bin'
    binary.mkdir()
    stub = binary / 'gh'
    stub.write_text('#!/bin/sh\nexit 42\n')
    stub.chmod(0o755)
    step = next(s for s in workflow('ratchet-push.yml')['jobs']['push']['steps'] if s.get('id') == 'list')
    output = tmp_path / 'output'
    result = subprocess.run(['bash', '-e', '-c', step['run']], cwd=tmp_path,
                            env=dict(os.environ, PATH=str(binary)+os.pathsep+os.environ['PATH'],
                                     GH_TOKEN='test', REPO='test/repo', RUN_ID='1', GITHUB_OUTPUT=str(output)),
                            capture_output=True, text=True)
    assert result.returncode == 42
    assert not output.exists()


def test_actionlint_is_a_standalone_required_check():
    lint = workflow('actionlint.yml')
    triggers = lint['on']
    assert 'pull_request' in triggers
    assert not triggers['pull_request']
    assert triggers['push']['branches'] == ['main']
    assert 'workflow_dispatch' in triggers
    assert 'workflow_call' not in triggers
    job = lint['jobs']['actionlint']
    assert job.get('name', 'actionlint') == 'actionlint'
    assert 'runs-on' in job and 'uses' not in job
    tests = workflow('tests.yml')['jobs']
    for caller in tests.values():
        assert Path(caller.get('uses', '').split('@', 1)[0]).name != 'actionlint.yml'
    assert set(tests['aggregate']['needs']) == set(LEGS)
