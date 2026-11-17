# Routine steps that seal evidence (for the owner to add)

The claude.ai routines are the owner's to change. These are the exact steps to append. Each one runs only after
the routine's normal work has finished, never changes, delays or repeats it, and reports only counts, paths and
exit codes. If a permission check blocks any part, the step stops and says which part; it never works around it.

Both routines run in environment `env_01Bv1CnNCGGs8eWB1mqsathv`. That environment needs read access to
`Algorythmos-AI/trading-options-funnel` and push access to its `data` branch (and nothing else in that repository).

## Options levels, after close (`trig_01EUa31y41kKS4WuJqBXJwWN`)

Append after the SHADOW STEP:

> SEAL STEP (added <date> at Sam's request; plan step S3 of trading-options-funnel). Only on a trading day, only
> after every step above has finished, whatever their result, and only if `focus.py pick` wrote tonight's focus
> file. Write nothing under /mnt/project-files; send nothing to the dashboard; never push to any branch but
> `data`; do not print or reply with any price, volume or other value.
> 1. Code: `git clone -q --depth 1 https://github.com/Algorythmos-AI/trading-options-funnel /tmp/funnel-code`
>    (or `git -C /tmp/funnel-code pull -q --ff-only` if it exists).
> 2. Evidence: `git clone -q --branch data --single-branch --depth 5 https://github.com/Algorythmos-AI/trading-options-funnel /tmp/funnel-data`
>    (or `git -C /tmp/funnel-data pull -q --ff-only`).
> 3. Seal: `cd /tmp/funnel-code && PYTHONPATH=src python3 -m options_funnel.cli seal-focus --root /tmp/funnel-data --levels /mnt/project-files/options-tab/data/levels/<next session>.json --iv-history /mnt/project-files/options-tab/data/iv_history.csv --focus /mnt/project-files/options-tab/data/focus/<next session>.json`.
>    Exit 1 means tonight is already sealed: stop the step quietly.
> 4. Check: `PYTHONPATH=src python3 -m options_funnel.cli replay-focus /tmp/funnel-data/data/focus/<next session>`. Note its exit code.
> 5. Commit and push: `cd /tmp/funnel-data && git add data/focus/<next session> && git -c user.name=skalaliya -c user.email=skalaliya@gmail.com commit -q -m "seal focus <next session>" && git push -q origin data`.
> 6. Reply in the thread with one line: "Seal <next session>: sealed (exit N), replay exit N, pushed yes/no", plus which part was blocked if any. Replay exit 5 means the repository's port and tonight's pick disagree: say so; do not change anything.

## Radar edition to dashboard (`trig_01B1TN3d4x7dZ3Wxad2LmYgb`)

Append at the end:

> SEAL STEP (added <date>; plan step S3 of trading-options-funnel). Only when send_outbox.py reported HTTP 200 for
> today's file. Same limits as the options seal step.
> 1. and 2. as in the options routine (clone `/tmp/funnel-code` and the `data` branch into `/tmp/funnel-data`).
> 3. `cd /tmp/funnel-code && PYTHONPATH=src python3 -m options_funnel.cli seal-radar --root /tmp/funnel-data --session <today, UTC date> --edition /mnt/project-files/radar-dashboard/outbox/<today>.json`.
>    Exit 1 means today is already sealed; on a same-day re-send add `--replaces <today>` so the new edition is
>    sealed beside the first, never over it.
> 4. Commit and push to `data` as above, message "seal radar <today>".
> 5. Update the status line only; reply only if a part was blocked or a command failed.

## Why the sealed copies matter

The dashboard keeps only the latest radar edition per date, and the picker's files in the project Library can be
replaced. A sealed copy with its sha256, committed at the time, is the only record that shows what was known
before the open. Every later evaluation of the Radar or the picker reads from it.
