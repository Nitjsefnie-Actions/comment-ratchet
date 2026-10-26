"""The README install block, asserted and run as written."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.test_action import metadata_env
from tests.test_ci_comment_baseline import commit, git, init_repo, write_records

ROOT = Path(__file__).resolve().parents[1]

def install_block():
    import re
    import yaml
    text = (ROOT / 'README.md').read_text()
    fence = re.search(r'```yaml\n(.*?)```', text, re.S)
    assert fence, 'the README install block is the first yaml fence'
    return fence.group(1), yaml.load(fence.group(1), Loader=yaml.BaseLoader)


def test_install_block_triggers_only_pull_requests():
    import re
    raw, block = install_block()
    assert list(block['on']) == ['pull_request']
    assert not block['on']['pull_request']
    # The default branch is resolved at run time: no branch literal and no
    # push trigger anywhere in the block as a consumer saves it.
    for literal in ('push', 'main', 'branches'):
        assert literal not in raw
    checkout = block['jobs']['comment-share']['steps'][0]
    assert checkout['with']['ref'] == '${{ github.event.pull_request.head.sha }}'
    assert '||' not in checkout['with']['ref']
    import yaml as metadata
    inputs = metadata.safe_load((ROOT / 'action.yml').read_text())['inputs']
    action_step = block['jobs']['comment-share']['steps'][1]
    assert action_step['with'] == {name: str(item['default'])
                                   for name, item in inputs.items()}
    for step in block['jobs']['comment-share']['steps']:
        assert 'uses' not in step or step['uses'].count('@') == 1
    # The SHA-and-version-comment pin style, on the raw fence: the YAML
    # parser strips the comment. Never assert which sha or version.
    import re as raw_re
    pins = raw_re.findall(r'@[0-9a-f]{40} # v\d+\.\d+\.\d+', raw)
    assert len(pins) == 2, pins


def test_install_block_check_on_a_master_default_consumer(tmp_path):
    from tests.test_action import metadata_env
    from tests.test_ci_comment_baseline import init_repo, commit, git, write_records
    _, block = install_block()
    raw_inputs = block['jobs']['comment-share']['steps'][1]['with']
    repo = tmp_path / 'consumer'
    repo.mkdir()
    init_repo(repo)
    source = repo / 'a.py'
    # 40 comments (# + 39 c) + 60 code (x) = (40, 100), above the target.
    source.write_text('#' + 'c' * 39 + '\n' + 'x' * 60 + '\n')
    write_records(repo, {'a.py': [40, 100]})
    base = commit(repo, 'seed a file above the target')
    git(repo, 'branch', '-M', 'master')
    # The runner substitutes the with: expressions; resolve them as it would.
    inputs = {name: (base if value == '${{ github.event.pull_request.base.sha }}' else value)
              for name, value in raw_inputs.items()}
    # The env block of the action's run step, from the block's own with:.
    env = {**metadata_env(repo, inputs=inputs),
           'GITHUB_EVENT_NAME': 'pull_request', 'GITHUB_REF': 'refs/pull/1/merge',
           'COMMENT_RATCHET_DEFAULT_BRANCH': 'master'}

    def run():
        return subprocess.run([sys.executable, '-I', str(ROOT / 'run.py')],
                              cwd=repo, env=env, capture_output=True, text=True)
    # A pull request that removes two comment bytes at a lower share must
    # carry the record tighten lowers: (38, 98).
    source.write_text('#' + 'c' * 37 + '\n' + 'x' * 60 + '\n')
    commit(repo, 'pr head removes comment bytes')
    result = run()
    assert result.returncode == 1
    assert '~ a.py: required [38, 98], recorded [40, 100]' in result.stderr
    write_records(repo, {'a.py': [38, 98]})
    commit(repo, 'carry the lowered record')
    assert run().returncode == 0
