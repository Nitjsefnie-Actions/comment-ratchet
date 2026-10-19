#!/usr/bin/env python3
"""Report added-statement coverage for run.py and scripts/ci/*.py.

The workflow diffs the measured merge checkout's HEAD^1..HEAD to exclude
base-only changes. Missing statement records invalidate XML rather than
shrink the denominator. Invalid reports exit 1 with empty stdout, no
traceback and the problem on stderr's first line. Informational only.
"""
import argparse
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from coverage import Coverage
from coverage.exceptions import CoverageException
from coverage.files import GlobMatcher, canonical_filename, prep_patterns

_TARGET = re.compile(r'^\+\+\+ (.*)$')
_HUNK = re.compile(r'^@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@')


def _decode_git_path(value):
    """Decode Git quoting and strip b/."""
    if not value.startswith('"'):
        value = value.split('\t', 1)[0]
        return value[2:] if value.startswith('b/') else value
    escaped = value[1:-1] if value.endswith('"') else value[1:]
    escapes = {
        '\\': b'\\', '"': b'"', 'a': b'\a', 'b': b'\b',
        'f': b'\f', 'n': b'\n', 'r': b'\r', 't': b'\t',
        'v': b'\v',
    }
    decoded = bytearray()
    index = 0
    while index < len(escaped):
        char = escaped[index]
        if char != '\\':
            decoded.extend(char.encode('utf-8'))
            index += 1
            continue
        octal = escaped[index + 1:index + 4]
        if len(octal) == 3 and all(item in '01234567' for item in octal):
            decoded.append(int(octal, 8))
            index += 4
            continue
        if index + 1 == len(escaped):
            decoded.extend(b'\\')
            index += 1
            continue
        escaped_char = escaped[index + 1]
        decoded.extend(escapes.get(escaped_char,
                                   escaped_char.encode('utf-8')))
        index += 2
    value = decoded.decode('utf-8')
    return value[2:] if value.startswith('b/') else value


def executable_lines(coverage_xml):
    """Read Cobertura line hits."""
    root = ET.parse(coverage_xml).getroot()
    if root.tag != 'coverage':
        raise ValueError('root is not <coverage>')
    measured = {}
    usable = False
    for class_node in root.iter('class'):
        filename = class_node.get('filename')
        if not filename:
            continue
        lines = measured.setdefault(filename, {})
        for line_node in class_node.iter('line'):
            number = line_node.get('number')
            hits = line_node.get('hits')
            if number is None:
                raise ValueError(f'missing line number for {filename}')
            try:
                number_value = int(number)
            except ValueError as error:
                raise ValueError(
                    f'invalid line number for {filename}: {number!r}'
                ) from error
            if number_value <= 0:
                raise ValueError(
                    f'invalid line number for {filename}: {number!r} '
                    '(must be positive)')
            if hits is None:
                raise ValueError(
                    f'missing hits for {filename}:{number_value}')
            try:
                hits_value = int(hits)
            except ValueError as error:
                raise ValueError(
                    f'invalid hits for {filename}:{number_value}: '
                    f'{hits!r}') from error
            # Any class reaching a line makes it covered.
            lines[number_value] = max(lines.get(number_value, 0), hits_value)
            usable = True
    if not usable:
        raise ValueError('no usable line entries')
    return measured


def added_lines(diff_text):
    """Walk added line numbers."""
    added = {}
    path = None
    line_number = 0
    in_hunk = False
    old_remaining = new_remaining = 0
    for line in diff_text.split('\n'):
        header = line.removesuffix('\r')
        if line.startswith('Binary files '):
            raise ValueError(
                f'binary diff record is not measurable: {line}')
        # Removed -- text resembles a header; read headers only outside hunks.
        if header.startswith('diff --git ') or (
                not in_hunk and header.startswith('--- ')):
            path = None
            in_hunk = False
            continue
        target = _TARGET.match(header) if not in_hunk else None
        if target is not None:
            name = _decode_git_path(target.group(1))
            path = None if name == '/dev/null' else name
            continue
        hunk = _HUNK.match(header)
        if hunk is not None:
            old_remaining = int(hunk.group(1) or 1)
            line_number = int(hunk.group(2))
            new_remaining = int(hunk.group(3) or 1)
            in_hunk = bool(old_remaining or new_remaining)
            continue
        if path is None or not in_hunk:
            continue
        if line.startswith('+'):
            added.setdefault(path, set()).add(line_number)
            line_number += 1
            new_remaining -= 1
        elif line.startswith('-'):
            old_remaining -= 1
        elif line.startswith(' ') or line == '':
            line_number += 1
            old_remaining -= 1
            new_remaining -= 1
        if old_remaining == 0 and new_remaining == 0:
            in_hunk = False
    return added


def _analyzer():
    """Analyze without colliding with the collector data file."""
    return Coverage(config_file=True, data_file=None)


