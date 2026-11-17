"""Command line (`python -m options_funnel.cli ...`):

- `focus-pick LEVELS IV_HISTORY --candidates OUT.jsonl [--focus OUT.json]`: score every desk name the way the cloud
  picker does; one candidate record per name and, if asked, the focus document (without `picked_at`/`premarket`).
- `seal-focus --root R --levels L --iv-history IV --focus F [--replaces FOLDER]`: seal one night's picker evidence.
- `seal-radar --root R --session S --edition E [--replaces FOLDER]`: seal one radar edition as sent.
- `verify FOLDER...`: check sealed folders against their manifests.
- `replay-focus FOLDER...`: run the port on sealed inputs and compare with the sealed focus file.

All checks run before anything is written. Each file is written to a temporary file, flushed to disk, then linked
into place, which fails if the target exists: a run never overwrites, and never leaves a partial file.

Exit codes: 0 done; 1 an output already exists; 3 bad input (missing file, bad CSV header, a name the record
cannot describe); 4 an output could not be written (missing folder, no permission, disk); 5 a sealed folder does
not verify or does not replay. On any failure after writing has started, every output this run created is removed.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from options_funnel.blind import BlindRuleError, assert_blind
from options_funnel.candidates import dump_jsonl
from options_funnel.focus_pick import pick
from options_funnel.replay import replay_focus
from options_funnel.seal import SealError, iv_rows_for, seal, sha256, verify

IV_COLUMNS = frozenset({"session", "ticker", "call_vol", "put_vol", "avg_call_vol", "avg_put_vol"})
EXIT_EXISTS = 1
EXIT_INPUT = 3
EXIT_WRITE = 4
EXIT_MISMATCH = 5


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


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def seal_focus(args: argparse.Namespace) -> int:
    try:
        levels_bytes = args.levels.read_bytes()
        iv_bytes = args.iv_history.read_bytes()
        focus_bytes = args.focus.read_bytes()
        levels = json.loads(levels_bytes.decode("utf-8-sig"))
        focus = json.loads(focus_bytes.decode("utf-8-sig"))
        session, built = levels["session"], levels["built_from"]
        if focus.get("session") != session:
            raise InputError(f"focus file is for {focus.get('session')!r}, levels file for {session!r}")
        files = {"levels.json": levels_bytes, "iv_rows.csv": iv_rows_for(iv_bytes, built), "focus.json": focus_bytes}
    except (OSError, KeyError, TypeError, SealError, json.JSONDecodeError) as error:
        raise InputError(f"{type(error).__name__}: {error}") from error
    provenance = {"iv_history_sha256": sha256(iv_bytes), "iv_history_size": len(iv_bytes), "built_from": built}
    return _seal(args, "focus", session, files, provenance)


def seal_radar(args: argparse.Namespace) -> int:
    try:
        edition = args.edition.read_bytes()
        json.loads(edition.decode("utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise InputError(f"{type(error).__name__}: {error}") from error
    return _seal(args, "radar", args.session, {"edition.json": edition}, {})


def _seal(args: argparse.Namespace, kind: str, session: str, files: dict[str, bytes], prov: dict[str, object]) -> int:
    try:
        sealed = seal(args.root, kind, session, files, _now(), prov, args.replaces)
    except SealError as error:
        if "already sealed" in str(error):
            print(str(error), file=sys.stderr)
            return EXIT_EXISTS
        raise InputError(str(error)) from error
    except OSError as error:
        print(f"could not write: {error}", file=sys.stderr)
        return EXIT_WRITE
    print(f"sealed {sealed.folder}")
    return 0


def verify_cmd(args: argparse.Namespace) -> int:
    problems = [p for folder in args.folders for p in verify(folder)]
    for problem in problems:
        print(problem, file=sys.stderr)
    if not problems:
        print(f"verified {len(args.folders)} sealed folder(s)")
    return EXIT_MISMATCH if problems else 0


def replay_cmd(args: argparse.Namespace) -> int:
    status = 0
    for folder in args.folders:
        diffs = replay_focus(folder)
        if diffs:
            status = EXIT_MISMATCH
            for diff in diffs:
                print(f"{folder.name}: {diff}", file=sys.stderr)
        else:
            print(f"{folder.name}: the port reproduces the sealed picks")
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="options_funnel.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    fp = sub.add_parser("focus-pick", help="score every desk name the way the cloud picker does")
    fp.add_argument("levels", type=Path)
    fp.add_argument("iv_history", type=Path)
    fp.add_argument("--candidates", type=Path, required=True)
    fp.add_argument("--focus", type=Path)
    fp.set_defaults(run=focus_pick)
    sf = sub.add_parser("seal-focus", help="seal one night's picker inputs and output")
    sf.add_argument("--root", type=Path, required=True)
    sf.add_argument("--levels", type=Path, required=True)
    sf.add_argument("--iv-history", type=Path, required=True)
    sf.add_argument("--focus", type=Path, required=True)
    sf.add_argument("--replaces")
    sf.set_defaults(run=seal_focus)
    sr = sub.add_parser("seal-radar", help="seal one radar edition as sent")
    sr.add_argument("--root", type=Path, required=True)
    sr.add_argument("--session", required=True)
    sr.add_argument("--edition", type=Path, required=True)
    sr.add_argument("--replaces")
    sr.set_defaults(run=seal_radar)
    vf = sub.add_parser("verify", help="check sealed folders against their manifests")
    vf.add_argument("folders", type=Path, nargs="+")
    vf.set_defaults(run=verify_cmd)
    rp = sub.add_parser("replay-focus", help="re-run the port on sealed nights and compare")
    rp.add_argument("folders", type=Path, nargs="+")
    rp.set_defaults(run=replay_cmd)
    args = parser.parse_args(argv)
    try:
        result: int = args.run(args)
        return result
    except (InputError, ValueError) as error:
        print(f"bad input: {error}", file=sys.stderr)
        return EXIT_INPUT


if __name__ == "__main__":
    raise SystemExit(main())
