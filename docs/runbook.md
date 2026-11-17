# Runbook

## The gate

`make check` runs the restricted-path guard, ruff (lint and format check), mypy `--strict`, pytest and gitleaks
over every ref. `make hooks` installs pre-commit for commits and `scripts/pre-push` for pushes. For each pushed ref, the
pre-push hook runs the restricted-path guard on every commit the remote does not have yet, each on its own tree,
so a file added and deleted later is still caught. Then it runs `make check` on the tip in a temporary worktree,
so it judges exactly what is pushed, not the working copy. It needs `pre-commit` and `gitleaks` on PATH (`uv tool install pre-commit`,
`brew install gitleaks`).

The organisation's Actions billing is failing as of 2026-11-17, so `.github/workflows/ci.yml` (the same gate plus
actionlint, with actions pinned by commit and gitleaks checksum-verified) does not start yet. Until it does, the
pre-push hook is the gate: never push with `--no-verify`.

## The pinned trading-lab-research tree

`wt.pin` holds one 40-character commit of `Algorythmos-AI/trading-lab-research`, taken from its `main` branch
(released code, the code that runs in production). `make wt-tree` fetches exactly that commit into `.wt-tree/`
(git-ignored). It refuses a tree whose `HEAD` differs from the pin, and a tree with any change in it, ignored
files included, because `wt` reads repository-relative files.

`wt` needs third-party packages, so code that imports it runs in a second venv built from trading-lab-research's
own hashed lock at the pin: `make wt-env`, then `PYTHONPATH=.wt-tree/src .venv-wt/bin/python ...`. The checks in
`make check` never import `wt`.

To bump the pin: change `wt.pin` in a pull request to a newer commit on trading-lab-research's `main`, run
`make wt-tree wt-env check`, and say in the pull request which trading-lab-research changes the bump brings in.

## Times

Every schedule is in New York time. Non-sessions come from the market calendar in the pinned tree, never from a
weekday rule.

## The data branch

The routines commit sealed evidence to `data` from claude.ai, where no pre-push hook runs. The guard therefore
holds the rule itself: on `data`, every path outside `data/` must be byte-identical to `main`
(`GATE_MAIN_REF`, default `origin/main`), and CI runs on every push to `data` once Actions can start. To bring a
change from `main` into `data`, merge `main` into `data`; nothing else may change there.
