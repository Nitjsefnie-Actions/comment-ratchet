#!/usr/bin/env python3
"""Fold the tests workflow's required legs into one fail-closed verdict."""
import json
import os
import sys

LEGS = ('suites', 'shellcheck', 'comment-share', 'size-baseline', 'codeql', 'secrets', 'audit')


def main() -> int:
    try:
        payload = os.environ.get('COMMENT_RATCHET_NEEDS_JSON')
        needs = json.loads(os.environ['NEEDS_JSON'] if payload is None else payload)
        if not isinstance(needs, dict):
            raise ValueError('needs must be an object')
        failed = [name for name in LEGS if not isinstance(needs.get(name), dict)
                  or needs[name].get('result') != 'success']
        if failed:
            print('CI legs not successful: ' + ', '.join(failed), file=sys.stderr)
            return 1
        print('All required CI legs succeeded')
        return 0
    except (KeyError, ValueError) as error:
        print(f'CI aggregate failed: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
