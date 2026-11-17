"""Command line: `python -m options_funnel.cli focus-pick LEVELS IV_HISTORY --candidates OUT.jsonl [--focus OUT.json]`.

Reads one levels file and the iv_history CSV, writes one candidate record per name (JSON Lines) and, if asked,
the focus document the cloud routine would write (without `picked_at` and `premarket`).

All checks run before anything is written. Each file is written to a temporary file, flushed to disk, then linked
into place, which fails if the target exists: a run never overwrites, and never leaves a partial file.

Exit codes: 0 done; 1 an output already exists; 3 bad input (missing file, bad CSV header, a name the record
cannot describe); 4 an output could not be written (missing folder, no permission, disk). On any failure after
writing has started, every output this run created is removed again.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
from pathlib import Path

from options_funnel.blind import BlindRuleError, assert_blind
from options_funnel.candidates import dump_jsonl
from options_funnel.focus_pick import pick

IV_COLUMNS = frozenset({"session", "ticker", "call_vol", "put_vol", "avg_call_vol", "avg_put_vol"})
EXIT_EXISTS = 1
EXIT_INPUT = 3
EXIT_WRITE = 4


class InputError(ValueError):
    """The inputs cannot be judged."""


def read_iv_history(path: Path) -> list[dict[str, str]]:
    """iv_history rows; a byte-order mark is ignored and a header without the needed columns is refused."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        missing = IV_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise InputError(f"{path}: header lacks {sorted(missing)}")
        return list(reader)


def _prepare(path: Path, text: str) -> str:
    """A flushed temporary file beside `path` holding `text`, with the mode a plain new file would get."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        umask = os.umask(0)
        os.umask(umask)
        os.fchmod(fd, 0o666 & ~umask)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        os.unlink(tmp)
        raise
    return tmp


def _place(tmp: str, path: Path) -> None:
    """Move `tmp` to `path`, failing with FileExistsError if `path` exists (no check-then-write race)."""
    try:
        os.link(tmp, path)
    except FileExistsError:
        raise
    except OSError:
        # A filesystem without hard links: fall back to an exclusive create and a copy of the bytes.
        with open(tmp, "rb") as src, open(path, "xb") as dst:
            try:
                dst.write(src.read())
                dst.flush()
                os.fsync(dst.fileno())
            except BaseException:
                path.unlink(missing_ok=True)
                raise


def write_all_new(outputs: list[tuple[Path, str]]) -> None:
    """Write every output or none: temporary files first, then each placed; on any failure, undo what was placed."""
    temps: list[str] = []
    placed: list[Path] = []
    try:
        for path, text in outputs:
            temps.append(_prepare(path, text))
        for (path, _), tmp in zip(outputs, temps, strict=True):
            _place(tmp, path)
            placed.append(path)
    except BaseException:
        for path in placed:
            path.unlink(missing_ok=True)
        raise
    finally:
        for tmp in temps:
            Path(tmp).unlink(missing_ok=True)


def focus_pick(args: argparse.Namespace) -> int:
    outputs = [p for p in (args.candidates, args.focus) if p is not None]
    if len({p.resolve() for p in outputs}) != len(outputs):
        raise InputError("--candidates and --focus must be different files")
    for target in outputs:
        if target.exists():
            print(f"refusing to overwrite {target}", file=sys.stderr)
            return EXIT_EXISTS
    try:
        levels = json.loads(args.levels.read_text(encoding="utf-8-sig"))
        rows = read_iv_history(args.iv_history)
        result = pick(levels, rows)
        texts = [dump_jsonl(result.candidates)]
        if args.focus:
            assert_blind(result.document)
            texts.append(json.dumps(result.document, indent=1, ensure_ascii=False) + "\n")
    except (OSError, KeyError, TypeError, BlindRuleError, json.JSONDecodeError) as error:
        raise InputError(f"{type(error).__name__}: {error}") from error
    try:
        write_all_new(list(zip(outputs, texts, strict=True)))
    except FileExistsError as error:
        print(f"refusing to overwrite {error.filename}", file=sys.stderr)
        return EXIT_EXISTS
    except OSError as error:
        print(f"could not write: {error}", file=sys.stderr)
        return EXIT_WRITE
    picked = sorted((r for r in result.candidates if r.rank is not None), key=lambda r: r.rank or 0)
    print(
        f"focus-pick {levels['session']}: {len(result.candidates)} names, picked "
        + ", ".join(f"#{r.rank} {r.symbol}" for r in picked)
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="options_funnel.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    fp = sub.add_parser("focus-pick", help="score every desk name the way the cloud picker does")
    fp.add_argument("levels", type=Path)
    fp.add_argument("iv_history", type=Path)
    fp.add_argument("--candidates", type=Path, required=True)
    fp.add_argument("--focus", type=Path)
    args = parser.parse_args(argv)
    try:
        return focus_pick(args)
    except (InputError, ValueError) as error:
        print(f"bad input: {error}", file=sys.stderr)
        return EXIT_INPUT


if __name__ == "__main__":
    raise SystemExit(main())
