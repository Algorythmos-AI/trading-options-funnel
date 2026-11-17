"""Sealed evidence: what a routine used or sent, kept byte for byte with its sha256, never edited afterwards.

Layout on the `data` branch (an orphan branch that holds nothing but `data/`):

    data/<kind>/<session>/MANIFEST.json      kind, session, sealed_at, sha256 and size of every file, provenance
    data/<kind>/<session>/<file>             the files, exactly as received (or derived, as the manifest says)

Kinds:
- `focus`: the after-close picker's inputs and output: `levels.json` (as built), `iv_rows.csv` (the header and the
  rows of iv_history.csv for the levels file's `built_from` session, line bytes unchanged, BOM kept), `focus.json`
  (the focus file as `focus.py pick` wrote it, before the pre-open pass adds `premarket`).
- `radar`: `edition.json`, the radar edition exactly as sent to the dashboard, with that morning's Nasdaq Trader
  symbol directory (`nasdaqlisted.txt`, `otherlisted.txt`) and, in the provenance, the Radar skill bundle's sha256.
- `radar-receipts`: `listing.json`, the dashboard's listing of the radar edition copies for the date (store path,
  receipt time, run, sha256s), as served, sealed after the open (trading-lab-research DEC-0055 section 1).

Writing: the session folder is created exclusively (`mkdir` fails if it exists), the files go in, the manifest
last, everything is flushed to disk, then the folder is verified; on any failure the new folder is removed.

Re-sealing the same bytes is a no-op ("already sealed, identical"). Different bytes for a sealed session are
refused unless asked for as a correction, which goes into the next free `<session>.rN` folder (N = 2..99); its
manifest names the folder it replaces, which is always the latest one, so corrections form a single chain.

Sealed files are evidence stored verbatim; the blind-rule guard applies to what this repository computes, not to
what a routine published.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

MANIFEST = "MANIFEST.json"
SCHEMA = "options-funnel/seal"
SCHEMA_VERSION = 1
MAX_CORRECTION = 99
KINDS: dict[str, frozenset[str]] = {
    "focus": frozenset({"levels.json", "iv_rows.csv", "focus.json"}),
    "radar": frozenset({"edition.json"}),
    "radar-receipts": frozenset({"listing.json"}),
}
# Files a kind may also hold, all of a group or none: a radar folder sealed while Nasdaq Trader was unreachable still
# seals its edition (its session is then not counted, DEC-0055 section 1 `no_directory`).
OPTIONAL: dict[str, frozenset[str]] = {"radar": frozenset({"nasdaqlisted.txt", "otherlisted.txt"})}


def allowed_file_sets(kind: str) -> list[frozenset[str]]:
    base = KINDS[kind]
    extra = OPTIONAL.get(kind)
    return [base] if extra is None else [base, base | extra]


_FOLDER = re.compile(r"(\d{4}-\d{2}-\d{2})(?:\.r([2-9]|[1-9]\d))?")
_BOM = b"\xef\xbb\xbf"


class SealError(ValueError):
    """A seal cannot be written or does not verify."""


class AlreadySealedDifferent(SealError):
    """The session is sealed with different bytes; only a correction may add them."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_session(value: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) or date.fromisoformat(value).isoformat() != value:
        raise SealError(f"bad session {value!r}")
    return value


def _first_field(line: bytes) -> bytes:
    field = line.split(b",", 1)[0].strip()
    if len(field) >= 2 and field[:1] == field[-1:] == b'"':
        field = field[1:-1]
    return field


def iv_rows_for(source: bytes, built_from: str) -> bytes:
    """The header line and every line of iv_history.csv whose session is `built_from`, bytes unchanged.

    Lines are split on b"\\n" only, so CRLF endings and any BOM are kept as they are. A quoted session field counts.
    Two rows for the same (session, ticker) are refused: the picker would silently use the last one.
    """
    bom = source.startswith(_BOM)
    lines = source.split(b"\n")
    keep_last = source.endswith(b"\n")
    lines = [line + b"\n" for line in lines[:-1]] + ([] if keep_last else [lines[-1]])
    if not lines or _first_field(lines[0][len(_BOM) if bom else 0 :]) != b"session":
        raise SealError("iv_history.csv must start with a header whose first column is session")
    wanted = built_from.encode()
    kept = [line for line in lines[1:] if _first_field(line) == wanted]
    if not kept:
        raise SealError(f"iv_history.csv has no rows for {built_from}")
    tickers = [line.split(b",")[1].strip().strip(b'"') if line.count(b",") else b"" for line in kept]
    duplicated = sorted({t.decode(errors="replace") for t in tickers if tickers.count(t) > 1})
    if duplicated:
        raise SealError(f"iv_history.csv has two rows for {built_from} and {', '.join(duplicated)}")
    return b"".join([lines[0], *kept])


