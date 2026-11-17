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
import urllib.error
import urllib.request
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")
DIRECTORY_BASE = "https://www.nasdaqtrader.com/dynamic/SymDir/"
DIRECTORY_FILES = ("nasdaqlisted.txt", "otherlisted.txt")
_HEADERS = {"nasdaqlisted.txt": "Symbol|", "otherlisted.txt": "ACT Symbol|"}
LISTING_SCHEMA = "stocksdelta/radar-copies"
TIMEOUT_S = 30


class WitnessError(RuntimeError):
    """A directory file or a receipt listing is missing, malformed or refused."""


def check_directory_file(name: str, data: bytes) -> None:
    """A Nasdaq Trader directory file: its header, an ETF column, and the creation-time footer as its last line."""
    if name not in _HEADERS:
        raise WitnessError(f"unknown directory file {name}")
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError as error:
        raise WitnessError(f"{name} is not ASCII") from error
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 3 or not lines[0].startswith(_HEADERS[name]):
        raise WitnessError(f"{name} does not start with its header")
    if "ETF" not in lines[0].split("|"):
        raise WitnessError(f"{name} has no ETF column")
    if not lines[-1].startswith("File Creation Time:"):
        raise WitnessError(f"{name} does not end with its creation-time line")


def fetch_directory(base: str = DIRECTORY_BASE) -> dict[str, bytes]:
    """Both directory files, as served, each checked."""
    out: dict[str, bytes] = {}
    for name in DIRECTORY_FILES:
        try:
            with urllib.request.urlopen(base + name, timeout=TIMEOUT_S) as response:
                data: bytes = response.read()
        except (urllib.error.URLError, TimeoutError) as error:
            raise WitnessError(f"could not fetch {name}: {type(error).__name__}") from error
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
    opening = datetime.combine(datetime.fromisoformat(date).date(), time(9, 30), NEW_YORK)
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
        keys = {"path", "received_at", "run_id", "copy_sha256", "edition_sha256"}
        if not isinstance(row, dict) or set(row) != keys:
            raise WitnessError("a listing row has the wrong keys")
        if not str(row["path"]).startswith(f"radar/copies/{date}/"):
            raise WitnessError("a listing row is for another date")
    return doc


def fetch_listing(base: str, date: str, now: float) -> bytes:
    """The dashboard's listing for `date`, signed with the research key; bytes as served, checked."""
    secret = os.environ.get("RADAR_INGEST_SECRET")
    if not secret:
        raise WitnessError("RADAR_INGEST_SECRET is not set")
    ts = int(now)
    headers = {"x-wt-key-id": "radar", "x-wt-timestamp": str(ts), "x-wt-signature": listing_signature(secret, ts, date)}
    bypass = os.environ.get("VERCEL_AUTOMATION_BYPASS_SECRET")
    if bypass:
        headers["x-vercel-protection-bypass"] = bypass
    request = urllib.request.Request(f"{base}/api/research/radar-copies?date={date}", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            data: bytes = response.read()
    except urllib.error.HTTPError as error:
        raise WitnessError(f"listing refused: HTTP {error.code}") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise WitnessError(f"listing failed: {type(error).__name__}") from error
    check_listing(data, date)
    return data
