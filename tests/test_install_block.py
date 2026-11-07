"""The README install block, asserted and run as written."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from tests.test_action import metadata_env
from tests.test_ci_comment_baseline import commit, git, init_repo, write_records

ROOT = Path(__file__).resolve().parents[1]
GATE = ('github.event_name == \'pull_request\' || (github.event_name == \'push\' && '
        'github.ref_type == \'branch\' && github.ref_name == github.event.repository.default_branch)')


def install_block():
    text = (ROOT / 'README.md').read_text()
    fence = re.search(r'```yaml\n(.*?)```', text, re.S)
    assert fence, 'the README install block is the first yaml fence'
    return fence.group(1), YAML(typ='base', pure=True).load(fence.group(1))


def evaluate(expression, context):
    """The GitHub expression subset the workflows use: ==, !=, &&, ||, (), format()."""
    tokens = re.findall(r"'[^']*'|[=!]=|&&|\|\||[(),]|[\w.-]+", expression.strip()
                        .removeprefix('${{').removesuffix('}}'))
    position = 0

    def take():
        nonlocal position
        position += 1
        return tokens[position - 1]

    def primary():
        token = take()
        if token == '(':
            value = either()
            assert take() == ')'
            return value
        if token.startswith("'"):
            return token[1:-1]
        if tokens[position:position + 1] == ['(']:
            take()
            args = [either()]
            while take() == ',':
                args.append(either())
            assert token == 'format'
            return args[0].format(*args[1:])
        return context.get(token, '')

    def compare():
        value = primary()
        while tokens[position:position + 1] in (['=='], ['!=']):
            equal = take() == '=='
            # GitHub compares strings ignoring case.
            value = (str(value).casefold() == str(primary()).casefold()) is equal
        return value

    def both():
        value = compare()
        while tokens[position:position + 1] == ['&&']:
            take()
            right = compare()
            value = right if value else value
        return value

    def either():
        value = both()
        while tokens[position:position + 1] == ['||']:
            take()
            right = both()
            value = value if value else right
        return value

    result = either()
    assert position == len(tokens), expression
    return result


def test_install_block_shape():
    raw, block = install_block()
    assert block['on'] == {'pull_request': '', 'push': {'branches': ['main', 'master']},
                           'workflow_call': ''}
    job = block['jobs']['comment-share']
    checkout, action_step = job['steps']
    assert checkout['with']['ref'] == '${{ github.event.pull_request.head.sha || github.sha }}'
    assert checkout['if'] == action_step['if'] == GATE
    inputs = YAML(typ='safe', pure=True).load(ROOT / 'action.yml')['inputs']
    assert action_step['with'] == {
        **{name: str(item['default']) for name, item in inputs.items()},
        'push-key': '${{ secrets.MASTER_PUSH_DEPLOY_KEY }}'}
    # The YAML parser strips pin comments: match the raw fence, and never a
    # sha or version.
    pins = re.findall(r'@[0-9a-f]{40} # v\d+\.\d+\.\d+', raw)
    assert len(pins) == 2, pins


def context(event, ref_type='branch', ref_name='main', default='main', workflow='comment share',
            sha='a1'):
    return {'github.event_name': event, 'github.ref_type': ref_type,
            'github.ref_name': ref_name, 'github.ref': f'refs/heads/{ref_name}',
            'github.event.repository.default_branch': default,
            'github.workflow': workflow, 'github.run_id': '42', 'github.sha': sha}


@pytest.mark.parametrize('github,environment,runs', [
    (context('pull_request', ref_name='1/merge'), '', True),
    (context('push'), 'main-push', True),
    (context('push', ref_name='master', default='master'), 'master-push', True),
    (context('push', ref_name='master'), '', False),
    (context('push', ref_type='tag'), '', False),
    (context('workflow_dispatch'), '', False),
    # A called run sees its caller's event under the caller's workflow name.
    (context('push', workflow='tests'), 'main-push', True),
    (context('pull_request', ref_name='1/merge', workflow='tests'), '', True),
    (context('push', workflow='Comment Share'), 'main-push', True),
], ids=['pull-request', 'main-push', 'master-push', 'other-branch', 'tag',
        'dispatch', 'called-push', 'called-pull-request', 'caller-named-alike'])
def test_environment_binds_only_on_a_default_branch_push(github, environment, runs):
    _, block = install_block()
    job = block['jobs']['comment-share']
    assert evaluate(job['environment'], github) == environment
    assert all(bool(evaluate(step['if'], github)) is runs for step in job['steps'])
    # The standalone group's prefix is the block's own workflow name.
    name = block['name']
    assert name == 'comment share'
    prefix = name.replace(' ', '-')
    if github['github.workflow'].casefold() != name.casefold():
        want = f'{prefix}-called-{github["github.run_id"]}'
    elif github['github.event_name'] == 'push':
        want = f'{prefix}-{github["github.ref"]}-{github["github.sha"]}'
    else:
        want = f'{prefix}-{github["github.ref"]}'
    assert evaluate(block['concurrency']['group'], github) == want


def test_a_records_commit_s_push_run_cancels_no_earlier_commit_s_run():
    # GitHub keeps one pending run per group even without cancel-in-progress,
    # so only a per-commit push group keeps the records commit's run from
    # cancelling the run that pushed it; a pull request still replaces its
    # own older run.
    _, block = install_block()
    group = block['concurrency']['group']

    def run(event, sha, **fields):
        return evaluate(group, context(event, sha=sha, **fields))
    assert run('push', 'a1') != run('push', 'b2')
    assert run('pull_request', 'a1', ref_name='1/merge') == \
        run('pull_request', 'b2', ref_name='1/merge')
    assert block['concurrency']['cancel-in-progress'] == 'true'


@pytest.mark.parametrize('require_tight,want', [('false', 0), ('true', 1)])
def test_install_block_check_on_a_master_default_consumer(tmp_path, require_tight, want):
    _, block = install_block()
    raw_inputs = block['jobs']['comment-share']['steps'][1]['with']
    repo = tmp_path / 'consumer'
    repo.mkdir()
    init_repo(repo)
    source = repo / 'a.py'
    # (40, 100), above the target.
    source.write_text('#' + 'c' * 39 + '\n' + 'x' * 60 + '\n')
    write_records(repo, {'a.py': [40, 100]})
    base = commit(repo, 'seed a file above the target')
    git(repo, 'branch', '-M', 'master')
    # Resolve the with: expressions as a pull-request run would: no
    # environment is bound, so the key reads empty.
    resolved = {'${{ github.event.pull_request.base.sha }}': base,
                '${{ secrets.MASTER_PUSH_DEPLOY_KEY }}': ''}
    inputs = {name: resolved.get(value, value) for name, value in raw_inputs.items()}
    inputs['require-tight'] = require_tight
    env = {**metadata_env(repo, inputs=inputs),
           'GITHUB_EVENT_NAME': 'pull_request', 'GITHUB_REF': 'refs/pull/1/merge',
           'COMMENT_RATCHET_DEFAULT_BRANCH': 'master'}
    assert env['COMMENT_RATCHET_PUSH_KEY_SET'] == 'false'

    def run():
        return subprocess.run([sys.executable, '-I', str(ROOT / 'run.py')],
                              cwd=repo, env=env, capture_output=True, text=True)
    # Removing comment bytes at a lower share lowers the record to (38, 98).
    source.write_text('#' + 'c' * 37 + '\n' + 'x' * 60 + '\n')
    commit(repo, 'pr head removes comment bytes')
    result = run()
    assert result.returncode == want, result.stderr
    if want:
        assert '~ a.py: required [38, 98], recorded [40, 100]' in result.stderr
    else:
        assert ('::notice::a.py: recorded [40, 100], tighten writes [38, 98]'
                in result.stdout)
    write_records(repo, {'a.py': [38, 98]})
    commit(repo, 'carry the lowered record')
    assert run().returncode == 0
