"""The Radar desk file (Phase 1): synthetic inputs only (no real edition or focus night is copied here)."""

import json
from pathlib import Path
from typing import Any

import pytest

from options_funnel import desk
from options_funnel.blind import assert_blind
from options_funnel.cli import EXIT_INPUT, EXIT_MISMATCH, EXIT_SEALED_DIFFERENT
from options_funnel.seal import KINDS, OPTIONAL, seal, verify

S, NEXT = "2026-11-17", "2026-11-18"  # a Tuesday and the Wednesday after it
NAMES = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "PLTR", "AVGO", "AMD"]
DESK_YAML = ("# desk\nnames: [" + ", ".join(NAMES) + "]\n").encode()
EARNINGS = b"""# kept by hand
symbol,date,when,source
AAPL,2026-11-17,bmo,test
NVDA,2026-11-17,amc,test
AMD,2026-11-18,bmo,test
AVGO,2026-12-09,amc,test
"""
MACRO = b"""date,time_et,type,severity,policy,source,quality
2026-11-17,08:30,cpi,high,reduce,test,exact
2026-11-17,14:00,fomc_minutes,medium,blackout_window,test,derived
2026-11-18,08:30,retail_sales,high,reduce,test,exact
2026-11-19,08:30,nfp,high,reduce,test,exact
"""


def edition(**change: object) -> dict[str, object]:
    doc: dict[str, object] = {
        "schema": "stocksdelta/radar",
        "edition_date": S,
        "lists": [{"name": "MEGA CAP OPTIONS", "symbols": ["NVDA", "MSFT"]}, {"name": "NEW LIST", "symbols": ["AMD"]}],
        "calendar": [
            {"date": S, "label": "NVDA", "kind": "earnings"},
            {"date": "2026-11-19", "label": "MSFT (after close)", "kind": "earnings"},
            {"date": S, "time_et": "08:30", "label": "CPI", "kind": "macro", "impact": "high"},
        ],
        "tickers": [
            {"symbol": s, "support": 100.0, "resistance": 110.0, "evidence_grade": 3, "chart_grade": 2, "tag": None}
            for s in NAMES
            if s not in ("PLTR",)
        ],
    }
    tickers = doc["tickers"]
    assert isinstance(tickers, list)
    for row in tickers:
        if row["symbol"] == "TSLA":
            row["tag"] = "risk"
        if row["symbol"] == "NVDA":
            row["support"], row["lists"] = 148.8, ["DAILY RADAR"]
    doc.update(change)
    return doc


def focus(symbols: tuple[str, ...] = ("NVDA", "MSFT", "AMD", "SPY")) -> dict[str, object]:
    levels = {"NVDA": (155.02, 160.0, 5.0), "MSFT": (100.4, 110.4, 4.0), "AMD": (101.0, 109.0, 6.0)}
    items = []
    for rank, s in enumerate(symbols, 1):
        support, resistance, atr = levels.get(s, (100.0, 110.0, 2.0))
        items.append(
            {"symbol": s, "support": {"price": support}, "resistance": {"price": resistance}, "atr": atr, "rank": rank}
        )
    return {"session": S, "built_from": "2026-11-16", "items": items}


def data_root(tmp_path: Path, with_focus: bool = True, ed: dict[str, object] | None = None) -> Path:
    root = tmp_path / "data-branch"
    seal(root, "radar", S, {"edition.json": json.dumps(ed or edition()).encode()}, "2026-11-17T11:20:00+00:00", {})
    if with_focus:
        files = {"levels.json": b"{}", "iv_rows.csv": b"session\n", "focus.json": json.dumps(focus()).encode()}
        seal(root, "focus", S, files, "2026-11-16T22:00:00+00:00", {})
    return root


def inputs(tmp_path: Path) -> list[str]:
    paths = {"earnings": EARNINGS, "macro": MACRO, "desk": DESK_YAML}
    for name, data in paths.items():
        (tmp_path / name).write_bytes(data)
    return [
        "--earnings",
        str(tmp_path / "earnings"),
        "--macro",
        str(tmp_path / "macro"),
        "--desk-config",
        str(tmp_path / "desk"),
    ]


def run_seal(root: Path, tmp_path: Path, *extra: str) -> int:
    return desk.main(["seal", "--root", str(root), "--session", S, *inputs(tmp_path), "--routine", "trig_test", *extra])


def built(root: Path) -> dict[str, Any]:
    raw = desk.from_files(root, S, EARNINGS, MACRO, DESK_YAML, "a" * 40)
    doc = json.loads(raw)
    assert isinstance(doc, dict)
    return doc


