import json
from pathlib import Path

import pytest

from options_funnel.candidates import load_jsonl
from options_funnel.cli import EXIT_EXISTS, EXIT_INPUT, main

NIGHTS = Path(__file__).resolve().parent / "fixtures" / "focus_nights"
LEVELS = NIGHTS / "levels" / "2026-11-12.json"
IV = NIGHTS / "iv_history.csv"


def run(*args: object) -> int:
    return main(["focus-pick", *map(str, args)])


def test_writes_records_and_document(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out, focus = tmp_path / "c.jsonl", tmp_path / "f.json"
    assert run(LEVELS, IV, "--candidates", out, "--focus", focus) == 0
    assert len(list(load_jsonl(out.read_text()))) == 12
    published = json.loads((NIGHTS / "focus" / "2026-11-12.json").read_text())
    assert json.loads(focus.read_text())["items"] == published["items"]
    assert "#1 MSFT" in capsys.readouterr().out
    assert sorted(p.name for p in tmp_path.iterdir()) == ["c.jsonl", "f.json"]


def test_never_overwrites(tmp_path: Path) -> None:
    out = tmp_path / "c.jsonl"
    out.write_text("keep")
    assert run(LEVELS, IV, "--candidates", out) == EXIT_EXISTS
    assert out.read_text() == "keep"


def test_same_path_twice_is_refused_before_writing(tmp_path: Path) -> None:
    out = tmp_path / "c.jsonl"
    assert run(LEVELS, IV, "--candidates", out, "--focus", out) == EXIT_INPUT
    assert not out.exists()


def test_byte_order_mark_does_not_change_the_picks(tmp_path: Path) -> None:
    bom = tmp_path / "iv.csv"
    bom.write_bytes(b"\xef\xbb\xbf" + IV.read_bytes())
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    assert run(LEVELS, IV, "--candidates", a) == 0
    assert run(LEVELS, bom, "--candidates", b) == 0
    assert a.read_text() == b.read_text()


def test_header_without_needed_columns_is_refused(tmp_path: Path) -> None:
    bad = tmp_path / "iv.csv"
    bad.write_text("ticker,call_vol\nSPY,1\n")
    assert run(LEVELS, bad, "--candidates", tmp_path / "c.jsonl") == EXIT_INPUT
    assert not (tmp_path / "c.jsonl").exists()


def test_blind_failure_writes_nothing(tmp_path: Path) -> None:
    levels = json.loads(LEVELS.read_text())
    sym = next(iter(levels["tickers"]))
    for lv in levels["tickers"][sym]["levels"]:
        lv["name"] = "[Q"
    path = tmp_path / "levels.json"
    path.write_text(json.dumps(levels))
    out, focus = tmp_path / "c.jsonl", tmp_path / "f.json"
    assert run(path, IV, "--candidates", out, "--focus", focus) == EXIT_INPUT
    assert not out.exists() and not focus.exists()


def test_missing_input_is_an_input_error(tmp_path: Path) -> None:
    assert run(tmp_path / "nope.json", IV, "--candidates", tmp_path / "c.jsonl") == EXIT_INPUT
