"""EXP-0038's status and read on synthetic sealed files, bars and listings only: no real data."""

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from options_funnel import exp0038 as x
from options_funnel import exp0038_read as r
from options_funnel.exp0038_inputs import (
    Fetched,
    InputError,
    daily_bars,
    desk_names_at,
    listing_matches,
    load_receipts,
    parse_desk_names,
    scored_content,
    spin_offs,
)
from options_funnel.seal import seal

HERE = Path(__file__).resolve().parent
DIRECTORY = {n: (HERE / "fixtures" / "directory" / n).read_bytes() for n in ("nasdaqlisted.txt", "otherlisted.txt")}
# The synthetic stock ACME is listed in this test's directory (and NEWCO, so that only the desk rule takes it out).
DIRECTORY["nasdaqlisted.txt"] = DIRECTORY["nasdaqlisted.txt"].replace(
    b"QQQ|", b"ACME|Acme Corp - Common Stock|Q|N|N|100|N|N\nNEWCO|Newco - Common Stock|Q|N|N|100|N|N\nQQQ|"
)
SESSIONS = ["2026-11-18", "2026-11-19", "2026-11-20", "2026-11-23", "2026-11-24"]


def edition(day: str, daily: list[str], small: list[str] | None = None) -> bytes:
    lists = [{"name": "DAILY RADAR", "symbols": daily}]
    if small is not None:
        lists.append({"name": "SMALL CAP MOMENTUM", "symbols": small})
    return json.dumps({"edition_date": day, "lists": lists}).encode()


def _compact(at: str) -> str:
    return at.replace("-", "").replace(":", "").replace(".", "")


def row(day: str, at: str, data: bytes, run: str = "r1", lost: bool = False) -> dict[str, Any]:
    return {
        "path": f"radar/copies/{day}/{_compact(at)}-{run}{'.lost' if lost else ''}.json",
        "received_at": at,
        "run_id": run,
        "lost": lost,
        "copy_sha256": None if lost else "c" * 64,
        "edition_sha256": None if lost else hashlib.sha256(data).hexdigest(),
    }


def make_session(
    root: Path,
    day: str,
    data: bytes,
    at: str = "",
    directory: bool = True,
    listing: bool = True,
    listing_at: str = "",
) -> list[dict[str, Any]]:
    files = {"edition.json": data, **(DIRECTORY if directory else {})}
    seal(root, "radar", day, files, f"{day}T11:13:00Z", {"skill_sha256": "s" * 64})
    rows = [row(day, at or f"{day}T11:12:40.123Z", data)]
    if listing:
        doc = {"schema": "stocksdelta/radar-copies", "version": 1, "date": day, "copies": rows}
        listed_at = listing_at or f"{day}T22:00:00Z"
        seal(root, "radar-receipts", day, {"listing.json": json.dumps(doc).encode()}, listed_at, {})
    return rows


def test_statuses_count_and_give_reasons(tmp_path: Path) -> None:
    make_session(tmp_path, "2026-11-18", edition("2026-11-18", ["ACME"]))
    make_session(tmp_path, "2026-11-19", edition("2026-11-19", ["ACME"]), directory=False)
    make_session(tmp_path, "2026-11-20", edition("2026-11-20", ["ACME"]), at="2026-11-20T14:31:00.000Z")
    make_session(tmp_path, "2026-11-23", edition("2026-11-23", ["ACME"]), listing=False)
    # Sealed after the next session's open (2026-11-25 09:30 New York): not counted.
    make_session(tmp_path, "2026-11-24", edition("2026-11-24", ["ACME"]), listing_at="2026-11-25T14:31:00Z")
    rows = r.statuses(tmp_path, "2026-11-18", "2026-11-25")
    assert [(s.session, s.reason) for s in rows] == [
        ("2026-11-18", "counted"),
        ("2026-11-19", "no_directory"),
        ("2026-11-20", "received_late"),
        ("2026-11-23", "no_listing"),
        ("2026-11-24", "listing_sealed_late"),
        ("2026-11-25", "no_listing"),
    ]
    report = r.status_report(tmp_path, "2026-11-18", "2026-11-25")
    assert report["counted"] == 1 and report["not_counted_by_reason"]["no_listing"] == 2
    assert report["early_end"] is False and report["early_end_after"] is None
    assert set(report) == {
        "start",
        "until",
        "sessions",
        "counted",
        "not_counted_by_reason",
        "last_countable",
        "early_end",
        "early_end_after",
    }


