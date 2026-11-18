# Routine steps that seal evidence (for the owner to add)

The claude.ai routines are the owner's to change. These are the exact steps to append. Each one runs only after
the routine's normal work has finished, never changes, delays or repeats it, and reports only counts, paths and
exit codes. If a permission check blocks any part, the step stops and says which part; it never works around it.

Both routines run in environment `env_01Bv1CnNCGGs8eWB1mqsathv`. That environment needs read access to
`Algorythmos-AI/trading-options-funnel` and push access to its `data` branch. Before the first real night, run the
step once by hand in that environment to prove the push is allowed (some cloud git proxies only allow pushes to
their own branch names).

Exit codes of the seal commands: 0 sealed, or already sealed with identical bytes (a re-run is harmless);
3 bad input; 4 could not write; 5 does not verify or replay; 6 already sealed with different bytes. Any other
code, including 1 or a traceback, means the step itself broke: stop and say so.

## Options levels, after close (`trig_01EUa31y41kKS4WuJqBXJwWN`)

Append after the SHADOW STEP. It must run in the same firing, before the pre-open routine (08:50 New York) adds its
block to the focus file: the seal refuses a focus file that already has it.

> SEAL STEP (added 2026-11-17 at Sam's request; trading-options-funnel plan step S3). Only on a trading day, only after
> every step above has finished, whatever their result, and only if `focus.py pick` wrote tonight's focus file.
> Write nothing under /mnt/project-files; send nothing to the dashboard; push only to the `data` branch of
> trading-options-funnel; do not print or reply with any price, volume or other value.
> 0. `python3 -V` must be 3.12 or newer; if not, stop and say so.
> 1. Code: `rm -rf /tmp/funnel-code && git clone -q --depth 1 https://github.com/Algorythmos-AI/trading-options-funnel /tmp/funnel-code`.
> 2. Evidence: `rm -rf /tmp/funnel-data && git clone -q --branch data --single-branch https://github.com/Algorythmos-AI/trading-options-funnel /tmp/funnel-data`.
> 3. Seal: `cd /tmp/funnel-code && PYTHONPATH=src python3 -m options_funnel.cli seal-focus --root /tmp/funnel-data --routine trig_01EUa31y41kKS4WuJqBXJwWN --levels /mnt/project-files/options-tab/data/levels/<next session>.json --iv-history /mnt/project-files/options-tab/data/iv_history.csv --focus /mnt/project-files/options-tab/data/focus/<next session>.json`.
>    On exit 6 do not correct anything: report it.
> 4. Check: `PYTHONPATH=src python3 -m options_funnel.cli replay-focus /tmp/funnel-data/data/focus/<next session>`. Note its exit code.
> 5. Commit and push, only if step 3 printed "sealed": `cd /tmp/funnel-data && git add data/focus && git -c user.name=skalaliya -c user.email=skalaliya@gmail.com commit -q -m "seal focus <next session>"`, then try `git push -q origin data` up to three times, running `git pull -q --rebase origin data` before each retry.
> 6. Health: `cd /tmp/funnel-code && PYTHONPATH=src python3 -m options_funnel.cli funnel-health --root /tmp/funnel-data --session <next session> --send`. It reads `RADAR_INGEST_SECRET` and `VERCEL_AUTOMATION_BYPASS_SECRET` from the environment and never prints them. Exit 7 with "does not accept" means the dashboard release that knows `stocksdelta/funnel` is not live yet: say so once, do not retry.
> 7. Radar receipts (trading-lab-research DEC-0055 section 1), for the session that just closed: `cd /tmp/funnel-code && PYTHONPATH=src python3 -m options_funnel.cli seal-receipts --root /tmp/funnel-data --routine trig_01EUa31y41kKS4WuJqBXJwWN --date <the session that just closed>`, then commit and push `data/radar-receipts` as in step 5, message "seal radar receipts <date>". It reads the same two secrets. It retries a server error or a timeout itself; exit 7 then means the dashboard refused or does not have the listing route yet: say so once. It refuses a day that is not a regular session (exit 3): skip quietly. Exit 6 means a listing for that day is already sealed and the dashboard now lists something else: say so and change nothing; the first listing stays the record. Run it every regular session, whatever the other steps did: a missing day is a session DEC-0055 cannot count.
> 8. Reply in the thread with one line: "Seal <next session>: seal exit N, replay exit N, pushed yes/no, health exit N, receipts exit N", plus which part was blocked if any. Replay exit 5 means the repository's port and tonight's pick disagree: say so; change nothing.

## Radar edition to dashboard (`trig_01B1TN3d4x7dZ3Wxad2LmYgb`)

As it runs since 2026-11-18. The radar run usually pushes the edition to the dashboard itself, so this routine's own
send normally answers "duplicate": the seal runs on HTTP 200, stored or duplicate.

Retries: if today's outbox file is missing, the routine schedules a retry 20 minutes later, and again at each retry,
until the file appears or it is past 13:45 UTC (08:45 New York); then it tells the coordinator session. Each retry
runs the send, then the SEAL STEP and the DESK STEP below.

> SEAL STEP (added 2026-11-17 at Sam's request; plan step S3). Only when send_outbox.py reported HTTP 200 for
> today's file, stored or duplicate. For a duplicate, `--sent-at` is the UTC time it was confirmed. Same limits and
> steps 0–2 as the options seal step.
> 3. `cd /tmp/funnel-code && PYTHONPATH=src python3 -m options_funnel.cli seal-radar --root /tmp/funnel-data --routine trig_01B1TN3d4x7dZ3Wxad2LmYgb --edition /mnt/project-files/radar-dashboard/outbox/<the file's date>.json --skill /mnt/project-files/skills/pre-market-radar.skill --fetch-directory --http-status 200 --sent-at <UTC ISO time>`.
>    It also downloads that morning's Nasdaq Trader symbol directory (nasdaqlisted.txt, otherlisted.txt), retrying a failed fetch, and records the skill bundle's sha256; DEC-0055 needs both. If the directory cannot be fetched it still seals the edition, prints a warning and records why: say so in the reply, because DEC-0055 cannot count that session. Running it again for the same edition seals nothing and fetches nothing. This step must finish before 09:30 New York.
>    The session is the date in the file's name. A same-day re-send of a changed edition exits 6: then run the same
>    command with `--correction`, which seals it beside the first, never over it.
> 4. Commit and push as in the options step, message "seal radar <date>".
> 5. Update the status line only; reply only if a part was blocked or a command failed.

> DESK STEP (added 2026-11-17; funnel plan Phase 1, display only). Only after the SEAL STEP ended with exit 0 for
> today's edition. Same limits; it sends nothing to the dashboard except step 4. Uses the SEAL STEP's clones.
> 1. Config, three files from one trading-lab-research commit: `rm -rf /tmp/wt-cfg && git clone -q --depth 1 --filter=blob:none --sparse https://github.com/Algorythmos-AI/trading-lab-research /tmp/wt-cfg && git -C /tmp/wt-cfg sparse-checkout set --no-cone /config/events/earnings.csv /config/events/macro_events.csv /config/options_desk.yaml`, then `WT=$(git -C /tmp/wt-cfg rev-parse HEAD)`.
> 2. Seal: `cd /tmp/funnel-code && PYTHONPATH=src python3 -m options_funnel.desk seal --root /tmp/funnel-data --session <date> --earnings /tmp/wt-cfg/config/events/earnings.csv --macro /tmp/wt-cfg/config/events/macro_events.csv --desk-config /tmp/wt-cfg/config/options_desk.yaml --wt-commit $WT --routine trig_01B1TN3d4x7dZ3Wxad2LmYgb`. Exit 6 only after a radar correction: run again with `--correction`.
> 3. Commit and push `data/radar-desk`, only if step 2 printed "sealed", message "seal radar desk <date>".
> 4. Send: `cd /tmp/funnel-code && PYTHONPATH=src python3 -m options_funnel.desk send --root /tmp/funnel-data --session <date>`. Exit 0 stored or duplicate; exit 7 with "does not accept" means the dashboard release with `stocksdelta/radar-desk` is not live: say so once, do not retry.
> 5. Fold the result into the seal step's one-line reply ("desk seal exit N, pushed yes/no, send exit N").

## Permissions in the cloud routines

The routines run in auto mode, where a classifier checks each command. Steps written in the routine's own prompt pass
it; the same command requested by another session may be blocked, and the routine then stops as instructed. The
routine needs `Algorythmos-AI/trading-options-funnel` attached as a repository to push to `data`.

## Protecting the branches

Since 2026-11-18 two repository rulesets protect the branches:

- `protect-data`: `data` cannot be deleted or force-pushed, by anyone. Ordinary pushes from the routines still go in.
- `main-via-pr`: `main` changes only through a pull request, merged with a merge commit once the CI `check` job
  passes. An admin may bypass this on a pull request only, never with a direct push.

The organisation's `protect-default-branches` also blocks deletion and force pushes on `main`.

## Why the sealed copies matter

The dashboard keeps only the latest radar edition per date, and the picker's focus file in the project Library is
edited in place by the pre-open pass the next morning. A sealed copy with its sha256, committed the same evening,
is the only record of what was known before the open. Every later evaluation of the Radar or the picker reads
from it.
