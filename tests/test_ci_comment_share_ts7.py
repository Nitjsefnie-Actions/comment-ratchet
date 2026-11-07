from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def failure(tmp_path, *names):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    subprocess.run(['git', '-C', str(tmp_path), 'add', *names], check=True)
    return subprocess.run([sys.executable, '-I', str(ROOT / 'scripts/ci/comment_share.py'),
                           '--report', '--root', str(tmp_path)],
                          capture_output=True, text=True)


@pytest.mark.parametrize('suffix', ['ts', 'tsx', 'mts', 'cts'])
def test_bom_before_a_shebang_fails(suffix, tmp_path):
    name = f'bad.{suffix}'
    (tmp_path / name).write_text('\ufeff#!/usr/bin/env node\n// c\nlet x=1;\n')
    result = failure(tmp_path, name)
    assert result.returncode == 1
    assert name in result.stderr
    assert not result.stdout


@pytest.mark.parametrize('suffix', ['ts', 'tsx', 'mts', 'cts'])
def test_backslash_named_malformed_source_fails(suffix, tmp_path):
    # The compiler normalizes separators, so this file must be found in the program.
    name = f'weird\\bad.{suffix}'
    (tmp_path / name).write_text('const x = ;\n')
    sibling = tmp_path / f'weird/bad.{suffix}'
    sibling.parent.mkdir()
    sibling.write_text('const ok = 1;\n')
    result = failure(tmp_path, name, str(sibling))
    assert result.returncode == 1
    assert name in result.stderr
    assert not result.stdout