def _status(day: str, counted: bool) -> r.SessionStatus:
    return r.SessionStatus(day, counted, "counted" if counted else "no_listing")


def test_last_countable_read_date_and_early_end() -> None:
    days = [f"2027-01-{d:02d}" for d in range(1, 31)]
    assert r.last_countable([_status(d, True) for d in days]) is None
    many = [_status(f"2026-{i:03d}", True) for i in range(125)]
    assert r.last_countable(many) == "2026-119"
    assert r.last_countable([_status("2027-06-30", False)]) == "2027-06-30"
    # Sessions counted after the close date never make the 120th.
    late = [_status(f"2027-05-{i:03d}", True) for i in range(100)] + [
        _status(f"2027-07-{i:03d}", True) for i in range(30)
    ]
    assert r.last_countable(late) == "2027-06-30"
    gaps = [_status(f"d{i:02d}", i < 3 or i > 12) for i in range(20)]
    assert r.early_end(gaps) == (True, "d02")
    assert r.early_end([_status(f"d{i:02d}", i > 9) for i in range(20)]) == (True, None)  # misses from the start
    assert r.early_end([_status(f"d{i:02d}", i % 5 != 0) for i in range(30)]) == (False, None)
    calendar_sessions = [_status(d, True) for d in ("2027-06-28", "2027-06-29", "2027-06-30")]
    assert r.read_date(calendar_sessions) == "2027-07-07"  # 4 sessions after 30 June, skipping 5 July


def test_inputs_read_back_from_sealed_files(tmp_path: Path) -> None:
    data = edition("2026-11-18", ["ACME"])
    rows = make_session(tmp_path, "2026-11-18", data)
    loaded = load_receipts(tmp_path, "2026-11-18")
    assert loaded is not None
    receipts, raw = loaded
    assert raw == rows and receipts[0].run_id == "r1"
    assert listing_matches(rows, json.loads(json.dumps(rows)))
    assert not listing_matches(rows, [])
    assert scored_content(receipts[0], lambda _p: None, tmp_path, "2026-11-18") == json.loads(data)
    lost = x.Receipt("p.lost.json", receipts[0].received_at, "gone", None, None, True)
    assert scored_content(lost, lambda _p: None, tmp_path, "2026-11-18") is None
    other = x.Receipt("p.json", receipts[0].received_at, "r", "c" * 64, "f" * 64)
    assert scored_content(other, lambda _p: None, tmp_path, "2026-11-18") is None


def test_desk_names_over_time() -> None:
    assert parse_desk_names("version: 1\nnames: [SPY, QQQ, 'AAPL']  # twelve\n") == frozenset({"SPY", "QQQ", "AAPL"})
    with pytest.raises(InputError):
        parse_desk_names("names:\n  - SPY\n")
    calls: list[list[str]] = []

    def git(args: Any) -> str:
        calls.append(list(args))
        return "abc123\n" if args[0] == "rev-list" else "names: [SPY, NEWCO]\n"

    assert desk_names_at("2026-11-18", git) == frozenset({"SPY", "NEWCO"})
    assert calls[0] == ["rev-list", "-1", "--first-parent", "--before=2026-11-18T09:30:00-05:00", "origin/main"]


def _pages(pages: Mapping[str, list[dict[str, Any]]]) -> Any:
    def fetch(url: str, headers: Mapping[str, str]) -> bytes:
        assert headers.get("APCA-API-KEY-ID") == "k"
        kind = "bars" if "/v2/stocks/bars" in url else "actions"
        n = 1 if "page_token=t1" in url else 0
        return json.dumps(pages[kind][n]).encode()

    return fetch


