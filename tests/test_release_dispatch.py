"""Release dispatch admits a release only on GitHub evidence, with no live reads."""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts' / 'ci'))
import release_dispatch as dispatch  # noqa: E402

REPO, SHA = 'owner/repo', 'a' * 40
TAGS, RULESETS = f'repos/{REPO}/tags', f'repos/{REPO}/rulesets'


class Read:
    """Answer only the endpoints a test names; an unmodelled read is a defect."""
    def __init__(self, responses):
        self.responses, self.calls = responses, []

    def __call__(self, argv):
        assert argv[:2] == ['gh', 'api'] and 'Cache-Control: no-cache' in argv
        assert argv[2] in self.responses, argv
        self.calls.append(argv)
        value = self.responses[argv[2]]
        return json.dumps(value() if callable(value) else value)


class Write:
    def __init__(self):
        self.calls = []

    def __call__(self, argv):
        self.calls.append(argv)
        return json.dumps({'sha': 'c' * 40})


class Clock:
    def __init__(self):
        self.elapsed, self.sleeps = 0, []

    def now(self):
        return self.elapsed

    def sleep(self, seconds):
        assert seconds > 0
        self.sleeps.append(seconds)
        self.elapsed += seconds


def refuses(operation, message):
    with pytest.raises(dispatch.ReleaseError, match=message):
        operation()


