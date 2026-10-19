# comment-ratchet

A composite action that gates the comment share of a repository's code: the
UTF-8 bytes of comments over all non-whitespace bytes, per tracked source file
(Python, JavaScript, YAML, shell). A file above the target (15% by default)
may not rise above its recorded share, and the recorded shares only tighten.

Extracted from [claudit](https://github.com/Nitjsefnie/claudit)'s
`scripts/ci/comment_share.py` and `comment_baseline.py`. Under construction:
see the open issues.
