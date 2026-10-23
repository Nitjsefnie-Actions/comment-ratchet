"""Check committed patch-report fixtures."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

# Ubuntu installs coverage; other interpreters may lack it.
pytest.importorskip('coverage', reason='diff-coverage controls require coverage')

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/diffcoverage'


def report(tmp_path, xml, diff):
    # Analyze the checkout source with its real config.
    return subprocess.run(
        [sys.executable, str(ROOT / '.github/scripts/diff_coverage.py'),
         '--coverage', str(FIXTURES / xml), '--diff', str(FIXTURES / diff)],
        cwd=ROOT, env=dict(os.environ, COMMENT_RATCHET_COVERAGE_SOURCE_ROOT=str(ROOT),
                           COVERAGE_FILE=str(tmp_path / '.coverage')),
        capture_output=True, text=True, check=False)


def renders(result, expected):
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected
    assert not result.stderr


def refuses(result, prefix):
    # Invalid inputs owe a useful first line, never a plausible empty report.
    assert result.returncode == 1, result.stderr
    assert result.stdout == ''
    assert result.stderr.splitlines()[0].startswith(prefix), result.stderr
    assert 'Traceback' not in result.stderr


def test_hunk_walk_missed_ranges(tmp_path):
    # A removed -- line must not hide the second hunk.
    renders(report(tmp_path, 'table.xml', 'table.diff'), """### Coverage of this change

**40.0%** of added lines covered (2/5).

| File | Covered | Added | Missed lines |
| --- | ---: | ---: | --- |
| `run.py` | 2 | 5 | 21-22, 24 |
""")


def test_quoted_path_decode(tmp_path):
    # Keeping the quotes or octal escape loses the measured source path.
    renders(report(tmp_path, 'quoted.xml', 'quoted.diff'), """### Coverage of this change

**100.0%** of added lines covered (2/2).

| File | Covered | Added | Missed lines |
| --- | ---: | ---: | --- |
| `run.py` | 2 | 2 | — |

Every added line was reached.
""")


def test_quoted_path_invalid_utf8(tmp_path):
    refuses(report(tmp_path, 'minimal.xml', 'quoted-invalid.diff'),
            "coverage report invalid: 'utf-8' codec can't decode byte 0xff "
            'in position 3: invalid start byte')


def test_whole_diff_unreadable_bytes(tmp_path):
    # Unreadable unrelated bytes must not change the measured row.
    renders(report(tmp_path, 'quoted.xml', 'wholediff-utf8.diff'), """### Coverage of this change

**100.0%** of added lines covered (1/1).

| File | Covered | Added | Missed lines |
| --- | ---: | ---: | --- |
| `run.py` | 1 | 1 | — |

Every added line was reached.
""")


def test_binary_diff_refused(tmp_path):
    refuses(report(tmp_path, 'minimal.xml', 'binary.diff'),
            'coverage report invalid: binary diff record is not measurable: '
            'Binary files a/run.py and b/run.py differ')


def test_absent_statement_records(tmp_path):
    # Pin the refusal, not the ranges: honest source edits can move them.
    refuses(report(tmp_path, 'absent.xml', 'absent.diff'),
            'coverage report invalid: missing executable statement records for run.py: ')


def test_duplicate_class_max_hits(tmp_path):
    # Last-wins or min would call import os missed, despite its first record.
    renders(report(tmp_path, 'merge.xml', 'merge.diff'), """### Coverage of this change

**100.0%** of added lines covered (1/1).

| File | Covered | Added | Missed lines |
| --- | ---: | ---: | --- |
| `run.py` | 1 | 1 | — |

Every added line was reached.
""")


def test_no_measured_lines(tmp_path):
    renders(report(tmp_path, 'minimal.xml', 'nomeasured.diff'), """### Coverage of this change

No **measured** lines were added. The report measures only `run.py` and `scripts/ci/*.py` (`[report] include` in `.coveragerc`), so a change confined to the suite, the workflows or any other file has no patch coverage to report -- that is not the same as none of it running.
""")


def test_unmeasured_changed_source(tmp_path):
    renders(report(tmp_path, 'minimal.xml', 'unmeasured.diff'), """### Coverage of this change

This change added lines to the sources the coverage report measures (`run.py` and `scripts/ci/*.py`), but the report names none of the changed paths. Nothing here was measured, which is not the same as nothing needing to be: the report most likely spells paths differently than the diff does.

Unmeasured changed source files:
- `run.py`
""")


def test_multiple_measured_sources(tmp_path):
    # The denominator must include changed CI scripts as well as run.py.
    renders(report(tmp_path, 'mixed.xml', 'mixed.diff'), """### Coverage of this change

**50.0%** of added lines covered (1/2).

| File | Covered | Added | Missed lines |
| --- | ---: | ---: | --- |
| `run.py` | 1 | 1 | — |
| `scripts/ci/aggregate_gate.py` | 0 | 1 | 3 |
""")


def test_unmeasured_changed_ci_source(tmp_path):
    # A path mismatch on a CI source owes the same warning as one on run.py.
    renders(report(tmp_path, 'minimal.xml', 'scripts.diff'), """### Coverage of this change

This change added lines to the sources the coverage report measures (`run.py` and `scripts/ci/*.py`), but the report names none of the changed paths. Nothing here was measured, which is not the same as nothing needing to be: the report most likely spells paths differently than the diff does.

Unmeasured changed source files:
- `scripts/ci/aggregate_gate.py`
""")


def test_partial_measurement_all_measured_lines_reached(tmp_path):
    # A fully covered row cannot conceal a second source absent from XML.
    renders(report(tmp_path, 'partial.xml', 'mixed.diff'), """### Coverage of this change

**100.0%** of measured added lines covered (1/1).

| File | Covered | Added | Missed lines |
| --- | ---: | ---: | --- |
| `run.py` | 1 | 1 | — |

The coverage report did not measure these changed source files:
- `scripts/ci/aggregate_gate.py`

Every measured added line was reached; the files above were not measured.
""")


def test_partial_measurement_missed_lines(tmp_path):
    renders(report(tmp_path, 'partial-missed.xml', 'mixed.diff'), """### Coverage of this change

**0.0%** of measured added lines covered (0/1).

| File | Covered | Added | Missed lines |
| --- | ---: | ---: | --- |
| `run.py` | 0 | 1 | 5 |

The coverage report did not measure these changed source files:
- `scripts/ci/aggregate_gate.py`
""")
