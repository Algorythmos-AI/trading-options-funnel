"""The funnel's health document (`stocksdelta/funnel` v1) for one session, built from the sealed evidence only.

It says, per stage, whether the evidence is sealed and verifies, when, how many corrections followed, how many names
the stage judged and kept, how many names carried each record flag, and whether the ported picker reproduces the
sealed picks. Counts, codes, statuses, dates and hashes only: no symbols, prices, scores or outcomes.

Which sealed folders a session's document reads:
- `focus_pick`: `data/focus/<session>` (the evening pick for that session), its latest correction if any.
- `radar`: `data/radar/<built_from>`, the radar edition of the morning the pick was built on (the radar for the
  session itself only exists the next morning, after this document is sent).

The contract belongs to the dashboard (trading-lab-research `dashboard/src/lib/funnel.schema.json`); this repository
keeps a copy in `contracts/` pinned by sha256 in `contracts/funnel.contract.lock.json`.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections import Counter
from pathlib import Path
from typing import Any

from options_funnel.blind import assert_blind
from options_funnel.focus_pick import pick
from options_funnel.replay import replay_focus
from options_funnel.seal import MANIFEST, check_session, verify

SCHEMA = "stocksdelta/funnel"
SCHEMA_VERSION = 1


def _latest(base: Path, session: str) -> tuple[Path | None, int]:
    """The latest sealed folder for `session` under `base`, and how many corrections follow the first seal."""
    first = base / session
    if not first.is_dir():
        return None, 0
    corrections = sorted(
        (p for p in base.glob(f"{session}.r*") if p.is_dir() and p.name[len(session) + 2 :].isdigit()),
        key=lambda p: int(p.name[len(session) + 2 :]),
    )
    return (corrections[-1] if corrections else first), len(corrections)


def _sealed_at(folder: Path) -> str | None:
    try:
        value = json.loads((folder / MANIFEST).read_text(encoding="utf-8")).get("sealed_at")
    except (OSError, ValueError):
        return None
    return value if isinstance(value, str) else None


def _missing(stage: str) -> dict[str, Any]:
    return {
        "stage": stage,
        "evidence": "missing",
        "sealed_at": None,
        "corrections": 0,
        "names_judged": None,
        "names_kept": None,
        "flags": [],
        "replay": "not_run",
    }


def focus_stage(root: Path, session: str) -> tuple[dict[str, Any], str | None]:
    """The focus_pick stage, and the `built_from` session it was picked on (None when not readable)."""
    folder, corrections = _latest(root / "data" / "focus", session)
    if folder is None:
        return _missing("focus_pick"), None
    stage = _missing("focus_pick") | {"corrections": corrections, "sealed_at": _sealed_at(folder)}
    if verify(folder):
        return stage | {"evidence": "broken"}, None
    levels = json.loads((folder / "levels.json").read_text(encoding="utf-8-sig"))
    rows = list(csv.DictReader(io.StringIO((folder / "iv_rows.csv").read_text(encoding="utf-8-sig"))))
    records = pick(levels, rows).candidates
    flags = Counter(flag for record in records for flag in record.flags)
    stage |= {
        "evidence": "sealed",
        "names_judged": len(records),
        "names_kept": sum(1 for r in records if r.rank is not None),
        "flags": [{"code": code, "names": n} for code, n in sorted(flags.items())],
        "replay": "mismatch" if replay_focus(folder) else "match",
    }
    built = levels.get("built_from")
    return stage, built if isinstance(built, str) else None


def radar_stage(root: Path, session: str | None) -> dict[str, Any]:
    """The radar stage for the morning edition of `session` (None: not known, so missing)."""
    if session is None:
        return _missing("radar")
    folder, corrections = _latest(root / "data" / "radar", session)
    if folder is None:
        return _missing("radar")
    stage = _missing("radar") | {"corrections": corrections, "sealed_at": _sealed_at(folder)}
    if verify(folder):
        return stage | {"evidence": "broken"}
    edition = json.loads((folder / "edition.json").read_text(encoding="utf-8-sig"))
    lists = edition.get("lists") if isinstance(edition, dict) else None
    names = {
        s
        for entry in (lists or [])
        if isinstance(entry, dict)
        for s in entry.get("symbols") or []
        if isinstance(s, str)
    }
    return stage | {"evidence": "sealed", "names_kept": len(names) if lists is not None else None}


def build(root: Path, session: str, as_of: str, code_commit: str | None) -> dict[str, Any]:
    """The document for `session`. `run_id` is a hash of its content, so the same evidence gives the same run."""
    check_session(session)
    focus, built = focus_stage(root, session)
    body: dict[str, Any] = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "session": session,
        "code_commit": code_commit,
        "stages": [radar_stage(root, built), focus],
    }
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:12]
    doc = {**body, "run_id": f"funnel-{session}-{digest}", "as_of": as_of}
    assert_blind(doc)
    return doc
