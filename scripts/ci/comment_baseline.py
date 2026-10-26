#!/usr/bin/env python3
"""Ratchet per-file comment shares using exact byte pairs."""
from __future__ import annotations

import argparse
import importlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

comment_share = importlib.import_module('scripts.ci.comment_share' if __package__ else 'comment_share')
TARGET = (15, 100)
Pair = tuple[int, int] | list[int]


def above(current: Pair, limit: Pair) -> bool:
    if limit[1] == 0:
        return current[0] > 0
    return current[0] * limit[1] > limit[0] * current[1]


def entries(measured: dict[str, tuple[int, int]], target: Pair = TARGET) -> dict[str, list[int]]:
    return {path: list(counts) for path, counts in measured.items()
            if above(counts, target)}


def violations(measured: dict[str, tuple[int, int]], baseline: dict[str, list[int]],
               target: Pair = TARGET) -> dict[str, str]:
    return {path: f'{counts} exceeds {baseline.get(path, target)}'
            for path, counts in measured.items()
            if (above(counts, baseline[path]) and counts[0] > baseline[path][0]
                if path in baseline else above(counts, target))}


def tightened(baseline: dict[str, list[int]], measured: dict[str, tuple[int, int]],
              target: Pair = TARGET) -> dict[str, list[int]]:
    updated = {}
    for path, recorded in baseline.items():
        current = measured.get(path)
        if current is not None and above(current, target):
            updated[path] = (list(current) if current[0] < recorded[0]
                             and not above(current, recorded) else recorded)
    updated.update({path: pair for path, pair in entries(measured, target).items()
                    if path not in baseline})
    return updated


def target_input(value: str) -> int:
    if not re.fullmatch(r'[0-9]+', value) or int(value) <= 0:
        raise ValueError(f'input target must be a positive integer, got {value!r}')
    return int(value)


def relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or '..' in path.parts
            or str(path) != value or '\\' in value
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError(f'expected a repository-relative file path, got {value!r}')
    return value


def records_input(value: str, root: Path) -> str:
    try:
        relative_path(value)
        if not (root / value).resolve().is_relative_to(root.resolve()):
            raise ValueError('path resolves outside the consumer checkout')
        if (root / value).is_dir():
            raise ValueError('path names a directory')
    except ValueError as error:
        raise ValueError(f'input records: {error}') from error
    return value


def base_input(value: str) -> str:
    if not value or value.startswith('-') or any(char.isspace() or ord(char) < 32 for char in value):
        raise ValueError(f'input base-ref must name a Git revision, got {value!r}')
    return value


def decode_records(raw: str | bytes) -> dict[str, list[int]]:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'duplicate records entry: {key}')
            result[key] = value
        return result

    try:
        data = json.loads(raw, object_pairs_hook=unique)
        if not isinstance(data, dict):
            raise ValueError('expected a JSON object')
        for path, pair in data.items():
            relative_path(path)
            if (not isinstance(pair, list) or len(pair) != 2
                    or any(type(count) is not int for count in pair)
                    or not 0 <= pair[0] <= pair[1] or pair[1] <= 0):
                raise ValueError(f'{path}: expected [comment_bytes, non_whitespace_bytes], '
                                 f'0 <= comments <= total and total > 0; got {pair!r}')
        return data
    except ValueError as error:
        raise ValueError(f'invalid records: {error}') from error


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(['git', '-C', str(root), *args], capture_output=True,
                          check=check, timeout=120)


def resolve_ref(root: Path, revision: str) -> str:
    base_input(revision)
    resolved = git(root, 'rev-parse', '--verify', '--end-of-options', f'{revision}^{{commit}}', check=False)
    if resolved.returncode:
        fetched = git(root, 'fetch', '--no-tags', 'origin', revision, check=False)
        if fetched.returncode:
            raise ValueError(f'input base-ref {revision!r} could not be fetched: '
                             + fetched.stderr.decode('utf-8', errors='replace').strip())
        resolved = git(root, 'rev-parse', '--verify', 'FETCH_HEAD^{commit}')
    return resolved.stdout.decode().strip()


def records_at(root: Path, revision: str, records: str) -> dict[str, list[int]] | None:
    sha = resolve_ref(root, revision)
    result = git(root, 'show', f'{sha}:{records}', check=False)
    if result.returncode:
        listed = git(root, 'ls-tree', '-z', sha, '--', records)
        if not listed.stdout:
            return None
        raise ValueError(f'cannot read records at input base-ref {revision!r}: '
                         + result.stderr.decode('utf-8', errors='replace').strip())
    return decode_records(result.stdout)