@dataclass(frozen=True, slots=True)
class Sealed:
    folder: Path
    manifest: dict[str, Any]
    created: bool


def _folders(base: Path, session: str) -> list[Path]:
    found = []
    for path in base.glob(f"{session}*"):
        match = _FOLDER.fullmatch(path.name)
        if match and match.group(1) == session and path.is_dir():
            found.append((int(match.group(2) or 1), path))
    return [p for _, p in sorted(found)]


def _hashes(files: dict[str, bytes]) -> dict[str, dict[str, Any]]:
    return {name: {"sha256": sha256(data), "size": len(data)} for name, data in sorted(files.items())}


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def seal(
    root: Path,
    kind: str,
    session: str,
    files: dict[str, bytes],
    sealed_at: str,
    provenance: dict[str, Any],
    correction: bool = False,
) -> Sealed:
    """Seal one session folder under `root/data/<kind>/`.

    Returns the existing folder (created=False) when the latest seal of the session holds exactly these bytes.
    Raises AlreadySealedDifferent when it holds other bytes and `correction` is not set.
    """
    if kind not in KINDS:
        raise SealError(f"unknown kind {kind!r}")
    if frozenset(files) not in allowed_file_sets(kind):
        raise SealError(
            f"{kind} needs exactly {sorted(KINDS[kind])} "
            f"(or with {sorted(OPTIONAL.get(kind, set()))}), got {sorted(files)}"
        )
    check_session(session)
    base = root / "data" / kind
    existing = _folders(base, session) if base.is_dir() else []
    hashes = _hashes(files)
    replaces = None
    if existing:
        latest = existing[-1]
        current = json.loads((latest / MANIFEST).read_text(encoding="utf-8"))
        if current.get("files") == hashes:
            return Sealed(latest, current, created=False)
        if not correction:
            raise AlreadySealedDifferent(f"{latest} is sealed with other bytes; a change needs --correction")
        if len(existing) >= MAX_CORRECTION:
            raise SealError(f"{session} already has {MAX_CORRECTION - 1} corrections")
        replaces = latest.name
        name = f"{session}.r{len(existing) + 1}"
    elif correction:
        raise SealError(f"{session} is not sealed yet, so there is nothing to correct")
    else:
        name = session
    manifest: dict[str, Any] = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "session": session,
        "sealed_at": sealed_at,
        "replaces": replaces,
        "provenance": provenance,
        "files": hashes,
    }
    base.mkdir(parents=True, exist_ok=True)
    target = base / name
    target.mkdir()  # exclusive: fails if another run created it first
    try:
        for file_name, data in sorted(files.items()):
            with open(target / file_name, "xb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
        with open(target / MANIFEST, "x", encoding="utf-8") as fh:
            fh.write(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        _fsync_dir(target)
        _fsync_dir(base)
        problems = verify(target)
        if problems:
            raise SealError("; ".join(problems))
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return Sealed(target, manifest, created=True)


def verify(folder: Path) -> list[str]:
    """Problems with a sealed folder: missing, extra or non-regular files, wrong sizes or hashes, a bad manifest."""
    if folder.is_symlink() or not folder.is_dir():
        return [f"{folder}: not a folder"]
    manifest_path = folder / MANIFEST
    if manifest_path.is_symlink():
        return [f"{folder}: the manifest is a symlink"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return [f"{folder}: unreadable manifest ({error})"]
    problems: list[str] = []
    if manifest.get("schema") != SCHEMA or manifest.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"{folder}: not an {SCHEMA} v{SCHEMA_VERSION} manifest")
    kind = manifest.get("kind")
    match = _FOLDER.fullmatch(folder.name)
    if kind not in KINDS or folder.parent.name != kind:
        problems.append(f"{folder}: kind {kind!r} does not match its folder")
    if not match or match.group(1) != manifest.get("session"):
        problems.append(f"{folder}: folder name does not match session {manifest.get('session')!r}")
    elif (match.group(2) is None) != (manifest.get("replaces") is None):
        problems.append(f"{folder}: a correction folder must name what it replaces, and only a correction may")
    listed = manifest.get("files") or {}
    present = {p.name for p in folder.iterdir() if p.name != MANIFEST}
    if kind in KINDS and frozenset(listed) not in allowed_file_sets(kind):
        problems.append(f"{folder}: manifest lists {sorted(listed)}, {kind} needs {sorted(KINDS[kind])}")
    for name in sorted(present - set(listed)):
        problems.append(f"{folder}: {name} is not in the manifest")
    for name, meta in sorted(listed.items()):
        path = folder / name
        if path.is_symlink() or not path.is_file():
            problems.append(f"{folder}: {name} is missing or not a regular file")
            continue
        data = path.read_bytes()
        if len(data) != meta.get("size") or sha256(data) != meta.get("sha256"):
            problems.append(f"{folder}: {name} does not match its manifest")
    return problems
