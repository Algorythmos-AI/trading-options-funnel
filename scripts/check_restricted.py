"""Refuse files that must never be tracked, in every commit being pushed, not only the last one.

Rules:
- Everywhere: secrets (.env files, except .env.example), PDFs, the kill switch, trading-lab-research's restricted
  folders (research/, knowledge/), the local pinned tree and scratch (.wt-tree, var).
- On any branch but `data`: no evidence (data/).
- On `data`: evidence under data/ only. Every other path must be byte-identical to the same path on main
  (GATE_MAIN_REF, default origin/main), so the data branch can never carry code or docs of its own.

Usage:
  check_restricted.py                    the checked-out tree (GATE_BRANCH, else the current branch)
  check_restricted.py --range A..B       every commit in the range, each judged on its own tree
A detached HEAD in CI counts as "not data", which is the strict side.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

EVERYWHERE = re.compile(
    r"(?i)(\.pdf$|(^|/)\.env($|\.(?!example$))|(^|/)KILL$|(^|/)knowledge(/|$)|(^|/)research(/|$)|^(\.wt-tree|var)(/|$))"
)
EVIDENCE = re.compile(r"^data(/|$)")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def blocked(path: str, branch: str) -> str | None:
    if EVERYWHERE.search(path):
        return "never tracked in this repository"
    if branch != "data" and EVIDENCE.search(path):
        return "evidence belongs on the data branch only"
    return None


def tree(rev: str) -> dict[str, str]:
    """path -> blob id for every file in `rev`."""
    out: dict[str, str] = {}
    for line in git("ls-tree", "-r", "-z", "--full-tree", rev).split("\0"):
        if line:
            meta, path = line.split("\t", 1)
            out[path] = meta.split()[2]
    return out


def problems(rev: str, branch: str, main_ref: str) -> list[str]:
    files = tree(rev)
    found = [f"{path}: {why}" for path in files if (why := blocked(path, branch))]
    if branch == "data":
        main = tree(main_ref)
        for path, blob in files.items():
            if not EVIDENCE.search(path) and main.get(path) != blob:
                found.append(f"{path}: the data branch may change only data/ (differs from {main_ref})")
        found += [f"{path}: missing on the data branch" for path in main if path not in files]
    return found


def main(argv: list[str]) -> int:
    branch = os.environ.get("GATE_BRANCH") or git("rev-parse", "--abbrev-ref", "HEAD").strip()
    main_ref = os.environ.get("GATE_MAIN_REF", "origin/main")
    if argv[:1] == ["--range"] and len(argv) == 2:
        revs = git("rev-list", "--reverse", argv[1]).split()
    elif not argv:
        revs = ["HEAD"]
    else:
        print("usage: check_restricted.py [--range A..B]", file=sys.stderr)
        return 2
    status = 0
    for rev in revs:
        for problem in problems(rev, branch, main_ref):
            print(f"restricted: {rev[:12]} {problem} (branch {branch}; see AGENTS.md)", file=sys.stderr)
            status = 1
    return status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