def changed_paths(root: Path, sha: str) -> set[str]:
    """The paths this pull request changes, from the branch point onward.

    Renames are judged as a delete plus an add, so a renamed recorded file
    cannot carry its old entry under a new name.
    """
    listed = git(root, 'diff', '--no-renames', '--name-only', '-z', sha, 'HEAD', '--')
    return {path for path in listed.stdout.decode('utf-8').split('\0') if path}


def records_diff(required: dict[str, list[int]], actual: dict[str, list[int]],
                 optional: set[str] = frozenset(),
                 accepted: dict[str, list[int]] | None = None,
                 origin: dict[str, list[int]] | None = None) -> list[str]:
    lines = []
    for path in sorted(set(required) - set(actual)):
        if path in optional:
            continue
        # An entry main added after the pull request branched is not the
        # pull request's to carry.
        if origin is not None and path not in origin and path not in actual:
            continue
        lines.append(f'+ {path}: required {required[path]}')
    for path in sorted(set(actual) - set(required)):
        # An entry main dropped after the pull request branched is not the
        # pull request's to drop.
        if origin is not None and origin.get(path) == actual[path]:
            continue
        lines.append(f'- {path}: recorded {actual[path]}')
    for path in sorted(set(required) & set(actual)):
        if required[path] != actual[path]:
            if accepted and accepted.get(path) == actual[path]:
                continue
            if origin is not None and origin.get(path) == actual[path]:
                continue
            lines.append(f'~ {path}: required {required[path]}, recorded {actual[path]}')
    return lines


def measure_changed_at(root: Path, sha: str, paths: set[str]) -> dict[str, tuple[int, int]]:
    changed = git(root, 'diff', '--no-renames', '--name-only', '-z', sha, 'HEAD', '--')
    paths = paths.intersection(changed.stdout.decode('utf-8').split('\0'))
    measured = {}
    with tempfile.TemporaryDirectory() as directory:
        tree = Path(directory)
        for path in sorted(paths):
            blob = git(root, 'show', f'{sha}:{path}', check=False)
            if blob.returncode:
                continue
            try:
                source = tree / path
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(blob.stdout)
                language = comment_share.language_for(source)
                if language:
                    measured.update(comment_share.measure_files(tree, {path: language}))
            except (OSError, ValueError):
                continue
    return measured


def write_records(path: Path, data: dict[str, list[int]]) -> None:
    serialized = json.dumps(data, sort_keys=True, indent=2) + '\n'
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                     delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(serialized)
    if path.exists():
        temporary.chmod(path.stat().st_mode)
    else:
        temporary.chmod(0o644)
    temporary.replace(path)


