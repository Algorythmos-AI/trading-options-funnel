#!/usr/bin/env bash
# Fetch trading-lab-research at the commit in wt.pin into .wt-tree/ for read-only use, and refuse any other commit
# or any change inside it (tracked, untracked or ignored). Needs only git: safe to run before `make bootstrap`.
set -euo pipefail
cd "$(dirname "$0")/.."
url="https://github.com/Algorythmos-AI/trading-lab-research.git"
sha="$(grep -E '^[0-9a-f]{40}$' wt.pin || true)"
if [ -z "$sha" ] || [ "$(wc -l < wt.pin | tr -d ' ')" != 1 ] || [ "$(wc -c < wt.pin | tr -d ' ')" != 41 ]; then
  echo "wt-tree: wt.pin must hold exactly one 40-character lowercase commit and a newline" >&2
  exit 1
fi
[ -d .wt-tree/.git ] || git init -q .wt-tree
if git -C .wt-tree remote get-url origin >/dev/null 2>&1; then
  git -C .wt-tree remote set-url origin "$url"
else
  git -C .wt-tree remote add origin "$url"
fi
if [ "$(git -C .wt-tree rev-parse -q --verify HEAD || true)" != "$sha" ]; then
  git -C .wt-tree fetch -q --depth 1 origin "$sha"
  git -C .wt-tree checkout -q --force --detach FETCH_HEAD
fi
head="$(git -C .wt-tree rev-parse HEAD)"
if [ "$head" != "$sha" ]; then
  echo "wt-tree: HEAD $head is not the pinned $sha" >&2
  exit 1
fi
# Ignored files count too: wt reads repository-relative data such as data/ and var/, which its .gitignore hides.
# The only thing allowed to appear is bytecode Python writes next to a tracked source file.
dirty=""
while IFS= read -r line; do
  path="${line:3}"
  if [[ "$line" == "!! "* && "$path" =~ ^(.*/)__pycache__/([^/]+)\.cpython-[0-9]+\.pyc$ ]] \
     && git -C .wt-tree ls-files --error-unmatch "${BASH_REMATCH[1]}${BASH_REMATCH[2]}.py" >/dev/null 2>&1; then
    continue
  fi
  dirty+="$line"$'\n'
done < <(git -C .wt-tree status --porcelain --ignored --untracked-files=all)
if [ -n "$dirty" ]; then
  echo "wt-tree: .wt-tree has local changes; it must stay a clean read-only copy:" >&2
  echo "$dirty" >&2
  exit 1
fi
echo "wt-tree: trading-lab-research at $sha"
