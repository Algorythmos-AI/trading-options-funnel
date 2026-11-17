"""EXP-0038's status (counts only, any time) and its read (once, at the read date): trading-lab-research DEC-0055.

`status_report` reports, for every regular session from the start, whether it is counted and why not, from the sealed
files only. It computes no return and no statistic, so it may run before the read (DEC-0055 section 5).

`run_read` refuses unless the read date has passed and no read exists under `<root>/exp0038-read/`. It then:
1. ends early with no verdict if 10 consecutive regular sessions were not counted inside the window;
2. makes the read-time checks (the store's listing, the scored edition's content); fewer than 60 analysed sessions
   returns every trial inconclusive before any bar is fetched or any return worked out;
3. assembles each list's names per session, fetches bars and corporate actions once (every page saved under
   `<root>/exp0038-read/inputs/`, reused as saved by a re-run), works out D_S per trial over the counted sessions in
   date order (None where a session is not included) and the verdicts with `exp0038.decide`;
4. writes `<root>/exp0038-read/report.json` once, with the sha256 of every sealed folder and every fetched page.

Report definitions the record leaves to the code: the name-weighted check is the mean of every scored name's excess
return over its benchmark, pooled over a trial's included sessions; the largest-name share of a session is
max |excess| / sum |excess| over its scored names, averaged over the trial's included sessions.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, time
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
    desk_names_commit,
    directory_for,
    listing_matches,
    load_receipts,
    scored_content,
    sealed_at,
    skill_hash,
    spin_offs,
)
from options_funnel.seal import MANIFEST, verify

CLOSE_DATE = "2027-06-30"
READ_DIR = "exp0038-read"
REPORT = "report.json"


@dataclass
class SessionStatus:
    session: str
    counted: bool
    reason: str
    receipt: x.Receipt | None = None
    rows: list[dict[str, Any]] = field(default_factory=list)


def _listing_deadline(day: str) -> datetime:
    """A day's receipt listing must be sealed before the next regular session opens."""
    nxt = calendar.load().session_plus(day, 1)
    return datetime.combine(datetime.fromisoformat(nxt).date(), time(9, 30), x.NEW_YORK)


def statuses(root: Path, start: str, until: str) -> list[SessionStatus]:
    """Every regular session from `start` to `until`, counted or with its reason (DEC-0055 section 1)."""
    cal = calendar.load()
    out: list[SessionStatus] = []
    day = start if cal.is_session(start) else cal.session_plus(cal.previous_session(start), 1)
    while day <= until:
        folder = root / "data" / "radar-receipts" / day
        if folder.is_dir() and verify(folder):
            out.append(SessionStatus(day, False, "listing_broken"))
        elif folder.is_dir() and ((at := sealed_at(folder)) is None or at >= _listing_deadline(day)):
            out.append(SessionStatus(day, False, "listing_sealed_late"))
        else:
            loaded = load_receipts(root, day)
            receipts, rows = loaded if loaded else ([], [])
            counted, reason = x.counted_for(day, loaded is not None, receipts, directory_for(root, day) is not None)
            receipt = x.scored_receipt(receipts, cal.previous_close(day), day)[0] if counted else None
            out.append(SessionStatus(day, counted, reason, receipt, rows))
        day = cal.session_plus(day, 1)
    return out


def last_countable(sessions: Sequence[SessionStatus], close_date: str = CLOSE_DATE) -> str | None:
    """The 120th counted session, or the close date if fewer are counted by then (None while neither has come)."""
    counted = [s.session for s in sessions if s.counted and s.session <= close_date]
    if len(counted) >= 120:
        return counted[119]
    if sessions and sessions[-1].session >= close_date:
        return close_date
    return None


def early_end(sessions: Sequence[SessionStatus]) -> tuple[bool, str | None]:
    """Whether 10 consecutive regular sessions were not counted, and the last session before them (None when the run
    of misses began at the first session)."""
    run = 0
    for i, s in enumerate(sessions):
        run = 0 if s.counted else run + 1
        if run == 10:
            first_missed = i - 9
            return True, sessions[first_missed - 1].session if first_missed > 0 else None
    return False, None


def _window(sessions: Sequence[SessionStatus]) -> list[SessionStatus]:
    last = last_countable(sessions)
    limit = last if last is not None else CLOSE_DATE
    return [s for s in sessions if s.session <= limit]


