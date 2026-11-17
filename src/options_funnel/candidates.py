"""One record per candidate per stage: what was judged, the verdict, why, and a hash of the inputs it was judged on.

Records are written as JSON Lines, one file per session and stage, so any day can be replayed and compared.
Reading is strict: exactly the known keys, exact types, no duplicate JSON keys, one spelling per value.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any

from options_funnel.blind import assert_blind, is_outcome_words

SCHEMA = "options-funnel/candidate"
SCHEMA_VERSION = 1

_SESSION = re.compile(r"\d{4}-\d{2}-\d{2}")
_SYMBOL = re.compile(r"[A-Z][A-Z0-9.]{0,9}")
_REASON = re.compile(r"[a-z][a-z0-9_]{1,47}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_KEYS = frozenset(
    {"schema", "schema_version", "session", "stage", "symbol", "verdict", "reason", "inputs_sha256", "score", "rank"}
)


class Stage(StrEnum):
    RADAR = "radar"
    FOCUS_PICK = "focus_pick"
    PREMARKET = "premarket"
    BOARD = "board"


class Verdict(StrEnum):
    PICKED = "picked"
    REJECTED = "rejected"
    VETOED = "vetoed"
    NOT_SCORED = "not_scored"


def canonical(value: Any) -> Any:
    """The value hashed for `inputs_sha256`: equal inputs give one form whatever produced them.

    Integral floats become ints (1.0 -> 1, -0.0 -> 0), strings are NFC-normalised, tuples become lists. Non-finite
    floats, non-string keys and anything that is not plain JSON are refused.
    """
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("inputs must not contain NaN or infinity")
        return int(value) if value.is_integer() else value
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, (list, tuple)):
        return [canonical(item) for item in value]
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("input keys must be strings")
        out: dict[str, Any] = {}
        for key, item in value.items():
            name = unicodedata.normalize("NFC", key)
            if name in out:
                raise ValueError(f"keys {key!r} collide after normalising")
            out[name] = canonical(item)
        return out
    raise ValueError(f"{type(value).__name__} is not plain JSON")


def inputs_sha256(inputs: Any) -> str:
    """sha256 of the canonical JSON of `inputs` (see `canonical`; sorted keys, no spaces, UTF-8)."""
    text = json.dumps(canonical(inputs), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _text(name: str, value: object, pattern: re.Pattern[str]) -> None:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"bad {name} {value!r}")


@dataclass(frozen=True, slots=True)
class CandidateRecord:
    session: str
    stage: Stage
    symbol: str
    verdict: Verdict
    reason: str
    inputs_sha256: str
    score: float | None = None
    rank: int | None = None

    def __post_init__(self) -> None:
        _text("session", self.session, _SESSION)
        if date.fromisoformat(self.session).isoformat() != self.session:
            raise ValueError(f"bad session {self.session!r}")
        if not isinstance(self.stage, Stage):
            raise ValueError(f"stage must be a Stage, not {self.stage!r}")
        if not isinstance(self.verdict, Verdict):
            raise ValueError(f"verdict must be a Verdict, not {self.verdict!r}")
        _text("symbol", self.symbol, _SYMBOL)
        _text("reason", self.reason, _REASON)
        if is_outcome_words(self.reason.split("_")):
            raise ValueError(f"reason {self.reason!r} names an outcome (blind rule)")
        _text("inputs_sha256", self.inputs_sha256, _SHA256)
        if self.score is not None:
            if isinstance(self.score, bool) or not isinstance(self.score, (int, float)):
                raise ValueError("score must be a finite number or None")
            try:
                value = float(self.score)
            except OverflowError as error:
                raise ValueError("score is out of range") from error
            if not math.isfinite(value):
                raise ValueError("score must be a finite number or None")
            # One spelling per value: 2 and 2.0 are the same score and are stored as 2.
            if isinstance(self.score, float) and value.is_integer():
                object.__setattr__(self, "score", int(value))
        if self.rank is not None and (isinstance(self.rank, bool) or not isinstance(self.rank, int) or self.rank < 1):
            raise ValueError("rank must be a positive integer or None")

    def to_row(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "schema": SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "session": self.session,
            "stage": self.stage.value,
            "symbol": self.symbol,
            "verdict": self.verdict.value,
            "reason": self.reason,
            "inputs_sha256": self.inputs_sha256,
            "score": self.score,
            "rank": self.rank,
        }
        assert_blind(row)
        return row

    def to_json(self) -> str:
        return json.dumps(self.to_row(), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)

    @classmethod
    def from_json(cls, line: str) -> CandidateRecord:
        row = json.loads(line, object_pairs_hook=_no_duplicate_keys)
        if not isinstance(row, dict) or set(row) != _KEYS:
            raise ValueError(f"keys must be exactly {sorted(_KEYS)}")
        assert_blind(row)
        version = row["schema_version"]
        if row["schema"] != SCHEMA or type(version) is not int or version != SCHEMA_VERSION:
            raise ValueError("not an options-funnel/candidate v1 record")
        for name in ("stage", "verdict"):
            if not isinstance(row[name], str):
                raise ValueError(f"bad {name} {row[name]!r}")
        return cls(
            session=row["session"],
            stage=Stage(row["stage"]),
            symbol=row["symbol"],
            verdict=Verdict(row["verdict"]),
            reason=row["reason"],
            inputs_sha256=row["inputs_sha256"],
            score=row["score"],
            rank=row["rank"],
        )


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for key, value in pairs:
        if key in row:
            raise ValueError(f"duplicate key {key!r}")
        row[key] = value
    return row


def dump_jsonl(records: Iterable[CandidateRecord]) -> str:
    """Records as JSON Lines, refused if one session/stage holds the same symbol twice."""
    seen: set[tuple[str, Stage, str]] = set()
    lines: list[str] = []
    for record in records:
        key = (record.session, record.stage, record.symbol)
        if key in seen:
            raise ValueError(f"duplicate candidate {key}")
        seen.add(key)
        lines.append(record.to_json())
    return "".join(line + "\n" for line in lines)


def load_jsonl(text: str) -> Iterator[CandidateRecord]:
    """Records from JSON Lines; any bad line or repeated session/stage/symbol raises with its line number."""
    seen: set[tuple[str, Stage, str]] = set()
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = CandidateRecord.from_json(line)
        except (ValueError, KeyError, TypeError) as error:
            raise ValueError(f"line {number}: {error}") from error
        key = (record.session, record.stage, record.symbol)
        if key in seen:
            raise ValueError(f"line {number}: duplicate candidate {key}")
        seen.add(key)
        yield record
