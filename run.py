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


def main() -> int:
    try:
        root = Path(os.environ.get('GITHUB_WORKSPACE', os.getcwd())).resolve()
        target = policy.target_input(env('TARGET', '15'))
        records = policy.records_input(env('RECORDS', '.github/comment-share.json'), root)
        mode = env('MODE', 'auto')
        if mode not in ('auto', 'check', 'tighten'):
            raise ValueError(f'input mode must be auto, check or tighten, got {mode!r}')
        if mode == 'auto':
            event = os.environ.get('GITHUB_EVENT_NAME', '')
            default = env('DEFAULT_BRANCH', '')
            if event == 'pull_request':
                mode = 'check'
            elif (event == 'push' and default
                  and os.environ.get('GITHUB_REF') == f'refs/heads/{default}'):
                mode = 'tighten'
            else:
                raise ValueError(f'input mode=auto cannot handle event {event!r} '
                                 f'on ref {os.environ.get("GITHUB_REF", "")!r}')
        base = policy.base_input(env('BASE_REF', '')) if mode == 'check' else ''
        if sys.argv[1:] == ['--validate-only']:
            return 0
        os.environ['COMMENT_RATCHET_ESPREE_DIR'] = str(ACTION_ROOT / 'node_modules')
        return policy.run(root, records, mode, target, base)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'comment-ratchet failed: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
