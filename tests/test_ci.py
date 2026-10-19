from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LEGS = ('suites', 'shellcheck', 'comment-share', 'actionlint', 'codeql', 'secrets', 'audit')


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
