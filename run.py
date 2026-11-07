#!/usr/bin/env python3
"""Dispatch the composite action from environment inputs."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ACTION_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ACTION_ROOT / 'scripts' / 'ci'))
import comment_baseline as policy


def env(name: str, default: str) -> str:
    value = os.environ.get('COMMENT_RATCHET_' + name)
    return os.environ.get(name, default) if value is None else value


def flag(name: str, input_name: str, default: str) -> bool:
    value = os.environ.get('COMMENT_RATCHET_' + name, default)
    if value not in ('true', 'false'):
        raise ValueError(f'input {input_name} must be true or false, got {value!r}')
    return value == 'true'


def tighten_on_push(root: Path, records: str, target: int, key_set: bool,
                    require_tight: bool) -> int:
    """Tighten a default-branch push and hand a change to the push step."""
    if key_set:
        head = policy.git(root, 'rev-parse', 'HEAD').stdout.decode().strip()
        if head != os.environ.get('GITHUB_SHA', ''):
            raise ValueError(f'checkout HEAD {head} is not github.sha '
                             f'{os.environ.get("GITHUB_SHA", "")!r}; nothing pushed')
    before = policy.records_at(root, 'HEAD', records) or {}
    status = policy.run(root, records, 'tighten', target)
    committed = policy.git(root, 'show', f'HEAD:{records}', check=False).stdout
    if status or committed == (root / records).read_bytes():
        return status
    if key_set:
        if not os.environ.get('GITHUB_OUTPUT'):
            raise ValueError('GITHUB_OUTPUT is unset, so the push step cannot run')
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
            output.write('changed=true\n')
        return 0
    lines = policy.records_diff(policy.decode_records((root / records).read_bytes()), before)
    # A called job's own workflow file differs from the caller's, whatever either is named.
    job_workflow = os.environ.get('COMMENT_RATCHET_JOB_WORKFLOW_REF', '')
    if job_workflow and job_workflow != os.environ.get('GITHUB_WORKFLOW_REF', ''):
        for line in lines:
            print(f'::notice::{line} (a called run reads push-key empty; the standalone '
                  'comment share run commits the tightened records)', file=sys.stdout)
        return 0
    for line in lines:
        report = f'{line} (input push-key is unset, so this run cannot commit the tightened records)'
        print(report, file=sys.stderr)
        if not require_tight:
            print(f'::warning::{report}', file=sys.stdout)
    return 1 if require_tight and lines else 0


def main() -> int:
    try:
        root = Path(os.environ.get('GITHUB_WORKSPACE', os.getcwd())).resolve()
        target = policy.target_input(env('TARGET', '15'))
        records = policy.records_input(env('RECORDS', '.github/comment-share.json'), root)
        require_tight = flag('REQUIRE_TIGHT', 'require-tight', 'false')
        key_set = flag('PUSH_KEY_SET', 'push-key', 'false')
        mode = env('MODE', 'auto')
        if mode not in ('auto', 'check', 'tighten'):
            raise ValueError(f'input mode must be auto, check or tighten, got {mode!r}')
        event = os.environ.get('GITHUB_EVENT_NAME', '')
        default = env('DEFAULT_BRANCH', '')
        pushed = (event == 'push' and bool(default)
                  and os.environ.get('GITHUB_REF') == f'refs/heads/{default}')
        if mode == 'auto':
            if event == 'pull_request':
                mode = 'check'
            elif pushed:
                mode = 'tighten'
            else:
                raise ValueError(f'input mode=auto cannot handle event {event!r} '
                                 f'on ref {os.environ.get("GITHUB_REF", "")!r}')
        base = policy.base_input(env('BASE_REF', '')) if mode == 'check' else ''
        if sys.argv[1:] == ['--validate-only']:
            return 0
        os.environ['COMMENT_RATCHET_ESPREE_DIR'] = str(ACTION_ROOT / 'node_modules')
        if mode == 'tighten' and pushed:
            return tighten_on_push(root, records, target, key_set, require_tight)
        return policy.run(root, records, mode, target, base, require_tight)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'comment-ratchet failed: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