def _head():
    return subprocess.run(['git', 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()


@pytest.mark.parametrize('version', ['2.1', '2.1.1-rc.1', '2.1.1+build', '', ' 2.1.1', '02.1.1', '2.01.1', '2.1.01', '-2.1.1', 'v2.1.1', '2\uff10.1.1', '2.1\uff10.1', '2.1.1\uff10'])
def test_version_is_a_canonical_three_component_number(version):
    refuses(lambda: dispatch.version_tuple(version), 'version')


def test_only_the_three_successors_of_the_numeric_latest_tag_release():
    read = Read({TAGS: [[{'name': 'v2.9.0'}, {'name': 'v99.0.0-rc.1'}], [{'name': 'v2.10.3'}, {'name': 'v2.1.0'}, {'name': 'v2.10.1\uff10'}, {'name': 'not-a-release'}]]})
    for version in ('2.10.4', '2.11.0', '3.0.0'):
        dispatch.validate_version(read, REPO, version)
    assert '--paginate' in read.calls[0] and '--slurp' in read.calls[0]
    for version in ('2.10.3', '2.9.1', '2.10.5', '2.11.1', '2.11.3', '4.0.0'):
        refuses(lambda version=version: dispatch.validate_version(read, REPO, version), 'successor')
    refuses(lambda: dispatch.validate_version(Read({TAGS: [[]]}), REPO, '1.0.0'), 'no vX.Y.Z')
    unread = Read({})
    refuses(lambda: dispatch.validate_version(unread, REPO, 'v1.0.0'), 'version')
    assert not unread.calls


def _rules(contexts=('required-one', 'required-two')):
    return {'id': 7, 'name': 'require-ci-aggregate', 'enforcement': 'active', 'rules': [{'type': 'required_status_checks', 'parameters': {
                'required_status_checks': [{'context': name} for name in contexts]}}]}


def test_required_contexts_come_from_the_active_named_ruleset_on_a_later_page():
    read = Read({RULESETS: [[{'id': 4, 'name': 'unrelated'}], [_rules()]], f'{RULESETS}/7': _rules(('a-new-context',))})
    assert dispatch.required_contexts(read, REPO) == {'a-new-context'}


@pytest.mark.parametrize('rows, detail', [ ([], _rules()), ([_rules()], _rules(())), ([_rules()], {**_rules(), 'enforcement': 'disabled'}),
    ([{**_rules(), 'enforcement': 'evaluate'}], _rules()), ([_rules(), _rules()], _rules()), ([_rules()], {**_rules(), 'rules': []})])
def test_a_missing_inactive_or_empty_ruleset_refuses(rows, detail):
    refuses(lambda: dispatch.required_contexts(Read({RULESETS: [rows], f'{RULESETS}/7': detail}), REPO), 'require-ci-aggregate')


def _run(suite=1, event='push', actor='human', sha=SHA, conclusion='failure'):
    return {'check_suite_id': suite, 'event': event, 'actor': {'login': actor}, 'head_sha': sha, 'status': 'completed', 'conclusion': conclusion}


def _check(name='gitleaks', suite=1, conclusion='success', status='completed', sha=SHA):
    return {'name': name, 'check_suite': {'id': suite}, 'conclusion': conclusion, 'status': status, 'head_sha': sha}


def _gate(runs, checks):
    return Read({f'repos/{REPO}/actions/runs?head_sha={SHA}': runs, f'repos/{REPO}/commits/{SHA}/check-runs?filter=all': checks})


def _wait(read, required=None, timeout=60, grace=40, clock=None):
    clock = clock or Clock()
    with patch.object(dispatch.time, 'monotonic', clock.now), \
            patch.object(dispatch.time, 'sleep', clock.sleep):
        dispatch.wait(read, REPO, SHA, required or {'gitleaks'}, timeout=timeout, grace=grace, interval=20)
    return clock


def test_a_same_sha_push_success_satisfies_whatever_its_workflow_reported():
    read = _gate([{'workflow_runs': [_run()]}], [{'check_runs': [ _check(), _check(name='informational', conclusion='failure')]}])
    assert _wait(read).sleeps == []
    assert all('--paginate' in argv and '--slurp' in argv for argv in read.calls)
    refuses(lambda: _wait(read, required={'gitleaks', 'new-context'}), 'new-context')


@pytest.mark.parametrize('run, check', [ (None, _check()), (_run(event='schedule'), _check()), (_run(actor='dependabot[bot]'), _check()),
    (_run(sha='b' * 40), _check()), (_run(suite=2), _check()), (_run(), _check(sha='b' * 40))])
def test_only_the_same_sha_push_suite_counts(run, check):
    clock = Clock()
    read = _gate([{'workflow_runs': [run] if run else []}], [{'check_runs': [check]}])
    refuses(lambda: _wait(read, clock=clock), 'registration grace')
    assert clock.elapsed == 40 and clock.sleeps == [20, 20]


@pytest.mark.parametrize('conclusion', ['failure', 'cancelled', 'timed_out', 'action_required', 'startup_failure', None])
def test_each_completed_non_success_refuses_at_once(conclusion):
    clock = Clock()
    read = _gate([{'workflow_runs': [_run(conclusion='success')]}],
                 [{'check_runs': [_check()]}, {'check_runs': [_check(conclusion=conclusion)]}])
    refuses(lambda: _wait(read, clock=clock), f'gitleaks: {conclusion}')
    assert clock.sleeps == []


def test_pending_and_absent_checks_have_separate_bounded_deadlines():
    for checks, message in (([], 'registration grace'), ([_check(status='queued', conclusion=None)], 'deadline')):
        clock = Clock()
        read = _gate([{'workflow_runs': [_run()]}], [{'check_runs': checks}])
        refuses(lambda: _wait(read, timeout=65, grace=35, clock=clock), message)
        assert clock.sleeps == ([20, 15] if not checks else [20, 20, 20, 5])


def test_a_check_arriving_during_the_grace_is_accepted_and_api_errors_refuse():
    clock = Clock()
    read = _gate([{'workflow_runs': [_run()]}], lambda: [{'check_runs': [_check()] if clock.elapsed else []}])
    assert _wait(read, clock=clock).sleeps == [20]

    def broken(_argv):
        raise OSError('network read refused')
    refuses(lambda: _wait(broken), 'cannot read repos/owner/repo/actions/runs')
    refuses(lambda: _wait(lambda _argv: 'not JSON'), 'cannot read')


def _verify(sha, contexts=('gitleaks',)):
    main = f'repos/{REPO}/branches/main'
    read = _gate([{'workflow_runs': [_run(sha=sha)]}], [{'check_runs': [_check(sha=sha)]}])
    read.responses = {key.replace(SHA, sha): value for key, value in read.responses.items()}
    read.responses.update({main: {'commit': {'sha': sha}}, TAGS: [[{'name': 'v2.10.0'}]],
                           RULESETS: [[_rules()]], f'{RULESETS}/7': _rules(contexts)})
    return read


def _clocked_verify(contexts, sha):
    read, printed, clock = _verify(sha, contexts), io.StringIO(), Clock()
    with patch.object(dispatch, 'time', SimpleNamespace(monotonic=clock.now, sleep=clock.sleep)), \
            contextlib.redirect_stdout(printed):
        dispatch.verify(read, REPO, '2.10.1')
    return printed.getvalue()


def test_verify_pins_main_first_and_returns_only_that_sha():
    head, read = _head(), _verify(_head())
    assert dispatch.verify(read, REPO, '2.10.1') == head
    assert read.calls[0][2] == f'repos/{REPO}/branches/main'
    assert [argv[2] for argv in read.calls].count(f'repos/{REPO}/branches/main') == 1


def test_verify_skips_only_the_pull_request_gate_and_names_it():
    assert dispatch.PULL_REQUEST_ONLY == {'gate'}
    assert 'skipping pull-request-only required context: gate' in _clocked_verify( ('gitleaks', 'gate'), _head())


def test_verify_still_refuses_a_missing_push_context_or_a_gate_only_ruleset():
    suites, near_miss = 'suites (ubuntu-latest)', 'gate (extra)'
    refuses(lambda: _clocked_verify(('gitleaks', 'gate', suites, near_miss), _head()),
            re.escape(f"registration grace: [{near_miss!r}, {suites!r}]"))
    refuses(lambda: _clocked_verify(('gate',), _head()), 'no push-checked contexts')
    refuses(lambda: dispatch.verify(_verify(_head()), REPO, '9.0.0'), 'successor')
    refuses(lambda: dispatch.verify(Read({f'repos/{REPO}/branches/main': {'commit': { 'sha': 'f' * 40}}}), REPO, '2.10.1'), 'main moved to')


def test_publish_writes_annotated_object_then_ref_then_generated_release():
    write = Write()
    dispatch.publish(Read({TAGS: [[{'name': 'v2.10.0'}]]}), write, REPO, '2.10.1', SHA)
    assert write.calls == [ ['gh', 'api', f'repos/{REPO}/git/tags', '-X', 'POST', '-f', 'tag=v2.10.1',
         '-f', 'message=Release v2.10.1', '-f', f'object={SHA}', '-f', 'type=commit'],
        ['gh', 'api', f'repos/{REPO}/git/refs', '-X', 'POST', '-f', 'ref=refs/tags/v2.10.1', '-f', 'sha=' + 'c' * 40],
        ['gh', 'api', f'repos/{REPO}/releases', '-X', 'POST', '-f', 'tag_name=v2.10.1', '-F', 'generate_release_notes=true']]


@pytest.mark.parametrize('version, sha, tags, message', [ ('v2.10.1', SHA, [], 'version'), ('2.10.1', 'not-a-sha', [], 'SHA'),
    ('2.10.1', SHA, [{'name': 'v2.10.1'}], 'already exists')])
def test_publish_refuses_before_its_first_write(version, sha, tags, message):
    write = Write()
    refuses(lambda: dispatch.publish(Read({TAGS: [[], tags]}), write, REPO, version, sha), message)
    assert not write.calls


@pytest.mark.parametrize('fail_at, attempt', [(1, 'annotated tag v2.10.1'), (2, 'tag ref v2.10.1'), (3, 'release v2.10.1')])
def test_a_failed_write_names_the_attempt_and_stops_later_writes(fail_at, attempt):
    write = Write()

    def broken(argv):
        result = write(argv)
        if len(write.calls) == fail_at:
            raise subprocess.CalledProcessError(1, argv, stderr='HTTP 422 already exists')
        return result

    refuses(lambda: dispatch.publish(Read({TAGS: [[]]}), broken, REPO, '2.10.1', SHA), attempt)
    assert len(write.calls) == fail_at


def test_cli_writes_its_output_only_after_verify_and_reports_a_refusal(tmp_path):
    head, output, write = _head(), tmp_path / 'output', Write()
    with patch.dict(os.environ, {'GITHUB_OUTPUT': str(output)}):
        assert dispatch.main(['verify', REPO, '2.10.1'], read=_verify(head), write=write) == 0
    assert output.read_text(encoding='utf-8') == f'sha={head}\n' and not write.calls
    assert dispatch.main(['publish', REPO, '2.10.1', SHA], read=Read({TAGS: [[]]}), write=Write()) == 0
    errors = io.StringIO()
    read = Read({f'repos/{REPO}/branches/main': {'commit': {'sha': None}}})
    with patch.dict(os.environ, {'GITHUB_OUTPUT': str(tmp_path / 'out')}), \
            contextlib.redirect_stderr(errors):
        assert dispatch.main(['verify', REPO, '2.10.1'], read=read, write=Write()) == 1
    assert 'verify' in errors.getvalue() and '2.10.1' in errors.getvalue()
