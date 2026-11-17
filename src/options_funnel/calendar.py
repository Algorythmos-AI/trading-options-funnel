"""The New York session calendar DEC-0055 uses: trading-lab-research's `dashboard/src/lib/market-calendar.json`, copied
into `contracts/market-calendar.json` and pinned by sha256 in `contracts/market-calendar.lock.json`.

Regular sessions, their closes (16:00 New York, 13:00 on a half day) and the n-th session after a date, nothing else.
"""

from __future__ import annotations

import bisect
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, time
from functools import cache
from pathlib import Path
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")
CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"


class CalendarError(ValueError):
    """A date outside the calendar, or a calendar that is not the pinned one."""


@dataclass(frozen=True)
class Calendar:
    sessions: tuple[str, ...]
    half_days: dict[str, str]
    first: str
    last: str

    def is_session(self, day: str) -> bool:
        self._in_range(day)
        i = bisect.bisect_left(self.sessions, day)
        return i < len(self.sessions) and self.sessions[i] == day

    def close(self, day: str) -> datetime:
        """The close of a regular session in New York time (13:00 on a half day)."""
        if not self.is_session(day):
            raise CalendarError(f"{day} is not a regular session")
        hh, mm = (int(x) for x in self.half_days.get(day, "16:00").split(":"))
        return datetime.combine(date.fromisoformat(day), time(hh, mm), NEW_YORK)

    def previous_session(self, day: str) -> str:
        self._in_range(day)
        i = bisect.bisect_left(self.sessions, day)
        if i == 0:
            raise CalendarError(f"no session before {day} in the calendar")
        return self.sessions[i - 1]

    def previous_close(self, day: str) -> datetime:
        """The close of the regular session before `day`."""
        return self.close(self.previous_session(day))

    def session_plus(self, day: str, n: int) -> str:
        """The n-th regular session after the session `day` (n = 0 is `day` itself)."""
        if not self.is_session(day):
            raise CalendarError(f"{day} is not a regular session")
        i = self.sessions.index(day) + n
        if i >= len(self.sessions):
            raise CalendarError(f"the calendar ends before {n} sessions after {day}")
        return self.sessions[i]

    def _in_range(self, day: str) -> None:
        if not (self.first <= day <= self.last):
            raise CalendarError(f"{day} is outside the calendar ({self.first} to {self.last})")


@cache
def load(contracts: Path = CONTRACTS) -> Calendar:
    """The pinned calendar; refuses a copy whose sha256 is not the lock's."""
    data = (contracts / "market-calendar.json").read_bytes()
    lock = json.loads((contracts / "market-calendar.lock.json").read_text())
    if hashlib.sha256(data).hexdigest() != lock["sha256"]:
        raise CalendarError("contracts/market-calendar.json is not the pinned copy")
    doc = json.loads(data)
    return Calendar(
        sessions=tuple(sorted(doc["sessions"])),
        half_days={h["date"]: h["close_et"] for h in doc["half_days"]},
        first=doc["range"]["start"],
        last=doc["range"]["end"],
    )
