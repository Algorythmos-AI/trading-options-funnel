# AGENTS.md: rules for automated contributors in trading-options-funnel

Read the org rules first (`Algorythmos-AI/algorythmos-handbook`: `AGENTS.md` and `catalog.yaml`). This file adds the
rules for this repository. Where the two conflict, the handbook's "Never" sections win.

## What this repository is

The measuring side of the stocks and options platform's selection funnel: Radar research → the Options desk's
focus names → the board. It logs every candidate at every stage with a verdict and a reason, seals what the
research routines send, and publishes one signed document to the trading-lab-research dashboard.

It is **research only**. It places, previews and suggests no orders, holds no broker credentials and never will.

## Hard rules

1. **trading-lab-research owns the registered code and the records.** This repository reads that repository at the
   commit in `wt.pin` (`scripts/wt-tree.sh`), as a read-only tree. It never copies, moves or edits code that a
   research record pins (`wt.options.select`, `levels`, `rules`, `config/options_select*.yaml`, ...). Hypotheses,
   experiments and decision records live in `trading-lab-research/research/`, never here.
2. **Blind rule (trading-lab-research DEC-0046, DEC-0047, EXP-0032).** Until EXP-0032's read:
   - never compute, store or show an outcome (R, result, hit, P&L, return) for a focus name that was not picked;
   - never compare the old picker's names with Stage 1's names;
   - `options_funnel.blind.assert_blind` runs on everything this repository writes or publishes.
3. **Pre-registration.** Any evaluation of the Radar or of a selection stage needs a hypothesis and an experiment
   in trading-lab-research before its first number is computed.
4. **Evidence is sealed.** Files the routines commit to the `data` branch are evidence: never edit or re-format them.
   A correction is a new file that names the one it replaces.
5. **No secrets in git.** Keys live in the claude.ai routine environment. GitHub Actions runs checks only and holds
   no secrets.
6. **No AI attribution anywhere** (org decision D-016). Author: `skalaliya <skalaliya@gmail.com>`.

## Data classes

| Class | Examples | May be published to the dashboard |
|---|---|---|
| PRIVATE | copied Radar routine files (`radar/source/`), sealed editions, levels inputs, candidate logs | Never as content; counts and freshness only |
| SAFE | counts, ids, statuses, dates, hashes | Yes, behind the owner's login |

Nothing from `trading-lab-research/research/`, its specifications or `knowledge/` is ever copied here.

## Branches

`main` takes pull requests only. `data` is an orphan branch holding only `data/`, the routines' sealed evidence. Work in a feature
branch, run `make check`, open a pull request into `main`.

## Everyday commands

```
make bootstrap   # .venv from requirements-dev.lock.txt
make check       # restricted-path guard + ruff + mypy --strict + pytest + gitleaks: the gate
make hooks       # pre-commit on commit; scripts/pre-push guards every new commit and gates the tip
make wt-tree     # fetch trading-lab-research at the pinned commit into .wt-tree/
make wt-env      # .venv-wt with trading-lab-research's own runtime lock, for code that imports wt
```

Until the organisation's Actions billing works, the pre-push hook is the only gate: install it and never push with
`--no-verify`.
