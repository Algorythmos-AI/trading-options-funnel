"""EXP-0038's inputs at the read (trading-lab-research DEC-0055 sections 1 to 3), from sealed files or fetched once.

- `load_receipts`: a sealed `data/radar-receipts/<S>` listing as receipts (the first folder only: sealed once).
- `listing_matches`: the read-time check that the store's copies are exactly the sealed listing's.
- `scored_content`: the scored edition's content from the store copy or, failing that, a sealed radar edition with the
  listing's sha256; otherwise `content_lost`.
- `directory_for`: the session's sealed Nasdaq Trader directory (the first radar folder of S that holds one).
- `desk_names_at`: `config/options_desk.yaml` on trading-lab-research's `origin/main` as it stood at 09:30 New York.
- `spin_offs`, `daily_bars`: Alpaca corporate actions and SIP daily bars (adjustment=all), every page, raw bytes kept.

Fetching needs the owner's Alpaca data keys and runs only at the read; nothing here is run before it.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Any

from options_funnel.exp0038 import NEW_YORK, Directory, Receipt, normalise_symbol
from options_funnel.seal import MANIFEST, verify


class InputError(ValueError):
    """An input of the read is missing, unreadable or not what the record says."""


def _first_folder(base: Path, session: str) -> Path | None:
    """The first sealed folder for `session` (the session itself, never a correction)."""
    folder = base / session
    return folder if folder.is_dir() else None


def load_receipts(root: Path, session: str) -> tuple[list[Receipt], list[dict[str, Any]]] | None:
    """The sealed receipt listing for `session` as receipts and raw rows; None when no listing was sealed."""
    folder = _first_folder(root / "data" / "radar-receipts", session)
    if folder is None:
        return None
    problems = verify(folder)
    if problems:
        raise InputError("; ".join(problems))
    doc = json.loads((folder / "listing.json").read_bytes())
    rows: list[dict[str, Any]] = doc["copies"]
    receipts = [
        Receipt(
            path=r["path"],
            received_at=datetime.fromisoformat(r["received_at"].replace("Z", "+00:00")),
            run_id=r["run_id"],
            copy_sha256=r["copy_sha256"],
            edition_sha256=r["edition_sha256"],
            lost=r["lost"],
        )
        for r in rows
    ]
    return receipts, rows


_ROW_KEYS = ("path", "received_at", "run_id", "lost", "copy_sha256", "edition_sha256")


def listing_matches(sealed_rows: Sequence[Mapping[str, Any]], store_rows: Sequence[Mapping[str, Any]]) -> bool:
    """True when the store lists exactly the sealed rows (paths, receipt times, runs, lost flags and hashes)."""

    def key(rows: Sequence[Mapping[str, Any]]) -> list[tuple[Any, ...]]:
        return sorted(tuple(r[k] for k in _ROW_KEYS) for r in rows)

    return key(sealed_rows) == key(store_rows)


EDITION_MARK = ',"edition":'


def _edition_from_copy(copy_text: bytes, receipt: Receipt) -> bytes | None:
    if receipt.copy_sha256 is None or hashlib.sha256(copy_text).hexdigest() != receipt.copy_sha256:
        return None
    text = copy_text.decode("utf-8")
    at = text.find(EDITION_MARK)
    if at < 0 or not text.endswith("}"):
        return None
    edition = text[at + len(EDITION_MARK) : -1].encode("utf-8")
    return edition if hashlib.sha256(edition).hexdigest() == receipt.edition_sha256 else None


def scored_content(
    receipt: Receipt, read_store_copy: Callable[[str], bytes | None], root: Path, session: str
) -> dict[str, Any] | None:
    """The scored edition as a dict, from the store copy or a sealed radar edition with its sha256; None if lost."""
    if receipt.lost or receipt.edition_sha256 is None:
        return None
    copy = read_store_copy(receipt.path)
    edition = _edition_from_copy(copy, receipt) if copy is not None else None
    if edition is None:
        for folder in sorted((root / "data" / "radar").glob(f"{session}*")):
            candidate = folder / "edition.json"
            if candidate.is_file() and not verify(folder):
                data = candidate.read_bytes()
                if hashlib.sha256(data).hexdigest() == receipt.edition_sha256:
                    edition = data
                    break
    if edition is None:
        return None
    parsed = json.loads(edition.decode("utf-8-sig"))
    return parsed if isinstance(parsed, dict) else None


def directory_for(root: Path, session: str) -> Directory | None:
    """The first sealed radar folder of `session` that holds both directory files, parsed; None when there is none."""
    folders = sorted(
        (p for p in (root / "data" / "radar").glob(f"{session}*") if p.is_dir()),
        key=lambda p: int(p.name.split(".r")[1]) if ".r" in p.name else 1,
    )
    for folder in folders:
        a, b = folder / "nasdaqlisted.txt", folder / "otherlisted.txt"
        if a.is_file() and b.is_file() and not verify(folder):
            return Directory.parse(a.read_bytes().decode("latin-1"), b.read_bytes().decode("latin-1"))
    return None


def skill_hash(root: Path, session: str) -> str | None:
    """The Radar skill bundle's sha256 recorded by the first sealed radar folder of `session`."""
    folder = _first_folder(root / "data" / "radar", session)
    if folder is None:
        return None
    value = json.loads((folder / MANIFEST).read_text()).get("provenance", {}).get("skill_sha256")
    return value if isinstance(value, str) else None


