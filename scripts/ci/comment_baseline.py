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
    return current[0] * limit[1] > limit[0] * current[1]


def entries(measured: dict[str, tuple[int, int]], target: Pair = TARGET) -> dict[str, list[int]]:
    return {path: list(counts) for path, counts in measured.items()
            if above(counts, target)}


def violations(measured: dict[str, tuple[int, int]], baseline: dict[str, list[int]],
               target: Pair = TARGET) -> dict[str, str]:
    return {path: f'{counts} exceeds {baseline.get(path, target)}'
            for path, counts in measured.items()
            if above(counts, baseline.get(path, target))}


def tightened(baseline: dict[str, list[int]], measured: dict[str, tuple[int, int]],
              target: Pair = TARGET) -> dict[str, list[int]]:
    updated = {}
    for path, recorded in baseline.items():
        current = measured.get(path)
        if current is not None and above(current, target):
            updated[path] = recorded if above(current, recorded) else list(current)
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


def records_at(root: Path, revision: str, records: str) -> dict[str, list[int]] | None:
    base_input(revision)
    resolved = git(root, 'rev-parse', '--verify', '--end-of-options', f'{revision}^{{commit}}', check=False)
    if resolved.returncode:
        fetched = git(root, 'fetch', '--no-tags', 'origin', revision, check=False)
        if fetched.returncode:
            raise ValueError(f'input base-ref {revision!r} could not be fetched: '
                             + fetched.stderr.decode('utf-8', errors='replace').strip())
        resolved = git(root, 'rev-parse', '--verify', 'FETCH_HEAD^{commit}')
    sha = resolved.stdout.decode().strip()
    result = git(root, 'show', f'{sha}:{records}', check=False)
    if result.returncode:
        listed = git(root, 'ls-tree', '-z', sha, '--', records)
        if not listed.stdout:
            return None
        raise ValueError(f'cannot read records at input base-ref {revision!r}: '
                         + result.stderr.decode('utf-8', errors='replace').strip())
    return decode_records(result.stdout)


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
        write_records(path, updated)
        print(f'{len(updated)} comment-share entries recorded in {records}')
        return 0
    baseline = records_at(root, base_input(base_ref), records)
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
        baseline = head
    found = violations(measured, baseline, limit)
    for path, detail in sorted(found.items()):
        print(f'{path}: {detail}', file=sys.stderr)
    if found:
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
