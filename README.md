# trading-options-funnel

**Sealed evidence, replay and measurement for the trading lab's options selection funnel.**

```
Radar (pre-market research)  →  Options desk focus names (4 of 12)  →  the board's checklist
```

The lab chooses what to trade in three stages, and each stage used to choose on its own, with no record of what it
saw or left out. This repository gives every stage a record:

- **Sealed evidence.** What each routine used or sent is kept byte for byte with its sha256, on the `data` branch,
  the same night, so it cannot change after the fact.
- **Replay.** Any sealed night can be re-run from the repository alone and must give the same answer.
- **Measurement.** Each stage can later be tested against the names it left out, under a record registered before
  any result is seen.

Research only. Nothing here places, previews or suggests an order. Paper trading is the only kind the lab runs.

## Status

| Stage | What runs | What this repository adds | Status |
|---|---|---|---|
| **Radar** | The pre-market research routine publishes an edition to the dashboard | `seal-radar`: the edition as sent, that morning's Nasdaq Trader symbol directory and the Radar skill's sha256 | Live since 2026-11-17 |
| **Focus names** | The after-close routine picks 4 of the 12 desk names (`focus.py pick`) | `seal-focus` and `replay-focus`: the picker's inputs and output, re-run by a port that must agree | Live since 2026-11-18 |
| **Radar desk file** | Built after the Radar seal, for the 12 desk names | `desk seal` and `desk send`: closed flag codes beside the board (Phase 1, display only) | First run 2026-11-18 |
| **Board** | The dashboard's checklist for the focus names | Shows the desk file's flags under the desk. It changes no pick, no board state and no rule | Live (release 2026-11-17) |
| **Receipts** | The dashboard keeps every radar copy with its receipt time | `seal-receipts`: that listing, sealed after each close | Live since 2026-11-17 |
| **Health** | `stocksdelta/funnel` on the dashboard | `funnel-health`: what was sealed and whether it replays | Live |

The Radar does not choose the focus names. Wiring it in is a strategy change. It needs its own hypothesis,
experiment and decision record in trading-lab-research first, and the Radar's forward evaluation (EXP-0038) to read.

## A night, step by step