Git = Callable[[Sequence[str]], str]


def _git(repo: Path) -> Git:
    def run(args: Sequence[str]) -> str:
        return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout

    return run


def desk_names_at(session: str, git: Git) -> frozenset[str]:
    """The desk's names in config/options_desk.yaml on origin/main as it stood at 09:30 New York on `session`."""
    at = datetime.combine(datetime.fromisoformat(session).date(), time(9, 30), NEW_YORK)
    commit = git(["rev-list", "-1", f"--before={at.isoformat()}", "origin/main"]).strip()
    if not commit:
        raise InputError(f"no origin/main commit before {at.isoformat()}")
    return parse_desk_names(git(["show", f"{commit}:config/options_desk.yaml"]))


_NAMES = re.compile(r"^names:\s*\[([^\]]*)\]\s*(#.*)?$", re.MULTILINE)


def parse_desk_names(text: str) -> frozenset[str]:
    """The `names: [A, B, ...]` line of config/options_desk.yaml (its only form); refused in any other form."""
    found = _NAMES.findall(text)
    if len(found) != 1:
        raise InputError("config/options_desk.yaml has no single one-line names list")
    names = [normalise_symbol(part.strip().strip("'\"")) for part in found[0][0].split(",") if part.strip()]
    if not names or any(n is None for n in names):
        raise InputError("config/options_desk.yaml names list has an unreadable symbol")
    return frozenset(n for n in names if n)


Fetch = Callable[[str, Mapping[str, str]], bytes]


def _http(url: str, headers: Mapping[str, str]) -> bytes:
    request = urllib.request.Request(url, headers=dict(headers))
    with urllib.request.urlopen(request, timeout=60) as response:
        data: bytes = response.read()
        return data


@dataclass
class Fetched:
    """Every raw page fetched, kept with its URL and sha256 so the read's inputs can be sealed."""

    pages: list[dict[str, str]]

    def add(self, url: str, data: bytes) -> None:
        self.pages.append({"url": url, "sha256": hashlib.sha256(data).hexdigest(), "body": data.decode("utf-8")})


def _paged(
    base: str, params: dict[str, str], headers: Mapping[str, str], fetch: Fetch, log: Fetched
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    token: str | None = None
    while True:
        query = dict(params, **({"page_token": token} if token else {}))
        url = f"{base}?{urllib.parse.urlencode(query)}"
        data = fetch(url, headers)
        log.add(url, data)
        doc = json.loads(data)
        out.append(doc)
        token = doc.get("next_page_token")
        if not token:
            return out


def spin_offs(
    symbols: Sequence[str], start: str, end: str, headers: Mapping[str, str], fetch: Fetch, log: Fetched
) -> dict[str, list[str]]:
    """Ex dates of spin-offs per source symbol in [start, end] (Alpaca /v1/corporate-actions, types=spin_off)."""
    out: dict[str, list[str]] = {}
    for i in range(0, len(symbols), 100):
        params = {
            "symbols": ",".join(symbols[i : i + 100]),
            "types": "spin_off",
            "start": start,
            "end": end,
            "limit": "1000",
        }
        for page in _paged("https://data.alpaca.markets/v1/corporate-actions", params, headers, fetch, log):
            for row in (page.get("corporate_actions") or {}).get("spin_offs") or []:
                out.setdefault(row["source_symbol"], []).append(row["ex_date"])
    return out


def daily_bars(
    symbols: Sequence[str], start: str, end: str, headers: Mapping[str, str], fetch: Fetch, log: Fetched
) -> dict[str, dict[str, tuple[float, float]]]:
    """SIP daily bars (adjustment=all) as {symbol: {YYYY-MM-DD New York: (o, c)}}, every page read."""
    out: dict[str, dict[str, tuple[float, float]]] = {}
    for i in range(0, len(symbols), 100):
        params = {
            "symbols": ",".join(symbols[i : i + 100]),
            "timeframe": "1Day",
            "feed": "sip",
            "adjustment": "all",
            "start": start,
            "end": end,
            "limit": "10000",
        }
        for page in _paged("https://data.alpaca.markets/v2/stocks/bars", params, headers, fetch, log):
            for symbol, bars in (page.get("bars") or {}).items():
                for bar in bars:
                    day = (
                        datetime.fromisoformat(bar["t"].replace("Z", "+00:00")).astimezone(NEW_YORK).date().isoformat()
                    )
                    out.setdefault(symbol, {})[day] = (float(bar["o"]), float(bar["c"]))
    return out