def status_report(root: Path, start: str, until: str) -> dict[str, Any]:
    """Counts only: counted sessions and the reasons the others are not counted. No return, no statistic."""
    rows = statuses(root, start, until)
    window = _window(rows)
    ended, after = early_end(window)
    return {
        "start": start,
        "until": until,
        "sessions": len(window),
        "counted": sum(1 for r in window if r.counted),
        "not_counted_by_reason": dict(Counter(r.reason for r in window if not r.counted)),
        "last_countable": last_countable(rows),
        "early_end": ended,
        "early_end_after": after,
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


def report_path(root: Path) -> Path:
    return root / READ_DIR / REPORT


def _folder_sha(folder: Path) -> str:
    return hashlib.sha256((folder / MANIFEST).read_bytes()).hexdigest()


def _cached(fetch: Fetch, inputs_dir: Path) -> Fetch:
    """Every page saved once under `inputs_dir` by the sha256 of its URL; a re-run reads the saved page, never anew."""
    inputs_dir.mkdir(parents=True, exist_ok=True)

    def wrapped(url: str, headers: Mapping[str, str]) -> bytes:
        path = inputs_dir / f"{hashlib.sha256(url.encode()).hexdigest()}.json"
        if path.exists():
            return path.read_bytes()
        data = fetch(url, headers)
        with open(path, "xb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        return data

    return wrapped


def _simple_return(bar: tuple[float, float] | None) -> float | None:
    return None if bar is None else bar[1] / bar[0] - 1


def run_read(
    root: Path, start: str, now_session: str, inputs: ReadInputs, provenance: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """The whole read, as one report written once under `<root>/exp0038-read/`. Refuses before the read date.
    `provenance` (the frozen commit, the read time) goes into the report as it is."""
    read_dir = root / READ_DIR
    if report_path(root).exists():
        raise ReadRefused(f"{report_path(root)} exists; the read is written once")
    sessions = statuses(root, start, now_session)
    when = read_date(sessions)
    if when is None or now_session <= when:
        raise ReadRefused(f"the read date has not passed (read after the close of {when})")
    window = _window(sessions)
    counted = [s for s in window if s.counted]
    reasons: Counter[str] = Counter(s.reason for s in window if not s.counted)
    sealed_inputs = {
        str(f.relative_to(root)): _folder_sha(f)
        for s in counted
        for f in sorted((root / "data").glob(f"*/{s.session}*"))
        if (f / MANIFEST).is_file()
    }
    base: dict[str, Any] = {
        **(provenance or {}),
        "last_countable": last_countable(sessions),
        "counted": len(counted),
        "sealed_inputs": sealed_inputs,
    }

    ended, after = early_end(window)
    if ended:
        return _write(read_dir, base | {"verdict": "early_end", "after": after, "not_counted": dict(reasons)})

    analysed: dict[str, dict[str, Any]] = {}
    for s in counted:
        if not listing_matches(s.rows, inputs.store_listing(s.session)):
            reasons["listing_mismatch"] += 1
            continue
        assert s.receipt is not None
        edition = scored_content(s.receipt, inputs.read_store_copy, root, s.session)
        if edition is None:
            reasons["content_lost"] += 1
            continue
        analysed[s.session] = edition
    base |= {"analysed": len(analysed), "not_counted_or_not_analysed": dict(reasons)}
    if len(analysed) < x.MIN_ANALYSED:
        # No bar is fetched and no return worked out (DEC-0055 section 1).
        short = {k: {"verdict": "inconclusive", "analysed": len(analysed)} for k in x.LISTS}
        return _write(read_dir, base | {"verdict": "read", "trials": short})

    cal = calendar.load()
    list_names = sorted(set(x.LISTS.values()))
    names: dict[str, dict[str, list[str]]] = {k: {} for k in list_names}
    taken_out: dict[str, Counter[str]] = {k: Counter() for k in list_names}
    missing_list: dict[str, list[str]] = {k: [] for k in list_names}
    desk_commits: dict[str, str] = {}
    symbols: set[str] = {"RSP", "IWM"}
    for day, edition in analysed.items():
        directory = directory_for(root, day)
        assert directory is not None
        commit, desk_now = desk_names_commit(day, inputs.git)
        desk_commits[day] = commit
        for list_name in list_names:
            listed = x.find_list(edition, list_name)
            if listed is None:
                missing_list[list_name].append(day)
                continue
            kept, out = x.scored_names(listed, directory, frozenset(desk_now - x.DESK_NAMES))
            taken_out[list_name].update(reason for _, reason in out)
            names[list_name][day] = kept
            symbols.update(kept)

    first = min(analysed)
    last_day = cal.session_plus(max(analysed), 4)
    fetched = Fetched([])
    fetch = _cached(inputs.fetch, read_dir / "inputs")
    bars = daily_bars(sorted(symbols), first, last_day, inputs.alpaca_headers, fetch, fetched)
    actions = spin_offs(sorted(symbols - {"RSP", "IWM"}), first, last_day, inputs.alpaca_headers, fetch, fetched)

    def ret(symbol: str, days: list[str]) -> float | None:
        own = [bars.get(symbol, {}).get(d) for d in days]
        return x.five_session_return(own) if len(days) > 1 else _simple_return(own[0])

    trials: dict[str, dict[str, Any]] = {}
    series: dict[str, list[float | None]] = {}
    for trial, list_name in x.LISTS.items():
        bench = x.BENCHMARKS[trial]
        n_days = 5 if trial == "HYP-0066" else 1
        not_included: Counter[str] = Counter()
        name_reasons: Counter[str] = Counter()
        values: list[float | None] = []
        pooled: list[float] = []
        shares: list[float] = []
        per_session: Counter[int] = Counter()
        for s in counted:
            value: float | None = None
            scored = names[list_name].get(s.session) if s.session in analysed else None
            if s.session not in analysed:
                pass
            elif scored is None:
                not_included["no_list"] += 1
            else:
                days = [cal.session_plus(s.session, k) for k in range(n_days)]
                bench_ret = ret(bench, days)
                if bench_ret is None:
                    not_included["no_benchmark"] += 1
                else:
                    rets: dict[str, float] = {}
                    for n in scored:
                        if any(days[0] <= d <= days[-1] for d in actions.get(n, [])):
                            name_reasons["corporate_action"] += 1
                            continue
                        r = ret(n, days)
                        if r is None:
                            name_reasons["no_bars"] += 1
                            continue
                        rets[n] = r
                    if not rets:
                        not_included["no_names"] += 1
                    else:
                        value = x.d_s(rets, bench_ret)
                        excess = [r - bench_ret for r in rets.values()]
                        pooled.extend(excess)
                        total = math.fsum(abs(e) for e in excess)
                        shares.append(max(abs(e) for e in excess) / total if total else 0.0)
                        per_session[len(rets)] += 1
            values.append(value)
        series[trial] = values
        trials[trial] = {
            "included": sum(1 for v in values if v is not None),
            "not_included": dict(not_included),
            "names_counted_not_scored": dict(name_reasons),
            "name_weighted_check": x.mean(pooled) if pooled else None,
            "largest_name_share_mean": x.mean(shares) if shares else None,
            "scored_names_per_session": {str(k): v for k, v in sorted(per_session.items())},
        }

    verdicts = x.decide(
        len(analysed),
        [v for v in series["HYP-0065"] if v is not None],
        series["HYP-0066"],
        [v for v in series["HYP-0067"] if v is not None],
    )
    for trial, (verdict, stats) in verdicts.items():
        trials[trial] |= {"verdict": verdict, **stats}
    skills = [(day, skill_hash(root, day)) for day in sorted(analysed)]
    changes = [list(skills[i]) for i in range(len(skills)) if i == 0 or skills[i][1] != skills[i - 1][1]]
    return _write(
        read_dir,
        base
        | {
            "verdict": "read",
            "trials": trials,
            "names_taken_out_by_list": {k: dict(v) for k, v in taken_out.items()},
            "list_missing_on": missing_list,
            "skill_hash_changes": changes,
            "desk_config_commits": desk_commits,
            "fetched_pages": [{"url": p["url"], "sha256": p["sha256"]} for p in fetched.pages],
        },
    )


def _write(read_dir: Path, report: dict[str, Any]) -> dict[str, Any]:
    """The report, written once: refuses if one is already there."""
    read_dir.mkdir(parents=True, exist_ok=True)
    with open(read_dir / REPORT, "x", encoding="utf-8") as fh:
        fh.write(json.dumps(report, indent=1, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return report


def check_frozen(repo: Path, frozen_commit: str, git: Git) -> None:
    """The read refuses unless this checkout is exactly the frozen commit, with nothing changed."""
    head = git(["rev-parse", "HEAD"]).strip()
    if head != frozen_commit:
        raise ReadRefused(f"the analysis code is at {head}, not the frozen {frozen_commit}")
    if git(["status", "--porcelain", "--untracked-files=no"]).strip():
        raise ReadRefused("the analysis checkout has changes")


def now_session(now: datetime) -> str:
    """Today's date in New York."""
    return now.astimezone(x.NEW_YORK).date().isoformat()


__all__ = ["InputError", "ReadInputs", "ReadRefused", "check_frozen", "report_path", "run_read", "status_report"]
