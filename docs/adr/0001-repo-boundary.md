# ADR 0001: what lives here and what stays in trading-lab-research

Status: accepted 2026-11-17 (owner, in the planning session that created this repository).

## Context

The owner asked for the Radar → Options selection work to live in its own repository, so trading-lab-research is
not crowded with it. A review of trading-lab-research found three facts that limit what can move:

1. `wt.options.select` hashes its own repository paths into `picker_ref`, DEC-0046 pins the sha256 of
   `config/options_select.yaml`, and several hypothesis, experiment and specification records pin script paths.
   Moving any of them breaks a registered record.
2. The dashboard's engine and signal hashes (`ENGINE_VERSION`, `SIGNAL_CODE_HASH`) cover `radar.ts`, `options.ts`
   and their generated types. Changing the Radar or Options schema moves both hashes.
3. EXP-0032 and DEC-0047 name `trading-focus-card-ledger` and its blind test as preconditions.

## Decision

- Nothing registered moves. This repository reads trading-lab-research at a pinned commit (`wt.pin`) as a read-only
  tree with `PYTHONPATH=.wt-tree/src`, in a venv built from that commit's own hashed runtime lock (`make wt-env`).
The pin is a commit on trading-lab-research's `main`, so it is released code. It does not pip-install `wt`: `wt.core.config` finds its root from the file
  path, and `wt.risk.events` and `wt.options.select` read repository-relative files. Both repositories belong to the
  stocks and options platform, so this is not a cross-platform import.
- The dashboard gets one new document kind, `stocksdelta/funnel`, in new files outside both hash closures, signed
  with the existing `radar` key id (a new key id would be broader under `ingest.ts`).
- The focus card ledger stays in its own repository until EXP-0032 is read.
- The jobs are claude.ai routines, because the IBKR connector, the ingest key and the Vercel bypass secret already
  live there. GitHub Actions runs checks only and holds no secrets.

## Consequences

- Bumping `wt.pin` is a pull request that re-runs the parity tests.
- Anything that changes how names are selected is still a hypothesis, experiment and decision in
  trading-lab-research first.
