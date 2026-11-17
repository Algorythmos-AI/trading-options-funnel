"""Command line: `python -m options_funnel.cli focus-pick LEVELS IV_HISTORY --candidates OUT.jsonl [--focus OUT.json]`.

Reads one levels file and the iv_history CSV, writes one candidate record per name (JSON Lines) and, if asked,
the focus document the cloud routine would write (without `picked_at` and `premarket`). Refuses to overwrite.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from options_funnel.blind import assert_blind
from options_funnel.candidates import dump_jsonl
from options_funnel.focus_pick import pick


def _write_new(path: Path, text: str) -> None:
    with open(path, "x", encoding="utf-8") as fh:
        fh.write(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="options_funnel.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    fp = sub.add_parser("focus-pick", help="score every desk name the way the cloud picker does")
    fp.add_argument("levels", type=Path)
    fp.add_argument("iv_history", type=Path)
    fp.add_argument("--candidates", type=Path, required=True)
    fp.add_argument("--focus", type=Path)
    args = parser.parse_args(argv)

    levels = json.loads(args.levels.read_text(encoding="utf-8"))
    with open(args.iv_history, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    result = pick(levels, rows)
    for target in (args.candidates, args.focus):
        if target is not None and target.exists():
            print(f"refusing to overwrite {target}", file=sys.stderr)
            return 1
    try:
        _write_new(args.candidates, dump_jsonl(result.candidates))
        if args.focus:
            assert_blind(result.document)
            _write_new(args.focus, json.dumps(result.document, indent=1, ensure_ascii=False) + "\n")
    except FileExistsError as error:
        print(f"refusing to overwrite {error.filename}", file=sys.stderr)
        return 1
    picked = [r for r in result.candidates if r.rank is not None]
    print(
        f"focus-pick {levels['session']}: {len(result.candidates)} names, picked "
        + ", ".join(f"#{r.rank} {r.symbol}" for r in sorted(picked, key=lambda r: r.rank or 0))
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