def test_bars_and_spin_offs_read_every_page() -> None:
    pages: dict[str, list[dict[str, Any]]] = {
        "bars": [
            {"bars": {"ACME": [{"t": "2026-11-18T05:00:00Z", "o": 10, "c": 11}]}, "next_page_token": "t1"},
            {"bars": {"ACME": [{"t": "2026-11-19T05:00:00Z", "o": 11, "c": 12}]}, "next_page_token": None},
        ],
        "actions": [{"corporate_actions": {"spin_offs": [{"source_symbol": "ACME", "ex_date": "2026-11-19"}]}}],
    }
    log = Fetched([])
    bars = daily_bars(["ACME"], "2026-11-18", "2026-11-19", {"APCA-API-KEY-ID": "k"}, _pages(pages), log)
    assert bars == {"ACME": {"2026-11-18": (10.0, 11.0), "2026-11-19": (11.0, 12.0)}}
    assert spin_offs(["ACME"], "2026-11-18", "2026-11-19", {"APCA-API-KEY-ID": "k"}, _pages(pages), log) == {
        "ACME": ["2026-11-19"]
    }
    assert len(log.pages) == 3 and all(len(p["sha256"]) == 64 for p in log.pages)


def _bar(o: float, c: float, day: str) -> dict[str, Any]:
    return {"t": f"{day}T05:00:00Z", "o": o, "c": c}


