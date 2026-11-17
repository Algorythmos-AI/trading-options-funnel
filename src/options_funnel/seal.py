"""Sealed evidence: what a routine used or sent, kept byte for byte with its sha256, never edited afterwards.

Layout on the `data` branch (an orphan branch that holds nothing but `data/`):

    data/<kind>/<session>/MANIFEST.json      kind, session, sealed_at, sha256 and size of every file, provenance
    data/<kind>/<session>/<file>             the files, exactly as received (or derived, as the manifest says)

Kinds:
- `focus`: the after-close picker's inputs and output: `levels.json` (as built), `iv_rows.csv` (the header and the
  rows of iv_history.csv for the levels file's `built_from` session, line bytes unchanged, plus the sha256 of the
  whole source file), `focus.json` (the focus file the routine wrote).
- `radar`: `edition.json`, the radar edition exactly as sent to the dashboard.

A seal is written to a temporary folder and renamed into place, so a session folder is either complete or absent.
An existing session folder is never touched: a correction is a new folder `<session>.r2` (then `.r3`, ...) whose
manifest names the one it replaces.

Sealed files are evidence stored verbatim; the blind-rule guard applies to what this repository computes, not to
what a routine published.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

MANIFEST = "MANIFEST.json"
SCHEMA = "options-funnel/seal"
SCHEMA_VERSION = 1
KINDS: dict[str, frozenset[str]] = {
    "focus": frozenset({"levels.json", "iv_rows.csv", "focus.json"}),
    "radar": frozenset({"edition.json"}),
}
_FOLDER = re.compile(r"(\d{4}-\d{2}-\d{2})(?:\.r([2-9]|[1-9]\d))?")


class SealError(ValueError):
    """A seal cannot be written or does not verify."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _session(value: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) or date.fromisoformat(value).isoformat() != value:
        raise SealError(f"bad session {value!r}")
    return value


def iv_rows_for(source: bytes, built_from: str) -> bytes:
    """The header line and every line of iv_history.csv whose first field is `built_from`, bytes unchanged."""
    text = source.decode("utf-8-sig")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].split(",")[0].strip() != "session":
        raise SealError("iv_history.csv must start with a header whose first column is session")
    kept = [line for line in lines[1:] if line.split(",", 1)[0] == built_from]
    if not kept:
        raise SealError(f"iv_history.csv has no rows for {built_from}")
    return "".join([lines[0], *kept]).encode("utf-8")


@dataclass(frozen=True, slots=True)
class Sealed:
    folder: Path
    manifest: dict[str, Any]


def seal(
    root: Path,
    kind: str,
    session: str,
    files: dict[str, bytes],
    sealed_at: str,
    provenance: dict[str, Any],
    replaces: str | None = None,
) -> Sealed:
    """Write one sealed session folder under `root/data/<kind>/` and return it. Refuses an existing folder."""
    if kind not in KINDS:
        raise SealError(f"unknown kind {kind!r}")
    if set(files) != KINDS[kind]:
        raise SealError(f"{kind} needs exactly {sorted(KINDS[kind])}, got {sorted(files)}")
    _session(session)
    base = root / "data" / kind
    name = session
    if replaces is not None:
        match = _FOLDER.fullmatch(replaces)
        if not match or match.group(1) != session or not (base / replaces).is_dir():
            raise SealError(f"replaces must name an existing folder of session {session}")
        n = 2
        while (base / f"{session}.r{n}").exists():
            n += 1
        name = f"{session}.r{n}"
    target = base / name
    if target.exists():
        raise SealError(f"{target} is already sealed; a correction needs --replaces")
    manifest: dict[str, Any] = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "session": session,
        "sealed_at": sealed_at,
        "replaces": replaces,
        "provenance": provenance,
        "files": {f: {"sha256": sha256(b), "size": len(b)} for f, b in sorted(files.items())},
    }
    base.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(dir=base, prefix=f".{name}."))
    try:
        for file_name, data in files.items():
            (tmp / file_name).write_bytes(data)
        (tmp / MANIFEST).write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        for path in tmp.iterdir():
            with open(path, "rb") as fh:
                os.fsync(fh.fileno())
        os.rename(tmp, target)  # fails if target appeared meanwhile (non-empty) on POSIX
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    problems = verify(target)
    if problems:
        raise SealError("; ".join(problems))
    return Sealed(target, manifest)


def verify(folder: Path) -> list[str]:
    """Problems with a sealed folder: missing or extra files, wrong sizes or hashes, a bad manifest."""
    try:
        manifest = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
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
    listed = manifest.get("files") or {}
    present = {p.name for p in folder.iterdir() if p.name != MANIFEST}
    if kind in KINDS and set(listed) != KINDS[kind]:
        problems.append(f"{folder}: manifest lists {sorted(listed)}, {kind} needs {sorted(KINDS[kind])}")
    for name in sorted(present - set(listed)):
        problems.append(f"{folder}: {name} is not in the manifest")
    for name, meta in sorted(listed.items()):
        path = folder / name
        if not path.is_file():
            problems.append(f"{folder}: {name} is missing")
            continue
        data = path.read_bytes()
        if len(data) != meta.get("size") or sha256(data) != meta.get("sha256"):
            problems.append(f"{folder}: {name} does not match its manifest")
    return problems
