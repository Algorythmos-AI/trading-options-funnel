"""EXP-0038's status (counts only, any time) and its read (once, at the read date): trading-lab-research DEC-0055.

`status` reports, for every regular session from the start, whether it is counted and why not, from the sealed files
only. It computes no return and no statistic, so it may run before the read (DEC-0055 section 5).

`read` refuses unless the read date has come, the analysis code is exactly the frozen commit, and no report exists. It
then makes the read-time checks, assembles each trial's sessions and names, fetches bars and corporate actions once,
works out the verdicts with `options_funnel.exp0038.decide`, and writes the report once with the sha256 of every input.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from options_funnel import calendar
from options_funnel import exp0038 as x
from options_funnel.exp0038_inputs import (
    Fetch,
    Fetched,
    Git,
    InputError,
    daily_bars,
    desk_names_at,
    directory_for,
    listing_matches,
    load_receipts,
    scored_content,
    skill_hash,
    spin_offs,
)


@dataclass
class SessionStatus:
    session: str
    counted: bool
    reason: str
    receipt: x.Receipt | None = None
    rows: list[dict[str, Any]] = field(default_factory=list)


def statuses(root: Path, start: str, until: str) -> list[SessionStatus]:
    """Every regular session from `start` to `until`, counted or with its reason (DEC-0055 section 1)."""
    cal = calendar.load()
    out: list[SessionStatus] = []
    day = start if cal.is_session(start) else cal.session_plus(cal.previous_session(start), 1)
    while day <= until:
        loaded = load_receipts(root, day)
        receipts, rows = loaded if loaded else ([], [])
        counted, reason = x.counted_for(day, loaded is not None, receipts, directory_for(root, day) is not None)
        receipt = None
        if counted:
            receipt, _ = x.scored_receipt(receipts, cal.previous_close(day), day)
        out.append(SessionStatus(day, counted, reason, receipt, rows))
        day = cal.session_plus(day, 1)
    return out


def last_countable(sessions: Sequence[SessionStatus], close_date: str = "2027-06-30") -> str | None:
    """The 120th counted session, or the close date if fewer are counted by then (None while neither has come)."""
    counted = [s.session for s in sessions if s.counted]
    if len(counted) >= 120:
        return counted[119]
    if sessions and sessions[-1].session >= close_date:
        return close_date
    return None


def early_end(sessions: Sequence[SessionStatus]) -> str | None:
    """The last session before 10 consecutive regular sessions were not counted, or None."""
    run = 0
    for i, s in enumerate(sessions):
        run = 0 if s.counted else run + 1
        if run == 10:
            first_missed = i - 9
            return sessions[first_missed - 1].session if first_missed > 0 else None
    return None


def status_report(root: Path, start: str, until: str) -> dict[str, Any]:
    """Counts only: counted sessions and the reasons the others are not counted. No return, no statistic."""
    rows = statuses(root, start, until)
    return {
        "start": start,
        "until": until,
        "sessions": len(rows),
        "counted": sum(1 for r in rows if r.counted),
        "not_counted_by_reason": dict(Counter(r.reason for r in rows if not r.counted)),
        "last_countable": last_countable(rows),
        "early_end_after": early_end(rows),
    }


# ---- the read ------------------------------------------------------------------------------------------------------


class ReadRefused(RuntimeError):
    """The read may not run (too early, code not frozen, already done)."""


def read_date(sessions: Sequence[SessionStatus]) -> str | None:
    """After the close of the fourth regular session after the last countable session."""
    last = last_countable(sessions)
    return None if last is None else calendar.load().session_plus(last, 4)


@dataclass
class ReadInputs:
    """What the read needs from outside the sealed files, injectable for tests."""

    store_listing: Callable[[str], list[dict[str, Any]]]  # the dashboard's rows for a date, at the read
    read_store_copy: Callable[[str], bytes | None]
    git: Git  # trading-lab-research, for desk names over time
    fetch: Fetch
    alpaca_headers: Mapping[str, str]


def _sha(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def run_read(root: Path, start: str, now_session: str, inputs: ReadInputs) -> dict[str, Any]:
    """The whole read, as one report. Refuses before the read date."""
    sessions = statuses(root, start, now_session)
    when = read_date(sessions)
    if when is None or now_session <= when:
        raise ReadRefused(f"the read date has not passed (read after the close of {when})")
    last = last_countable(sessions)
    assert last is not None
    window = [s for s in sessions if s.session <= last]
    stop = early_end(window)
    if stop is not None:
        return {"verdict": "early_end", "after": stop, "trials": {}}
    counted = [s for s in window if s.counted]

    analysed: list[tuple[SessionStatus, dict[str, Any]]] = []
    reasons: Counter[str] = Counter(s.reason for s in window if not s.counted)
    for s in counted:
        if not listing_matches(s.rows, inputs.store_listing(s.session)):
            reasons["listing_mismatch"] += 1
            continue
        assert s.receipt is not None
        edition = scored_content(s.receipt, inputs.read_store_copy, root, s.session)
        if edition is None:
            reasons["content_lost"] += 1
            continue
        analysed.append((s, edition))

    cal = calendar.load()
    names_by_trial: dict[str, dict[str, list[str]]] = {k: {} for k in x.LISTS}
    out_reasons: Counter[str] = Counter()
    symbols: set[str] = {"RSP", "IWM"}
    for s, edition in analysed:
        directory = directory_for(root, s.session)
        assert directory is not None
        desk_added = desk_names_at(s.session, inputs.git) - x.DESK_NAMES
        for list_name in sorted(set(x.LISTS.values())):
            listed = x.find_list(edition, list_name)
            if listed is None:
                continue
            kept, out = x.scored_names(listed, directory, frozenset(desk_added))
            # Counted once per list and session, whichever trials share the list.
            out_reasons.update(reason for _, reason in out)
            symbols.update(kept)
            for trial, name in x.LISTS.items():
                if name == list_name:
                    names_by_trial[trial][s.session] = kept

    fetched = Fetched([])
    first, last_day = (
        (analysed[0][0].session, cal.session_plus(analysed[-1][0].session, 4)) if analysed else (start, start)
    )
    bars = daily_bars(sorted(symbols), first, last_day, inputs.alpaca_headers, inputs.fetch, fetched)
    actions = spin_offs(sorted(symbols - {"RSP", "IWM"}), first, last_day, inputs.alpaca_headers, inputs.fetch, fetched)

    def window_of(day: str, n: int) -> list[str]:
        return [cal.session_plus(day, k) for k in range(n)]

    series: dict[str, list[float]] = {"HYP-0065": [], "HYP-0067": []}
    counted_66: list[float | None] = []
    pooled: list[float] = []
    largest_share: list[float] = []
    names_per_session: list[int] = []
    for s, _edition in analysed:
        for trial in ("HYP-0065", "HYP-0066", "HYP-0067"):
            names = names_by_trial[trial].get(s.session)
            bench = x.BENCHMARKS[trial]
            days = window_of(s.session, 5 if trial == "HYP-0066" else 1)
            value: float | None = None
            if names is not None and bars.get(bench, {}).get(s.session):
                kept = [n for n in names if not any(days[0] <= d <= days[-1] for d in actions.get(n, []))]
                if trial == "HYP-0066":
                    rets = {
                        n: r
                        for n in kept
                        if (r := x.five_session_return([bars.get(n, {}).get(d) for d in days])) is not None
                    }
                    bench_ret = x.five_session_return([bars[bench].get(d) for d in days])
                else:
                    rets = x.returns({n: [bars[n][s.session]] for n in kept if s.session in bars.get(n, {})}, kept)
                    bench_ret = bars[bench][s.session][1] / bars[bench][s.session][0] - 1
                if rets and bench_ret is not None:
                    value = x.d_s(rets, bench_ret)
                    if trial == "HYP-0065":
                        names_per_session.append(len(rets))
                        excess = [r - bench_ret for r in rets.values()]
                        pooled.extend(excess)
                        total = math.fsum(abs(e) for e in excess)
                        largest_share.append(max(abs(e) for e in excess) / total if total else 0.0)
            if trial == "HYP-0066":
                counted_66.append(value)
            elif value is not None:
                series[trial].append(value)

    verdicts = x.decide(len(analysed), series["HYP-0065"], counted_66, series["HYP-0067"])
    skills = [(s.session, skill_hash(root, s.session)) for s in counted]
    changes = [skills[i] for i in range(len(skills)) if i == 0 or skills[i][1] != skills[i - 1][1]]
    return {
        "verdict": "read",
        "last_countable": last,
        "counted": len(counted),
        "analysed": len(analysed),
        "not_counted_or_not_analysed": dict(reasons),
        "names_taken_out": dict(out_reasons),
        "trials": {k: {"verdict": v[0], **v[1]} for k, v in verdicts.items()},
        "name_weighted_check": x.mean(pooled) if len(analysed) >= x.MIN_ANALYSED and pooled else None,
        "largest_name_share_mean": x.mean(largest_share) if len(analysed) >= x.MIN_ANALYSED and largest_share else None,
        "scored_names_per_session": dict(Counter(names_per_session)) if len(analysed) >= x.MIN_ANALYSED else None,
        "skill_hash_changes": changes,
        "inputs": {"fetched_pages": [{"url": p["url"], "sha256": p["sha256"]} for p in fetched.pages]},
        "fetched_sha256": _sha([p["sha256"] for p in fetched.pages]),
    }


def check_frozen(repo: Path, frozen_commit: str, git: Git) -> None:
    """The read refuses unless this checkout is exactly the frozen commit, with nothing changed."""
    head = git(["rev-parse", "HEAD"]).strip()
    if head != frozen_commit:
        raise ReadRefused(f"the analysis code is at {head}, not the frozen {frozen_commit}")
    if git(["status", "--porcelain", "--untracked-files=no"]).strip():
        raise ReadRefused("the analysis checkout has changes")


def write_report(path: Path, report: Mapping[str, Any]) -> None:
    """Written once: refuses if a report is already there."""
    with open(path, "x", encoding="utf-8") as fh:
        fh.write(json.dumps(report, indent=1, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def now_session(now: datetime) -> str:
    """Today's date in New York."""
    return now.astimezone(x.NEW_YORK).date().isoformat()


__all__ = ["InputError", "ReadInputs", "ReadRefused", "check_frozen", "run_read", "status_report", "write_report"]