def run(root: Path, records: str, mode: str, target: int, base_ref: str = '') -> int:
    limit = (target, 100)
    if mode == 'tighten':
        path = root / records
        baseline = decode_records(path.read_bytes()) if path.exists() else {}
        measured = comment_share.measure_files(root)
        updated = tightened(baseline, measured, limit)
        # A recorded file above its record on both dimensions is reported,
        # never failed and never raised: the loose entry stays until a pull
        # request's repair lands it (issue 69).
        for path_name, counts in sorted(measured.items()):
            record = baseline.get(path_name)
            if record is not None and above(counts, record) and counts[0] > record[0]:
                report = (f'{path_name}: {counts} exceeds its record {record}; '
                          'a pull request touching the file must bring it back '
                          'within its effective limit (tighten never raises a '
                          'record)')
                print(report, file=sys.stderr)
                print(f'::warning::{report}', file=sys.stdout)
        if updated != baseline or not path.exists():
            write_records(path, updated)
        print(f'{len(updated)} comment-share entries recorded in {records}')
        return 0
    base_sha = resolve_ref(root, base_ref)
    baseline = records_at(root, base_sha, records)
    measured = comment_share.measure_files(root)
    if baseline is None:
        head = records_at(root, 'HEAD', records)
        if head is None:
            raise ValueError(f'no records file {records!r} at base-ref or HEAD; '
                             'run tighten to seed one and commit it')
        expected = entries(measured, limit)
        differing = sorted(path for path in head.keys() | expected.keys()
                           if head.get(path) != expected.get(path))
        for path in differing:
            print(f'{path}: head records {head.get(path)} differs from exact '
                  f'tighten seed {expected.get(path)}', file=sys.stderr)
        if differing:
            return 1
        print(f'{len(measured)} tracked files within the comment-share policy')
        return 0
    # The pull request's own change set is the merge-base diff: a pull
    # request behind main is not failed for paths main moved after the
    # branch point. Records policy itself stays at base-ref.
    branch_point = git(root, 'merge-base', base_sha, 'HEAD').stdout.decode().strip()
    changed = changed_paths(root, branch_point)
    origin = {path: list(pair)
              for path, pair in (records_at(root, branch_point, records) or {}).items()
              if path not in changed}
    head = records_at(root, 'HEAD', records) or {}
    # Unchanged paths the merge skew left unrecorded above the target: the
    # head entry is optional, and when present it must be the measured pair,
    # so a records-only pull request can land the named repair.
    optional = {path: list(counts) for path, counts in measured.items()
                if path not in changed and path not in baseline
                and above(counts, limit)}
    # An unchanged recorded path may carry a records-only tightening: fewer
    # comment bytes at a share not above the record's, by tighten's own
    # rewrite rule judged on the record the pull request carries.
    accepted = {path: list(pair) for path, pair in head.items()
                if path in baseline and path not in changed
                and pair[0] < baseline[path][0] and not above(pair, baseline[path])}
    # The records at HEAD must already be what tighten writes over this tree
    # from the base records, so one pull request lands the records change. A
    # changed unrecorded file gains no entry here: it is judged against the
    # target, and its repair is trimming, never recording.
    required = {path: list(pair) for path, pair in baseline.items()
                if path not in changed}
    required.update(optional)
    required.update(tightened({path: pair for path, pair in baseline.items()
                               if path in changed},
                              {path: counts for path, counts in measured.items()
                               if path in changed and path in baseline}, limit))
    failed = False
    report = records_diff(required, head, optional, accepted, origin)
    if report:
        failed = True
        print('require-tight: the records at HEAD differ from what tighten '
              'writes over this tree from the base records:', file=sys.stderr)
        for line in report:
            print(line, file=sys.stderr)
    # A missing optional entry is the merge skew the pull request does not
    # touch: name the exact entry to add, so a records-only pull request can
    # land the repair, and never fail the pull request for it.
    for path in sorted(optional.keys() - head.keys()):
        notice = (f'{path}: {measured[path]} exceeds the target {limit}; a '
                  'records-only pull request may land the entry '
                  f'{optional[path]}')
        print(notice, file=sys.stderr)
        print(f'::notice::{notice}', file=sys.stdout)
    # Measurement judges only files this change touches: a file the change
    # does not alter is never failed for a tree that moved under it.
    checked = {path: counts for path, counts in measured.items()
               if path in changed and path in baseline
               and (not above(baseline[path], limit) or above(counts, limit))}
    # A changed file above its committed record on both dimensions fails
    # against the record itself, even where the substituted base pair below
    # would admit it: the record binds the pull request that grows it.
    over_record = {path: counts for path, counts in checked.items()
                   if above(counts, baseline[path]) and counts[0] > baseline[path][0]}
    for path, counts in sorted(over_record.items()):
        print(f'{path}: {counts} exceeds its record {baseline[path]}', file=sys.stderr)
    if over_record:
        failed = True
    # A changed unrecorded file above the target fails, as in v1: the target
    # is the only allowance an unrecorded file has, so the repair is trimming
    # the file, never recording it.
    unrecorded = {path: counts for path, counts in measured.items()
                  if path in changed and path not in baseline
                  and above(counts, limit)}
    for path, counts in sorted(unrecorded.items()):
        print(f'{path}: {counts} exceeds the target {limit}; trim the file, '
              'never record it', file=sys.stderr)
    if unrecorded:
        failed = True
    for path, counts in measure_changed_at(root, base_sha,
                                           baseline.keys() & measured.keys()).items():
        if not above(counts, baseline[path]):
            baseline[path] = list(counts)
    found = violations(checked, baseline, limit)
    for path, detail in sorted(found.items()):
        print(f'{path}: {detail}', file=sys.stderr)
    if found:
        failed = True
    # A file the change does not touch is never failed: one above its record
    # on both dimensions is named as a notice, so a pull request can repair
    # it without the wedge.
    for path, counts in sorted(measured.items()):
        if (path in baseline and path not in changed
                and above(counts, baseline[path]) and counts[0] > baseline[path][0]):
            notice = (f'{path}: {counts} exceeds its record {baseline[path]}; '
                      'a pull request touching the file must bring it back within '
                      'its effective limit (tighten never raises a record)')
            print(notice, file=sys.stderr)
            print(f'::notice::{notice}', file=sys.stdout)
    if failed:
        return 1
    print(f'{len(measured)} tracked files within the comment-share policy')
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--records', default='.github/comment-share.json')
    parser.add_argument('--target', default='15')
    parser.add_argument('--mode', default='check')
    parser.add_argument('--base-ref', default='')
    args = parser.parse_args(argv)
    try:
        if args.mode not in ('check', 'tighten'):
            raise ValueError(f'input mode must be check or tighten, got {args.mode!r}')
        return run(args.root.resolve(), records_input(args.records, args.root), args.mode,
                   target_input(args.target), args.base_ref)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'comment-share check failed: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
