"""The pieces DEC-0055 needs beside a sealed radar edition (trading-lab-research DEC-0055 sections 1, 2 and 8).

- The Nasdaq Trader symbol directory of the day (`nasdaqlisted.txt`, `otherlisted.txt`), sealed with the edition
  before the open, so every decision about a symbol is made from what was listed that morning.
- The dashboard's listing of the radar edition copies for a date (`GET /api/research/radar-copies`), sealed after the
  open as `data/radar-receipts/<date>`: which editions the dashboard received, and when, by the store's own clock.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime
from datetime import time as clock_time
from typing import Any
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")
DIRECTORY_BASE = "https://www.nasdaqtrader.com/dynamic/SymDir/"
DIRECTORY_FILES = ("nasdaqlisted.txt", "otherlisted.txt")
_HEADERS = {"nasdaqlisted.txt": "Symbol|", "otherlisted.txt": "ACT Symbol|"}
LISTING_SCHEMA = "stocksdelta/radar-copies"
TIMEOUT_S = 30
_RECEIVED = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z")
_HEX64 = re.compile(r"[0-9a-f]{64}")


class WitnessError(RuntimeError):
    """A directory file or a receipt listing is missing, malformed or refused."""


DIRECTORY_COLUMNS = {
    "nasdaqlisted.txt": ("Symbol", "Test Issue", "ETF"),
    "otherlisted.txt": ("ACT Symbol", "CQS Symbol", "Test Issue", "ETF"),
}
USER_AGENT = "trading-options-funnel/1 (research; no orders)"
TRIES = 3


def check_directory_file(name: str, data: bytes) -> None:
    """A Nasdaq Trader directory file: its header with every column the read uses, and the creation-time footer."""
    if name not in _HEADERS:
        raise WitnessError(f"unknown directory file {name}")
    text = data.decode("latin-1")
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 3 or not lines[0].startswith(_HEADERS[name]):
        raise WitnessError(f"{name} does not start with its header")
    header = lines[0].split("|")
    missing = [c for c in DIRECTORY_COLUMNS[name] if c not in header]
    if missing:
        raise WitnessError(f"{name} has no {', '.join(missing)} column")
    if not lines[-1].startswith("File Creation Time:"):
        raise WitnessError(f"{name} does not end with its creation-time line")


def _get(url: str, headers: dict[str, str], sleep: Callable[[float], None] = time.sleep, tries: int = TRIES) -> bytes:
    """GET with a plain User-Agent; retried on a timeout, a network error or a 5xx; never on a 4xx."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **headers})
    last = "no attempt"
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
                data: bytes = response.read()
                return data
        except urllib.error.HTTPError as error:
            if error.code < 500:
                raise WitnessError(f"HTTP {error.code}") from error
            last = f"HTTP {error.code}"
        except (urllib.error.URLError, TimeoutError) as error:
            last = type(error).__name__
        if attempt + 1 < tries:
            sleep(2 ** (attempt + 1))
    raise WitnessError(f"{last} after {tries} tries")


def fetch_directory(base: str = DIRECTORY_BASE) -> dict[str, bytes]:
    """Both directory files, as served, each checked."""
    out: dict[str, bytes] = {}
    for name in DIRECTORY_FILES:
        try:
            data = _get(base + name, {})
        except WitnessError as error:
            raise WitnessError(f"could not fetch {name}: {error}") from error
        check_directory_file(name, data)
        out[name] = data
    return out


def sha256_file(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def listing_signature(secret: str, ts: int, date: str) -> str:
    message = f"{ts}.GET /api/research/radar-copies?date={date}".encode()
    return "sha256=" + hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def after_open(date: str, now: datetime) -> bool:
    """True once 09:30 New York on `date` has passed: before it, the day's listing may still grow."""
    opening = datetime.combine(datetime.fromisoformat(date).date(), clock_time(9, 30), NEW_YORK)
    return now >= opening


def check_listing(data: bytes, date: str) -> dict[str, Any]:
    try:
        doc = json.loads(data)
    except ValueError as error:
        raise WitnessError("the listing is not JSON") from error
    if not isinstance(doc, dict) or doc.get("schema") != LISTING_SCHEMA or doc.get("version") != 1:
        raise WitnessError("not a stocksdelta/radar-copies v1 listing")
    if doc.get("date") != date or not isinstance(doc.get("copies"), list):
        raise WitnessError("the listing is for another date or has no copies list")
    for row in doc["copies"]:
        keys = {"path", "received_at", "run_id", "lost", "copy_sha256", "edition_sha256"}
        if not isinstance(row, dict) or set(row) != keys or not isinstance(row["lost"], bool):
            raise WitnessError("a listing row has the wrong keys")
        path = row["path"]
        if not isinstance(path, str) or not path.startswith(f"radar/copies/{date}/") or not path.endswith(".json"):
            raise WitnessError("a listing row is for another date")
        if path.endswith(".lost.json") != row["lost"]:
            raise WitnessError("a row's lost flag does not match its path")
        if not isinstance(row["received_at"], str) or not _RECEIVED.fullmatch(row["received_at"]):
            raise WitnessError("a listing row's receipt time is not ISO with milliseconds and Z")
        if not isinstance(row["run_id"], str) or not row["run_id"]:
            raise WitnessError("a listing row has no run id")
        hashes = (row["copy_sha256"], row["edition_sha256"])
        if row["lost"] and hashes != (None, None):
            raise WitnessError("a lost row has hashes")
        if not row["lost"] and not all(isinstance(h, str) and _HEX64.fullmatch(h) for h in hashes):
            raise WitnessError("a kept row lacks its hashes")
    return doc


def fetch_listing(base: str, date: str, now: float) -> bytes:
    """The dashboard's listing for `date`, signed with the research key; bytes as served, checked."""
    secret = os.environ.get("RADAR_INGEST_SECRET")
    if not secret:
        raise WitnessError("RADAR_INGEST_SECRET is not set")
    headers = {"x-wt-key-id": "radar"}
    bypass = os.environ.get("VERCEL_AUTOMATION_BYPASS_SECRET")
    if bypass:
        headers["x-vercel-protection-bypass"] = bypass
    # Each try is signed afresh, so a retry is never refused as stale.
    for attempt in range(TRIES):
        ts = int(now) if attempt == 0 else int(time.time())
        headers["x-wt-timestamp"] = str(ts)
        headers["x-wt-signature"] = listing_signature(secret, ts, date)
        try:
            data = _get(f"{base}/api/research/radar-copies?date={date}", headers, tries=1)
            break
        except WitnessError as error:
            if not str(error).startswith(("HTTP 5", "URLError", "TimeoutError")) or attempt + 1 == TRIES:
                raise WitnessError(f"listing failed: {error}") from error
            time.sleep(2 ** (attempt + 1))
    check_listing(data, date)
    return data