def validate_statement_records(measured, added):
    """Require XML records for added statements."""
    analyzer = _analyzer()
    for path in sorted(set(measured) & set(added)):
        _source, statements, _excluded, _missing, _formatted = (
            analyzer.analysis2(path))
        required = set(statements) & added[path]
        absent = required.difference(measured[path])
        if absent:
            raise ValueError(
                f'missing executable statement records for {path}: '
                f'{_ranges(absent)}')


def _ranges(numbers):
    """Join adjacent missed lines."""
    spans = []
    for number in sorted(numbers):
        if spans and number == spans[-1][1] + 1:
            spans[-1][1] = number
        else:
            spans.append([number, number])
    return ', '.join(str(low) if low == high else f'{low}-{high}'
                     for low, high in spans)


def measure(measured, added):
    """Intersect added lines with measured statements."""
    rows = []
    covered = total = 0
    for path in sorted(added):
        lines = measured.get(path)
        if not lines:
            # Unmeasured is distinct from missed.
            continue
        touched = sorted(number for number in added[path] if number in lines)
        if not touched:
            continue
        missed = [number for number in touched if lines[number] == 0]
        rows.append((path, len(touched) - len(missed), len(touched), missed))
        covered += len(touched) - len(missed)
        total += len(touched)
    return rows, covered, total


def _measured_source(path):
    """Match .coveragerc's report include."""
    return path == 'run.py' or (Path(path).parent == Path('scripts/ci')
                               and path.endswith('.py'))


def unmeasured_sources(measured, added):
    """Find changed sources absent from XML."""
    config = _analyzer()
    patterns = [
        pattern
        for option in ('run:omit', 'report:omit')
        for pattern in config.get_option(option) or []]
    omitted = GlobMatcher(prep_patterns(patterns))
    return {path for path in added
            if _measured_source(path) and path not in measured
            and not omitted.match(canonical_filename(path))}


def render(rows, covered, total, unmeasured=()):
    """Render the comment body."""
    unmeasured = set(unmeasured)
    out = ['### Coverage of this change', '']
    if total == 0 and unmeasured:
        out.append('This change added lines to the sources the coverage report '
                   'measures (`run.py` and `scripts/ci/*.py`), but the report '
                   'names none of the changed paths. Nothing here was '
                   'measured, which '
                   'is not the same as nothing needing to be: the report '
                   'most likely spells paths differently than the diff '
                   'does.')
        out.append('')
        out.append('Unmeasured changed source files:')
        out.extend(f'- `{path}`' for path in sorted(unmeasured))
    elif total == 0:
        # No measured statements is distinct from no changes.
        out.append('No **measured** lines were added. The report measures '
                   'only `run.py` and `scripts/ci/*.py` '
                   '(`[report] include` in `.coveragerc`), '
                   'so a change confined to the suite, the workflows or any '
                   'other file has no patch coverage to report -- that is '
                   'not the same as none of it running.')
    else:
        percent = 100.0 * covered / total
        if covered < total:
            percent = min(percent, 99.9)
        subject = 'measured added lines' if unmeasured else 'added lines'
        out.append(f'**{percent:.1f}%** of {subject} covered '
                   f'({covered}/{total}).')
        out.append('')
        out.append('| File | Covered | Added | Missed lines |')
        out.append('| --- | ---: | ---: | --- |')
        for path, file_covered, file_total, missed in rows:
            detail = _ranges(missed) if missed else '—'
            out.append(
                f'| `{path}` | {file_covered} | {file_total} | {detail} |')
        if unmeasured:
            out.append('')
            out.append('The coverage report did not measure these changed '
                       'source files:')
            out.extend(f'- `{path}`' for path in sorted(unmeasured))
        if covered == total:
            out.append('')
            if unmeasured:
                out.append('Every measured added line was reached; the '
                           'files above were not measured.')
            else:
                out.append('Every added line was reached.')
    return '\n'.join(out) + '\n'


def main():
    """Print patch coverage."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--coverage', required=True,
                        help='Cobertura XML written by `coverage xml`')
    parser.add_argument('--diff', default='-',
                        help='unified diff to read, or - for stdin')
    args = parser.parse_args()

    diff_bytes = (sys.stdin.buffer.read() if args.diff == '-'
                  else Path(args.diff).read_bytes())
    # Replacement preserves line numbers in Git's --text output.
    diff_text = diff_bytes.decode('utf-8', errors='replace')
    try:
        # Keep strict path decoding inside the refusal guard.
        measured = executable_lines(args.coverage)
        added = added_lines(diff_text)
        validate_statement_records(measured, added)
    except (CoverageException, ET.ParseError, ValueError) as error:
        print(f'coverage report invalid: {error}', file=sys.stderr)
        return 1
    rows, covered, total = measure(measured, added)
    sys.stdout.write(render(rows, covered, total,
                            unmeasured_sources(measured, added)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
