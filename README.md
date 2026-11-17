# trading-options-funnel

Measures the selection funnel of the trading lab's stocks and options platform:

```
Radar (pre-market research)  →  Options desk focus names (4 of 12)  →  the board's checklist
```

Today each stage chooses on its own and none is measured against the names it left out. This repository makes
every stage leave a record: each candidate, its verdict, a reason code and a hash of the inputs it was judged
on. Then any day can be replayed, and each stage can later be tested for whether it adds anything.

Research only. Nothing here places, previews or suggests an order.

## How it fits

| Piece | Where |
|---|---|
| Registered selection code, records, dashboard | `Algorythmos-AI/trading-lab-research` (read here at the commit in `wt.pin`) |
| Focus card ledger (EXP-0032 names it) | `Algorythmos-AI/trading-focus-card-ledger`, separate on purpose |
| Nightly jobs | claude.ai cloud routines (they hold the keys); they commit what they send to the `data` branch |
| This repository | candidate logs, sealed evidence, replay, the dashboard's `stocksdelta/funnel` document |

The boundary and why it is drawn there: [`docs/adr/0001-repo-boundary.md`](docs/adr/0001-repo-boundary.md).
How to run and debug it: [`docs/runbook.md`](docs/runbook.md). Rules for contributors: [`AGENTS.md`](AGENTS.md).

## Quick start

Needs `uv`, `git`, `pre-commit` and `gitleaks` on PATH.

```
make bootstrap   # .venv with the hashed dev lock
make hooks       # pre-commit on commit, the full gate on push
make check       # the gate
make wt-env      # only for code that imports trading-lab-research (see docs/runbook.md)
```
