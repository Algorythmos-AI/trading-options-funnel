"""Replay a sealed night: run the ported picker on the sealed inputs and compare with the sealed output."""

from __future__ import annotations

import csv
import io
import json
import math
from pathlib import Path

from options_funnel.focus_pick import pick
from options_funnel.seal import verify


def replay_focus(folder: Path) -> list[str]:
    """Differences between the sealed focus file and the port's picks from the sealed inputs (empty: identical)."""
    problems = verify(folder)
    if problems:
        return problems
    levels = json.loads((folder / "levels.json").read_text(encoding="utf-8-sig"))
    rows = list(csv.DictReader(io.StringIO((folder / "iv_rows.csv").read_text(encoding="utf-8-sig"))))
    sealed = json.loads((folder / "focus.json").read_text(encoding="utf-8-sig"))
    document = pick(levels, rows).document
    out: list[str] = []
    for key in ("session", "built_from", "method"):
        if document[key] != sealed.get(key):
            out.append(f"{key}: port {document[key]!r}, sealed {sealed.get(key)!r}")
    ported = document["items"]
    published = sealed.get("items") or []
    if [i["symbol"] for i in ported] != [i.get("symbol") for i in published]:
        out.append(
            "names: port "
            + ", ".join(i["symbol"] for i in ported)
            + "; sealed "
            + ", ".join(str(i.get("symbol")) for i in published)
        )
    else:
        for mine, theirs in zip(ported, published, strict=True):
            for key in sorted(set(mine) | set(theirs)):
                if not _same(mine.get(key), theirs.get(key)):
                    out.append(f"{mine['symbol']}.{key}: port {mine.get(key)!r}, sealed {theirs.get(key)!r}")
    return out


def _same(a: object, b: object) -> bool:
    """Equal, with floats compared to 1e-9 (both sides come from the same rounding)."""
    if isinstance(a, float) or isinstance(b, float):
        return isinstance(a, int | float) and isinstance(b, int | float) and math.isclose(a, b, abs_tol=1e-9)
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    return a == b
