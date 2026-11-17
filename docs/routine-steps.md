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

> SEAL STEP (added <date> at Sam's request; trading-options-funnel plan step S3). Only on a trading day, only after
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
> 6. Reply in the thread with one line: "Seal <next session>: seal exit N, replay exit N, pushed yes/no", plus which part was blocked if any. Replay exit 5 means the repository's port and tonight's pick disagree: say so; change nothing.

## Radar edition to dashboard (`trig_01B1TN3d4x7dZ3Wxad2LmYgb`)

Append at the end:

> SEAL STEP (added <date>; trading-options-funnel plan step S3). Only when send_outbox.py reported HTTP 200 for
> the file it sent. Same limits and steps 0–2 as the options seal step.
> 3. `cd /tmp/funnel-code && PYTHONPATH=src python3 -m options_funnel.cli seal-radar --root /tmp/funnel-data --routine trig_01B1TN3d4x7dZ3Wxad2LmYgb --edition /mnt/project-files/radar-dashboard/outbox/<the file's date>.json --http-status 200 --sent-at <the send time, UTC ISO>`.
>    The session is the date in the file's name. A same-day re-send of a changed edition exits 6: then run the same
>    command with `--correction`, which seals it beside the first, never over it.
> 4. Commit and push as in the options step, message "seal radar <date>".
> 5. Update the status line only; reply only if a part was blocked or a command failed.

## Protecting the branches (owner, in GitHub settings)

Nothing on GitHub stops a force-push or a deletion of `data` yet, and a token that can push to `data` can also push
to `main`. Suggested rulesets (Settings → Rules → Rulesets, or the API):

- `data`: block deletion and force pushes.
- `main`: require a pull request before merging; block deletion and force pushes.

## Why the sealed copies matter

The dashboard keeps only the latest radar edition per date, and the picker's focus file in the project Library is
edited in place by the pre-open pass the next morning. A sealed copy with its sha256, committed the same evening,
is the only record of what was known before the open. Every later evaluation of the Radar or the picker reads
from it.
