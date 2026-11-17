"""Command line (`python -m options_funnel.cli ...`):

- `focus-pick LEVELS IV_HISTORY --candidates OUT.jsonl [--focus OUT.json]`: score every desk name the way the cloud
  picker does; one candidate record per name and, if asked, the focus document (without `picked_at`/`premarket`).
- `seal-focus --root R --levels L --iv-history IV --focus F [--routine ID] [--correction]`: seal one night's picker
  evidence (refuses a focus file the pre-open pass has already edited).
- `seal-radar --root R --edition OUTBOX/<date>.json --skill BUNDLE (--fetch-directory | --directory DIR) [--session D]
  [--http-status N] [--sent-at T] [--routine ID] [--correction]`: seal one radar edition as sent, with the day's
  Nasdaq Trader symbol directory and the Radar skill bundle's sha256; the session defaults to the file's date.
- `seal-receipts --root R --date D [--base URL] [--routine ID]`: after 09:30 New York on D, seal the dashboard's
  listing of the radar edition copies for D as data/radar-receipts/D.
- `exp0038-status --root R --start S [--until D]`: EXP-0038's counted sessions and the reasons for the others; counts
  only, no return and no statistic, so it may run before the read.
- `exp0038-read --root R --start S --frozen-commit C --tlr PATH`: the read, once, at the read date, written to
  `R/exp0038-read/report.json` (fetched pages kept under `R/exp0038-read/inputs/`), from
  this checkout at the frozen commit; needs the Alpaca data keys (APCA_API_KEY_ID, APCA_API_SECRET_KEY) and the
  research key (RADAR_INGEST_SECRET) in the environment.
- `verify FOLDER...`: check sealed folders against their manifests.
- `replay-focus FOLDER...`: run the port on sealed inputs and compare with the sealed focus file.
- `funnel-health --root R --session S [--out F] [--send] [--base URL]`: build the dashboard's health document for a
  session from the sealed evidence; print it, write it, or send it to the signed ingest.

All checks run before anything is written. Each file is written to a temporary file, flushed to disk, then linked
into place, which fails if the target exists: a run never overwrites, and never leaves a partial file.

Exit codes: 0 done; 1 an output already exists; 3 bad input (missing file, bad CSV header, a name the record
cannot describe); 4 an output could not be written (missing folder, no permission, disk); 5 a sealed folder does
not verify or does not replay; 6 the session is already sealed with different bytes (a change needs --correction);
7 the health document could not be sent or was refused.
Sealing the same bytes again exits 0 and says "already sealed, identical". On any failure after writing has
started, every output this run created is removed.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from options_funnel import calendar
from options_funnel.blind import BlindRuleError, assert_blind
from options_funnel.candidates import dump_jsonl
from options_funnel.focus_pick import pick
from options_funnel.health import build as build_health
from options_funnel.publish import DEFAULT_BASE, PublishError, send
from options_funnel.replay import replay_focus
from options_funnel.seal import AlreadySealedDifferent, SealError, check_session, iv_rows_for, seal, sha256, verify
from options_funnel.witness import (
    DIRECTORY_FILES,
    WitnessError,
    after_open,
    check_directory_file,
    fetch_directory,
    fetch_listing,
)

IV_COLUMNS = frozenset({"session", "ticker", "call_vol", "put_vol", "avg_call_vol", "avg_put_vol"})
EXIT_EXISTS = 1
EXIT_INPUT = 3
EXIT_WRITE = 4
EXIT_MISMATCH = 5
EXIT_SEALED_DIFFERENT = 6
EXIT_SEND = 7


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


def code_commit() -> str | None:
    """The commit of this repository's code that is sealing (None when not run from a git checkout)."""
    here = str(Path(__file__).resolve().parent)
    try:
        head = subprocess.run(["git", "-C", here, "rev-parse", "HEAD"], check=True, capture_output=True, text=True)
        dirty = subprocess.run(
            ["git", "-C", here, "status", "--porcelain", "--untracked-files=no"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return head.stdout.strip() + ("+dirty" if dirty.stdout.strip() else "")


def _base_provenance(args: argparse.Namespace, sources: dict[str, Path]) -> dict[str, object]:
    return {
        "code_commit": code_commit(),
        "routine": args.routine,
        "sources": {name: str(path) for name, path in sorted(sources.items())},
    }


def seal_focus(args: argparse.Namespace) -> int:
    try:
        levels_bytes = args.levels.read_bytes()
        iv_bytes = args.iv_history.read_bytes()
        focus_bytes = args.focus.read_bytes()
        levels = json.loads(levels_bytes.decode("utf-8-sig"))
        focus = json.loads(focus_bytes.decode("utf-8-sig"))
        session, built = levels["session"], levels["built_from"]
        if focus.get("session") != session or focus.get("built_from") != built:
            raise InputError(
                f"focus file is for {focus.get('session')!r} built from {focus.get('built_from')!r}; "
                f"levels file for {session!r} built from {built!r}"
            )
        if focus.get("premarket") is not None:
            raise InputError("the focus file already carries the pre-open pass; seal it before the pre-open run")
        files = {"levels.json": levels_bytes, "iv_rows.csv": iv_rows_for(iv_bytes, built), "focus.json": focus_bytes}
    except (OSError, KeyError, TypeError, SealError, json.JSONDecodeError) as error:
        raise InputError(f"{type(error).__name__}: {error}") from error
    provenance = _base_provenance(args, {"levels": args.levels, "iv_history": args.iv_history, "focus": args.focus})
    provenance |= {"iv_history_sha256": sha256(iv_bytes), "iv_history_size": len(iv_bytes), "built_from": built}
    return _seal(args, "focus", session, files, provenance)


DATE_NAME = re.compile(r"\d{4}-\d{2}-\d{2}")


def seal_radar(args: argparse.Namespace) -> int:
    try:
        edition = args.edition.read_bytes()
        json.loads(edition.decode("utf-8-sig"))
        session = args.session or args.edition.stem
        check_session(session)
        if args.session and DATE_NAME.fullmatch(args.edition.stem) and args.edition.stem != args.session:
            raise InputError(f"--session {args.session} does not match the file's date {args.edition.stem}")
        skill = args.skill.read_bytes()
        existing = _sealed_edition_sha(args.root, session)
        if existing == sha256(edition) and not args.correction:
            # The same edition again: nothing to seal, and no second directory fetch to disagree with the first.
            print(f"already sealed, identical: {args.root / 'data' / 'radar' / session}")
            return 0
        directory_note: str
        if args.directory:
            directory = {name: (args.directory / name).read_bytes() for name in DIRECTORY_FILES}
            for name, data in directory.items():
                check_directory_file(name, data)
            directory_note = str(args.directory)
        else:
            try:
                directory = fetch_directory()
                directory_note = "nasdaqtrader.com/dynamic/SymDir"
            except WitnessError as error:
                # The edition is still sealed; DEC-0055 then does not count the session (no_directory).
                print(f"warning: symbol directory not sealed: {error}", file=sys.stderr)
                directory = {}
                directory_note = f"unavailable: {error}"
    except (OSError, SealError, WitnessError, json.JSONDecodeError) as error:
        raise InputError(f"{type(error).__name__}: {error}") from error
    provenance = _base_provenance(args, {"edition": args.edition, "skill": args.skill})
    provenance |= {
        "http_status": args.http_status,
        "sent_at": args.sent_at,
        "skill_sha256": sha256(skill),
        "skill_size": len(skill),
        "directory_source": directory_note,
        "directory_fetched_at": _now() if directory else None,
    }
    return _seal(args, "radar", session, {"edition.json": edition, **directory}, provenance)


def _sealed_edition_sha(root: Path, session: str) -> str | None:
    """The edition.json sha256 of the latest sealed radar folder for `session`, or None when there is none."""
    base = root / "data" / "radar"
    folders = sorted(
        (p for p in base.glob(f"{session}*") if p.is_dir()),
        key=lambda p: int(p.name.split(".r")[1]) if ".r" in p.name else 1,
    )
    if not folders:
        return None
    manifest = json.loads((folders[-1] / "MANIFEST.json").read_text(encoding="utf-8"))
    value = manifest.get("files", {}).get("edition.json", {}).get("sha256")
    return value if isinstance(value, str) else None


def seal_receipts(args: argparse.Namespace) -> int:
    try:
        check_session(args.date)
        if not calendar.load().is_session(args.date):
            raise InputError(f"{args.date} is not a regular New York session")
    except (SealError, calendar.CalendarError) as error:
        raise InputError(str(error)) from error
    if not after_open(args.date, datetime.now(UTC)):
        raise InputError(f"too early: the listing for {args.date} is sealed only after 09:30 New York on that day")
    try:
        listing = fetch_listing(args.base, args.date, time.time())
    except WitnessError as error:
        print(f"not sealed: {error}", file=sys.stderr)
        return EXIT_SEND
    sealed_sha = _sealed_edition_sha(args.root, args.date)
    rows = json.loads(listing)["copies"]
    provenance = _base_provenance(args, {}) | {
        "base": args.base,
        "radar_edition_sha256": sealed_sha,
        "rows_matching_sealed_edition": [r["run_id"] for r in rows if sealed_sha and r["edition_sha256"] == sealed_sha],
    }
    # Sealed once: a listing that differs from the first is reported (exit 6), never sealed as a correction.
    args.correction = False
    return _seal(args, "radar-receipts", args.date, {"listing.json": listing}, provenance)


def _seal(args: argparse.Namespace, kind: str, session: str, files: dict[str, bytes], prov: dict[str, object]) -> int:
    try:
        sealed = seal(args.root, kind, session, files, _now(), prov, args.correction)
    except AlreadySealedDifferent as error:
        print(str(error), file=sys.stderr)
        return EXIT_SEALED_DIFFERENT
    except SealError as error:
        raise InputError(str(error)) from error
    except OSError as error:
        print(f"could not write: {error}", file=sys.stderr)
        return EXIT_WRITE
    print(f"{'sealed' if sealed.created else 'already sealed, identical:'} {sealed.folder}")
    return 0


def exp0038_status(args: argparse.Namespace) -> int:
    from options_funnel.exp0038_read import status_report

    until = args.until or datetime.now(UTC).astimezone(ZoneInfo("America/New_York")).date().isoformat()
    try:
        report = status_report(args.root, args.start, until, datetime.now(UTC))
    except (calendar.CalendarError, ValueError, OSError) as error:
        raise InputError(f"{type(error).__name__}: {error}") from error
    print(json.dumps(report, indent=1, sort_keys=True))
    return 0


def exp0038_read(args: argparse.Namespace) -> int:
    from options_funnel.exp0038_inputs import _git, _http
    from options_funnel.exp0038_read import ReadInputs, ReadRefused, check_frozen, now_session, report_path, run_read

    if report_path(args.root).exists():
        print(f"refusing: {report_path(args.root)} exists; the read is written once", file=sys.stderr)
        return EXIT_EXISTS
    key, secret = os.environ.get("APCA_API_KEY_ID"), os.environ.get("APCA_API_SECRET_KEY")
    if not key or not secret:
        raise InputError("APCA_API_KEY_ID and APCA_API_SECRET_KEY must be set")
    here = Path(__file__).resolve().parents[2]
    try:
        check_frozen(here, args.frozen_commit, _git(here))

        def store_listing(day: str) -> list[dict[str, object]]:
            rows: list[dict[str, object]] = json.loads(fetch_listing(args.base, day, time.time()))["copies"]
            return rows

        inputs = ReadInputs(
            store_listing=store_listing,
            # The store's copies are read through the sealed radar editions (DEC-0055 section 1's fallback): the
            # research key has no read of copy content.
            read_store_copy=lambda _path: None,
            git=_git(args.tlr),
            fetch=_http,
            alpaca_headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
        )
        provenance = {"frozen_commit": args.frozen_commit, "read_at": _now()}
        run_read(args.root, args.start, now_session(datetime.now(UTC)), inputs, provenance)
    except ReadRefused as error:
        print(f"refusing: {error}", file=sys.stderr)
        return EXIT_INPUT
    except (WitnessError, OSError, ValueError, KeyError) as error:
        print(f"read failed before any statistic was printed: {type(error).__name__}: {error}", file=sys.stderr)
        return EXIT_INPUT
    print(f"read written to {report_path(args.root)}")
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


def health_cmd(args: argparse.Namespace) -> int:
    try:
        doc = build_health(args.root, args.session, _now(), code_commit())
    except (OSError, KeyError, TypeError, SealError, json.JSONDecodeError) as error:
        raise InputError(f"{type(error).__name__}: {error}") from error
    text = json.dumps(doc, indent=1, ensure_ascii=False) + "\n"
    if args.out:
        try:
            write_all_new([(args.out, text)])
        except FileExistsError:
            print(f"refusing to overwrite {args.out}", file=sys.stderr)
            return EXIT_EXISTS
        except OSError as error:
            print(f"could not write: {error}", file=sys.stderr)
            return EXIT_WRITE
    if args.send:
        try:
            sent = send(doc, args.base)
        except PublishError as error:
            print(f"not sent: {error}", file=sys.stderr)
            return EXIT_SEND
        print(f"sent {doc['run_id']}: HTTP {sent.status} {sent.outcome}")
    elif not args.out:
        print(text, end="")
    return 0


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
    sf.add_argument("--routine")
    sf.add_argument("--correction", action="store_true")
    sf.set_defaults(run=seal_focus)
    sr = sub.add_parser("seal-radar", help="seal one radar edition as sent")
    sr.add_argument("--root", type=Path, required=True)
    sr.add_argument("--session")
    sr.add_argument("--edition", type=Path, required=True)
    sr.add_argument("--skill", type=Path, required=True, help="the Radar skill bundle the run used")
    source = sr.add_mutually_exclusive_group(required=True)
    source.add_argument("--fetch-directory", action="store_true", help="download today's Nasdaq Trader directory")
    source.add_argument("--directory", type=Path, help="a folder holding nasdaqlisted.txt and otherlisted.txt")
    sr.add_argument("--http-status", type=int)
    sr.add_argument("--sent-at")
    sr.add_argument("--routine")
    sr.add_argument("--correction", action="store_true")
    sr.set_defaults(run=seal_radar)
    rr = sub.add_parser("seal-receipts", help="seal the dashboard's radar copies listing for a date, after the open")
    rr.add_argument("--root", type=Path, required=True)
    rr.add_argument("--date", required=True)
    rr.add_argument("--base", default=DEFAULT_BASE)
    rr.add_argument("--routine")
    rr.set_defaults(run=seal_receipts)
    es = sub.add_parser("exp0038-status", help="EXP-0038 counted sessions and reasons (counts only)")
    es.add_argument("--root", type=Path, required=True)
    es.add_argument("--start", required=True)
    es.add_argument("--until")
    es.set_defaults(run=exp0038_status)
    er = sub.add_parser("exp0038-read", help="EXP-0038's read, once, at the read date")
    er.add_argument("--root", type=Path, required=True)
    er.add_argument("--start", required=True)
    er.add_argument("--frozen-commit", required=True)
    er.add_argument("--tlr", type=Path, required=True, help="a trading-lab-research checkout with origin/main fetched")
    er.add_argument("--base", default=DEFAULT_BASE)
    er.set_defaults(run=exp0038_read)
    vf = sub.add_parser("verify", help="check sealed folders against their manifests")
    vf.add_argument("folders", type=Path, nargs="+")
    vf.set_defaults(run=verify_cmd)
    rp = sub.add_parser("replay-focus", help="re-run the port on sealed nights and compare")
    rp.add_argument("folders", type=Path, nargs="+")
    rp.set_defaults(run=replay_cmd)
    fh = sub.add_parser("funnel-health", help="build (and send) the dashboard's health document for a session")
    fh.add_argument("--root", type=Path, required=True)
    fh.add_argument("--session", required=True)
    fh.add_argument("--out", type=Path)
    fh.add_argument("--send", action="store_true")
    fh.add_argument("--base", default=DEFAULT_BASE)
    fh.set_defaults(run=health_cmd)
    args = parser.parse_args(argv)
    try:
        result: int = args.run(args)
        return result
    except (InputError, ValueError) as error:
        print(f"bad input: {error}", file=sys.stderr)
        return EXIT_INPUT


if __name__ == "__main__":
    raise SystemExit(main())