def by_name(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    names = doc["names"]
    assert isinstance(names, list)
    return {row["symbol"]: row for row in names}


def test_flags_and_fields(tmp_path: Path) -> None:
    doc = built(data_root(tmp_path))
    rows = by_name(doc)
    assert sorted(rows) == sorted(NAMES)
    assert rows["NVDA"]["flags"] == ["EARNINGS_IN_HOLD", "MACRO_HIGH_IN_HOLD", "LEVELS_DISAGREE"]
    assert rows["AAPL"]["flags"] == ["EARNINGS_BEFORE_OPEN", "EARNINGS_SOURCES_DISAGREE", "MACRO_HIGH_IN_HOLD"]
    assert "EARNINGS_IN_HOLD" in rows["AMD"]["flags"]  # reports before the next open
    # The Radar has MSFT on the 19th, earnings.csv has nothing: flagged, not taken as fact.
    assert rows["MSFT"]["earnings"] == {
        "next": None,
        "csv_dates": [],
        "radar_dates": ["2026-11-19"],
        "sources_agree": False,
    }
    assert "EARNINGS_SOURCES_DISAGREE" in rows["MSFT"]["flags"]
    # AMD reports on the 18th by earnings.csv; the Radar's calendar does not have it.
    assert rows["AMD"]["earnings"]["sources_agree"] is False
    assert rows["NVDA"]["earnings"]["sources_agree"] is True
    assert "RADAR_RISK_TAG" in rows["TSLA"]["flags"]
    assert rows["PLTR"]["radar"] is None and "RADAR_NOT_COVERED" in rows["PLTR"]["flags"]
    assert rows["AVGO"]["earnings"]["next"] == {"date": "2026-12-09", "when": "amc"}
    assert rows["NVDA"]["radar"]["lists"] == ["daily_radar", "mega_cap_options"]
    assert rows["AMD"]["radar"]["lists"] == ["other"]
    assert rows["NVDA"]["focus"] is True and rows["AAPL"]["focus"] is False
    assert rows["NVDA"]["level_gap_atr"] == {"support": -1.24, "resistance": -10.0}
    assert rows["MSFT"]["level_gap_atr"] == {"support": -0.1, "resistance": -0.1}
    assert "LEVELS_DISAGREE" not in rows["MSFT"]["flags"]
    assert rows["AAPL"]["desk_levels"] is None and rows["AAPL"]["level_gap_atr"] is None
    # 08:30 on the session is before the open (not held); 08:30 the next morning is; the day after is not.
    assert doc["macro_in_hold"] == [
        {"date": S, "time_et": "14:00", "type": "fomc_minutes", "severity": "medium"},
        {"date": NEXT, "time_et": "08:30", "type": "retail_sales", "severity": "high"},
    ]
    assert doc["coverage"] == {
        "desk_names": 12,
        "radar_covered": 11,
        "focus_names": 4,
        "radar_calendar_has_earnings": True,
    }
    assert_blind(doc)


def test_no_text_or_prices_after_the_edition(tmp_path: Path) -> None:
    raw = desk.from_files(data_root(tmp_path), S, EARNINGS, MACRO, DESK_YAML, None).decode()
    for word in ("MEGA CAP", "after close", "CPI", "test", "kept by hand", "built_from", "rank", "score"):
        assert word not in raw


def test_no_focus_file_and_no_radar_calendar(tmp_path: Path) -> None:
    ed = edition(calendar=[])
    doc = built(data_root(tmp_path, with_focus=False, ed=ed))
    rows = by_name(doc)
    assert all(row["focus"] is None and row["desk_levels"] is None for row in rows.values())
    assert rows["NVDA"]["earnings"]["sources_agree"] is None
    assert "EARNINGS_SOURCES_DISAGREE" not in rows["NVDA"]["flags"]
    assert doc["coverage"]["focus_names"] is None


def test_seal_check_and_verify(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = data_root(tmp_path)
    assert run_seal(root, tmp_path, "--wt-commit", "b" * 40) == 0
    folder = root / "data" / desk.KIND / S
    assert sorted(p.name for p in folder.iterdir()) == sorted([*desk.FILES, "MANIFEST.json"])
    assert verify(folder) == []
    assert (folder / desk.EARNINGS).read_bytes() == EARNINGS
    manifest = json.loads((folder / "MANIFEST.json").read_text())
    assert manifest["provenance"]["wt_commit"] == "b" * 40
    assert desk.main(["check", "--root", str(root), "--session", S]) == 0
    assert desk.main(["verify", str(folder)]) == 0
    # The same inputs again: nothing new. Different inputs: refused without --correction, then a correction.
    assert run_seal(root, tmp_path, "--wt-commit", "b" * 40) == 0
    assert "already sealed, identical" in capsys.readouterr().out
    assert run_seal(root, tmp_path, "--wt-commit", "c" * 40) == EXIT_SEALED_DIFFERENT
    assert run_seal(root, tmp_path, "--wt-commit", "c" * 40, "--correction") == 0
    assert (root / "data" / desk.KIND / f"{S}.r2").is_dir()
    assert desk.main(["check", "--root", str(root), "--session", S]) == 0


def test_check_keeps_the_inputs_it_was_sealed_with(tmp_path: Path) -> None:
    root = data_root(tmp_path, with_focus=False)
    assert run_seal(root, tmp_path) == 0
    # A focus file sealed later does not change what the sealed desk file rebuilds from.
    files = {"levels.json": b"{}", "iv_rows.csv": b"session\n", "focus.json": json.dumps(focus()).encode()}
    seal(root, "focus", S, files, "2026-11-17T12:00:00+00:00", {})
    assert desk.main(["check", "--root", str(root), "--session", S]) == 0


def test_check_finds_a_changed_file(tmp_path: Path) -> None:
    root = data_root(tmp_path)
    assert run_seal(root, tmp_path) == 0
    sealed = root / "data" / desk.KIND / S / desk.DESK
    sealed.chmod(0o644)
    sealed.write_bytes(sealed.read_bytes().replace(b'"risk_tag": true', b'"risk_tag": false'))
    assert desk.main(["check", "--root", str(root), "--session", S]) == EXIT_MISMATCH


def test_refusals(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert run_seal(empty, tmp_path) == EXIT_INPUT  # no sealed edition
    root = data_root(tmp_path, ed=edition(edition_date="2026-11-16"))
    assert run_seal(root, tmp_path) == EXIT_INPUT  # the edition is for another day
    with pytest.raises(desk.DeskError):
        desk.read_earnings(b"symbol,date,when\nNVDA,2026-11-17,later\n")
    with pytest.raises(desk.DeskError):
        desk.read_macro(b"date,time_et,type,severity\n2026-11-17,8:30,cpi,high\n")
    weekend = tmp_path / "w"
    weekend.mkdir()
    seal(weekend, "radar", "2026-11-21", {"edition.json": b"{}"}, "2026-11-21T11:00:00+00:00", {})
    args = ["seal", "--root", str(weekend), "--session", "2026-11-21", *inputs(tmp_path)]
    assert desk.main(args) == EXIT_INPUT


def test_check_refuses_free_text_and_extra_keys(tmp_path: Path) -> None:
    doc = built(data_root(tmp_path))
    desk.check(doc)
    rows = doc["names"]
    assert isinstance(rows, list)
    rows[0]["note"] = "anything"
    with pytest.raises(desk.DeskError):
        desk.check(doc)
    del rows[0]["note"]
    rows[0]["flags"] = ["SOMETHING_ELSE"]
    with pytest.raises(desk.DeskError):
        desk.check(doc)


def test_existing_seal_kinds_unchanged() -> None:
    """Registering the desk kind leaves the frozen kinds exactly as EXP-0038's analysis commit has them."""
    assert KINDS["radar"] == frozenset({"edition.json"})
    assert KINDS["focus"] == frozenset({"levels.json", "iv_rows.csv", "focus.json"})
    assert KINDS["radar-receipts"] == frozenset({"listing.json"})
    assert OPTIONAL == {"radar": frozenset({"nasdaqlisted.txt", "otherlisted.txt"})}
    assert KINDS[desk.KIND] == desk.FILES


def test_malformed_edition_fields_are_ignored_not_crashed(tmp_path: Path) -> None:
    bad = edition(tickers=5, lists="DAILY RADAR", calendar={"kind": "earnings"})
    doc = built(data_root(tmp_path, ed=bad))
    rows = by_name(doc)
    assert all(row["radar"] is None for row in rows.values())
    assert doc["coverage"]["radar_calendar_has_earnings"] is False
    nan = edition()
    nan["tickers"] = [{"symbol": "NVDA", "support": float("nan"), "resistance": float("inf"), "lists": "DAILY RADAR"}]
    root = tmp_path / "nan"
    root.mkdir()
    rows = by_name(built(data_root(root, ed=nan)))
    assert rows["NVDA"]["radar"]["support"] is None and rows["NVDA"]["radar"]["resistance"] is None
    assert rows["NVDA"]["radar"]["lists"] == ["mega_cap_options"]  # the string `lists` adds nothing


def test_calendar_dates_and_labels(tmp_path: Path) -> None:
    ed = edition(
        calendar=[
            {"date": "2026-11-18 per IR call notes", "label": "NVDA", "kind": "earnings"},
            {"date": S, "label": "NVDA.", "kind": "earnings"},
            {"date": "2026-11-19", "label": "Alphabet (GOOG)", "kind": "earnings"},
            {"date": "2026-11-20", "label": "Meta and Amd", "kind": "earnings"},
        ]
    )
    raw = desk.from_files(data_root(tmp_path, ed=ed), S, EARNINGS, MACRO, DESK_YAML, None)
    assert b"IR call" not in raw
    rows = by_name(json.loads(raw))
    assert rows["NVDA"]["earnings"]["radar_dates"] == [S]
    assert rows["GOOGL"]["earnings"]["radar_dates"] == ["2026-11-19"]
    assert rows["META"]["earnings"]["radar_dates"] == [] and rows["AMD"]["earnings"]["radar_dates"] == []


def test_window_edges(tmp_path: Path) -> None:
    earnings = b"symbol,date,when\nAAPL,2026-11-18,during\nMSFT,2026-11-18,amc\n"
    macro = (
        b"date,time_et,type,severity\n"
        b"2026-11-17,09:29,early,high\n2026-11-17,09:30,at_open,medium\n"
        b"2026-11-18,09:29,next_early,medium\n2026-11-18,09:30,next_open,high\n"
    )
    doc = json.loads(desk.from_files(data_root(tmp_path), S, earnings, macro, DESK_YAML, None))
    assert [m["type"] for m in doc["macro_in_hold"]] == ["at_open", "next_early"]
    rows = by_name(doc)
    assert "MACRO_HIGH_IN_HOLD" not in rows["SPY"]["flags"]
    assert "EARNINGS_IN_HOLD" not in rows["AAPL"]["flags"] and "EARNINGS_IN_HOLD" not in rows["MSFT"]["flags"]


def test_holiday_between_sessions(tmp_path: Path) -> None:
    """2026-11-25 is followed by Thanksgiving: anything dated on the 26th falls between the two opens."""
    day = "2026-11-25"
    root = tmp_path / "data-branch"
    seal(root, "radar", day, {"edition.json": json.dumps(edition(edition_date=day)).encode()}, "x", {})
    earnings = b"symbol,date,when\nNVDA,2026-11-26,amc\n"
    macro = b"date,time_et,type,severity\n2026-11-26,10:00,holiday_release,high\n"
    doc = json.loads(desk.from_files(root, day, earnings, macro, DESK_YAML, None))
    assert doc["next_session"] == "2026-11-27"
    rows = by_name(doc)
    assert "EARNINGS_IN_HOLD" in rows["NVDA"]["flags"] and "MACRO_HIGH_IN_HOLD" in rows["SPY"]["flags"]


def test_inputs_refused(tmp_path: Path) -> None:
    root = data_root(tmp_path, ed=edition(edition_date=None))
    with pytest.raises(desk.DeskError):
        desk.from_files(root, S, EARNINGS, MACRO, DESK_YAML, None)  # an edition must name its date
    for bad in (
        b"date,time_et,type,severity\n11/17/2026,08:30,cpi,high\n",
        b"date,time_et,type,severity\n2026-11-17,99:99,cpi,high\n",
        b"date,time_et,type,severity\n2026-11-17,08:30,cpi,High\n",
    ):
        with pytest.raises(desk.DeskError):
            desk.read_macro(bad)
    with pytest.raises(desk.DeskError):
        desk.read_earnings(b"\xff\xfe")
    assert desk.main(["seal", "--root", str(root), "--session", S, *inputs(tmp_path)]) == EXIT_INPUT


def test_a_broken_latest_correction_is_refused(tmp_path: Path) -> None:
    root = data_root(tmp_path)
    fixed = json.dumps(edition(lists=[])).encode()
    seal(root, "radar", S, {"edition.json": fixed}, "2026-11-17T11:30:00+00:00", {}, correction=True)
    (root / "data" / "radar" / f"{S}.r2" / "edition.json").write_bytes(b"{}")
    assert run_seal(root, tmp_path) == EXIT_INPUT  # never falls back to the superseded edition


def test_check_takes_only_the_session_folders(tmp_path: Path) -> None:
    root = data_root(tmp_path)
    assert run_seal(root, tmp_path) == 0
    (root / "data" / desk.KIND / f"{S}.bak").mkdir()
    assert desk.main(["check", "--root", str(root), "--session", S]) == 0
    assert desk.main(["check", "--root", str(root), "--session", "2026-11-1"]) == EXIT_INPUT