Times are New York (the sessions' clock). Sydney is 16 hours ahead while New York is on standard time and Sydney
on summer time.

| New York | Routine (claude.ai) | Writes |
|---|---|---|
| 16:52 (day before) | **Options levels, after close.** Levels, focus pick, card, then the seal step | `data/focus/<S>`, `data/radar-receipts/<S-1>`, the funnel health document |
| ~05:30 | **Pre-market Radar.** Research, the six IBKR watch lists, the edition to the dashboard | The radar edition (dashboard) |
| 06:12, then every 20 min until 08:45 | **Radar edition to dashboard.** Confirms the edition is stored, then the seal step and the desk step | `data/radar/<S>`, `data/radar-desk/<S>`, the desk document |
| 08:50 | **Options focus, pre-open.** The card for the four names | (dashboard) |
| 09:00 | Dashboard feed watch expects the desk document; the board shows its flags | — |
| 09:30 | The open. Everything above must be sealed by now | — |

The exact routine steps are in [`docs/routine-steps.md`](docs/routine-steps.md). Each step runs only after the
routine's own work is done, reports only counts, paths and exit codes, and stops if a permission check blocks it.

## What is sealed

```
data branch (sealed evidence only; never edited, never force-pushed)
data/
  radar/<S>/          edition.json, nasdaqlisted.txt, otherlisted.txt, MANIFEST.json
  radar-receipts/<S>/ listing.json, MANIFEST.json
  focus/<S>/          levels.json, iv_rows.csv, focus.json, MANIFEST.json
  radar-desk/<S>/     desk.json, earnings.csv, macro_events.csv, options_desk.yaml, MANIFEST.json
```

- Each `MANIFEST.json` holds the sha256 and size of every file, the time it was sealed, the routine, the code commit
  that sealed it and the provenance (for the Radar, the skill bundle's sha256).
- Re-sealing the same bytes does nothing. Different bytes go into a correction folder `<S>.rN` that names what it
  replaces. The first folder is never overwritten.
- `<S>` is the New York session the files are for.

## Commands

```
python -m options_funnel.cli seal-radar | seal-focus | seal-receipts   # seal one night's evidence
python -m options_funnel.cli verify FOLDER...                          # check folders against their manifests
python -m options_funnel.cli replay-focus FOLDER                       # re-run the picker port, compare
python -m options_funnel.cli funnel-health --session S [--send]        # the dashboard's health document
python -m options_funnel.cli exp0038-status                            # EXP-0038: counted sessions (counts only)
python -m options_funnel.desk seal | check | send | verify             # the Radar desk file
```

Exit codes: 0 done (or already sealed identically) · 3 bad input · 4 could not write · 5 does not verify or replay
· 6 already sealed with different bytes · 7 the dashboard refused or does not know the document yet.

## The Radar desk file

One file per session for the 12 desk names. For each name it records whether it is a focus name, the Radar's lists
(as codes), grades and risk tag, the Radar's weekly levels beside the desk's own, and earnings and high-impact macro
events between the open and the next open. It also sets closed flag codes:

`EARNINGS_BEFORE_OPEN` · `EARNINGS_IN_HOLD` · `EARNINGS_SOURCES_DISAGREE` · `MACRO_HIGH_IN_HOLD` ·
`RADAR_RISK_TAG` · `RADAR_NOT_COVERED` · `LEVELS_DISAGREE`

- The file holds no free text and no outcome of any kind.
- `earnings.csv` in trading-lab-research stays the board's source of earnings dates. The Radar's calendar is only a
  cross-check, and a disagreement is flagged, not resolved.
- The dashboard shows the flags for the focus names only (`stocksdelta/radar-desk` v1, pinned in
  [`contracts/`](contracts)).

## Guarantees

- **Paper only, no orders.** No code here talks to a broker.
- **Blind rule** (trading-lab-research DEC-0046 and DEC-0047). Nothing written or published here carries an outcome
  for a name that was not picked. `assert_blind` guards every document, and each document type checks its exact key
  set.
- **Frozen analysis code.** EXP-0038's read runs only from a checkout of commit `eaff05f` with nothing changed. Later
  work adds new files and leaves those files as they are.
- **Pre-registration.** A strategy or evaluation change is registered in trading-lab-research before any result it
  affects is seen.
- **Secrets stay in the routines.** The routines hold the dashboard keys. This repository's CI runs with none.

## How it fits

| Piece | Where |
|---|---|
| Registered selection code, records (DEC, EXP, HYP), dashboard | [`Algorythmos-AI/trading-lab-research`](https://github.com/Algorythmos-AI/trading-lab-research), read at the commit in `wt.pin` |
| Focus card ledger (EXP-0032) | `Algorythmos-AI/trading-focus-card-ledger`, separate on purpose |
| Nightly jobs | claude.ai cloud routines; they seal what they send to this repository's `data` branch |
| This repository | sealing, verify and replay, the picker port, the desk file, the health document, EXP-0038's analysis code |

Why the boundary is drawn there: [`docs/adr/0001-repo-boundary.md`](docs/adr/0001-repo-boundary.md).

## Repository layout

```
src/options_funnel/   seal, verify and replay; focus_pick (the picker port); desk; health and publish;
                      exp0038* (frozen analysis); blind (the guard); calendar (pinned New York sessions)
tests/                pytest: synthetic inputs, generator vectors, parity with the picker
contracts/            dashboard schemas pinned by sha256, and the market calendar
docs/                 runbook, routine steps, Radar method audit, ADRs
radar/                sha256 manifest of the copied Radar skill files
wt.pin                the trading-lab-research commit read here
```

## Development

Needs `uv`, `git`, `pre-commit` and `gitleaks` on PATH.

```
make bootstrap   # .venv from the hashed dev lock
make hooks       # pre-commit on commit, the full gate on push
make check       # ruff, mypy --strict, pytest, gitleaks: the same gate as CI
make wt-env      # only for code that imports trading-lab-research (see docs/runbook.md)
```

Pull requests go into `main` and are merged with a merge commit by the owner, once CI is green. Routines push only
to `data`. Contributor rules, data classes and the boundaries above are in [`AGENTS.md`](AGENTS.md). Running and
debugging: [`docs/runbook.md`](docs/runbook.md).

## Owner steps still open

- Make the repository private (EXP-0038 precondition 1).
- Protect `data` (no deletion, no force push) and `main` (pull request required) with rulesets.
- Accept DEC-0055 in trading-lab-research.

EXP-0038's first counted session is the first regular session after the last of its preconditions is written in its
README.