def test_the_read_end_to_end_on_synthetic_sessions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    listings: dict[str, list[dict[str, Any]]] = {}
    for day in SESSIONS:
        listings[day] = make_session(tmp_path, day, edition(day, ["ACME", "NVDA", "QQQ", "ZZZZ", "NEWCO"], ["BRK.B"]))
    listings["2026-11-24"] = []  # the store no longer matches the sealed listing for one day
    days = SESSIONS + ["2026-11-25", "2026-11-27", "2026-11-30"]
    bars: dict[str, list[dict[str, Any]]] = {
        "ACME": [_bar(10, 10.2, d) for d in days],
        "NEWCO": [_bar(5, 6, d) for d in days],
        "BRK.B": [_bar(100, 101, d) for d in days],
        "RSP": [_bar(50, 50.5, d) for d in days],
        "IWM": [_bar(200, 200, d) for d in days],
    }
    pages: dict[str, list[dict[str, Any]]] = {
        "bars": [{"bars": bars, "next_page_token": None}],
        "actions": [{"corporate_actions": {}}],
    }

    def git(args: Any) -> str:
        desk = "names: [SPY, QQQ, AAPL, MSFT, NVDA, AMZN, GOOGL, META, TSLA, PLTR, AVGO, AMD, NEWCO]\n"
        return "abc\n" if args[0] == "rev-list" else desk

    fetches: list[str] = []
    real_fetch = _pages(pages)

    def counting_fetch(url: str, headers: Mapping[str, str]) -> bytes:
        fetches.append(url)
        data: bytes = real_fetch(url, headers)
        return data

    inputs = r.ReadInputs(
        store_listing=lambda day: listings[day],
        read_store_copy=lambda _p: None,
        git=git,
        fetch=counting_fetch,
        alpaca_headers={"APCA-API-KEY-ID": "k"},
    )
    with pytest.raises(r.ReadRefused):
        r.run_read(tmp_path, SESSIONS[0], "2026-11-24", inputs)
    monkeypatch.setattr(r, "last_countable", lambda sessions, close_date="2027-06-30": "2026-11-24")
    report = r.run_read(tmp_path, SESSIONS[0], "2026-12-02", inputs, {"frozen_commit": "f" * 40})
    assert report["counted"] == 5 and report["analysed"] == 4 and report["frozen_commit"] == "f" * 40
    assert report["not_counted_or_not_analysed"] == {"listing_mismatch": 1}
    assert {k: v["verdict"] for k, v in report["trials"].items()} == dict.fromkeys(report["trials"], "inconclusive")
    assert all("mean" not in v for v in report["trials"].values())  # under 60 analysed: no statistic
    assert fetches == [] and "fetched_pages" not in report  # and no bar fetched
    assert json.loads(r.report_path(tmp_path).read_text()) == report
    assert set(report["sealed_inputs"]) == {
        f"data/{kind}/{day}" for kind in ("radar", "radar-receipts") for day in SESSIONS
    }
    with pytest.raises(r.ReadRefused):  # written once
        r.run_read(tmp_path, SESSIONS[0], "2026-12-02", inputs)

    # With the minimums lowered for the test only, the numbers come out as the record defines them.
    monkeypatch.setattr(x, "MIN_ANALYSED", 1)
    monkeypatch.setattr(x, "MIN_INCLUDED", 1)
    r.report_path(tmp_path).unlink()
    report = r.run_read(tmp_path, SESSIONS[0], "2026-12-02", inputs)
    assert report["names_taken_out_by_list"] == {
        "DAILY RADAR": {"desk_name": 8, "unknown_symbol": 4, "desk_added": 4},  # once per list and session
        "SMALL CAP MOMENTUM": {},
    }
    assert report["desk_config_commits"] == dict.fromkeys(SESSIONS[:4], "abc")
    assert len(report["fetched_pages"]) == 2 and len(fetches) == 2
    saved = sorted(f.name for f in (tmp_path / "exp0038-read" / "inputs").iterdir())
    assert len(saved) == 7 and saved[-5:] == [f"store-listing-{d}.json" for d in SESSIONS]  # 2 pages, 5 listings
    assert set(report["store_listings_sha256"]) == set(SESSIONS)
    assert report["trials"]["HYP-0065"]["included"] == 4
    assert report["trials"]["HYP-0065"]["scored_names_per_session"] == {"1": 4}
    assert report["trials"]["HYP-0065"]["name_weighted_check"] == pytest.approx(0.01)
    assert report["trials"]["HYP-0065"]["largest_name_share_mean"] == pytest.approx(1.0)
    # A re-run reads the saved pages and fetches nothing anew.
    r.report_path(tmp_path).unlink()
    again = r.run_read(tmp_path, SESSIONS[0], "2026-12-02", inputs)
    assert len(fetches) == 2 and again["trials"] == report["trials"]
    assert report["trials"]["HYP-0065"]["mean"] == pytest.approx(0.02 - 0.01)  # ACME +2% minus RSP +1%
    assert report["trials"]["HYP-0067"]["mean"] == pytest.approx(0.01 - 0.0)  # BRK.B +1% minus IWM 0%
    assert report["trials"]["HYP-0066"]["mean"] == pytest.approx(0.02 - 0.01)  # five sessions: o(S) to c(S+4)
    assert report["skill_hash_changes"] == [["2026-11-18", "s" * 64]]


def test_review_edges(tmp_path: Path) -> None:
    data = edition("2026-11-18", ["ACME"])
    rows = make_session(tmp_path, "2026-11-18", data)
    # The same instant in another format is the same receipt.
    reformatted = [dict(rows[0], received_at=rows[0]["received_at"].replace("Z", "+00:00"))]
    assert listing_matches(rows, reformatted)
    # Status leaves out a session whose listing may still be sealed, rather than showing it as a miss.
    from datetime import UTC, datetime

    report = r.status_report(tmp_path, "2026-11-18", "2026-11-19", now=datetime(2026, 11, 19, 20, 0, tzinfo=UTC))
    assert report["sessions"] == 1 and report["counted"] == 1
    # An unreadable directory is no directory, not a crash.
    bad = {"edition.json": data, "nasdaqlisted.txt": b"garbage\n", "otherlisted.txt": b"garbage\n"}
    seal(tmp_path, "radar", "2026-11-19", bad, "2026-11-19T11:13:00Z", {})
    assert [s.reason for s in r.statuses(tmp_path, "2026-11-19", "2026-11-19")] == ["no_listing"]
    from options_funnel.exp0038_inputs import directory_for

    assert directory_for(tmp_path, "2026-11-19") is None
