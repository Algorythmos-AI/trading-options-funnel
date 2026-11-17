"""Refuse files that must never be tracked on the branch being checked.

- Everywhere: secrets (.env files, except .env.example), PDFs, the kill switch, and anything from
  trading-lab-research's restricted folders (research/, knowledge/) or the local pinned tree and scratch.
- On `data`: only sealed evidence under data/ (plus the repository's own top-level files it inherits).
- Elsewhere: no evidence (data/) and no copied routine files outside radar/source/.

The branch comes from GATE_BRANCH (set by the pre-push hook for the ref being pushed); otherwise from git. A detached
HEAD (a pull-request checkout in CI) counts as "not data", which is the strict side.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

EVERYWHERE = re.compile(
    r"(?i)(\.pdf$|(^|/)\.env($|\.(?!example$))|(^|/)KILL$|(^|/)knowledge/|(^|/)research/|^\.wt-tree/|^var/)"
)
EVIDENCE = re.compile(r"^data/")


def blocked(path: str, branch: str) -> str | None:
    if EVERYWHERE.search(path):
        return "never tracked in this repository"
    if branch != "data" and EVIDENCE.search(path):
        return "evidence belongs on the data branch only"
    return None


def main() -> int:
    branch = (
        os.environ.get("GATE_BRANCH")
        or subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], check=True, capture_output=True, text=True
        ).stdout.strip()
    )
    files = subprocess.run(["git", "ls-files"], check=True, capture_output=True, text=True).stdout.splitlines()
    bad = [(name, why) for name in files if (why := blocked(name, branch))]
    for name, why in bad:
        print(f"restricted: {name}: {why} (branch {branch}; see AGENTS.md)", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
