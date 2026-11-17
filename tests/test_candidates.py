import json

import pytest

from options_funnel.candidates import (
    CandidateRecord,
    Stage,
    Verdict,
    canonical,
    dump_jsonl,
    inputs_sha256,
    load_jsonl,
)

SHA = inputs_sha256({"close": 1.5, "levels": [1, 2]})
CTX = inputs_sha256({"all": True})


def record(**changes: object) -> CandidateRecord:
    fields: dict[str, object] = {
        "session": "2026-11-17",
        "stage": Stage.FOCUS_PICK,
        "symbol": "NVDA",
        "verdict": Verdict.PICKED,
        "reason": "top_score",
        "inputs_sha256": SHA,
        "context_sha256": CTX,
        "score": 3.25,
        "rank": 1,
    }
    fields.update(changes)
    return CandidateRecord(**fields)  # type: ignore[arg-type]


def row(**changes: object) -> str:
    base = record().to_row()
    base.update(changes)
    return json.dumps(base)


def test_round_trip() -> None:
    rows = [record(), record(symbol="AMD", verdict=Verdict.REJECTED, reason="below_top_four", rank=None, score=1)]
    assert list(load_jsonl(dump_jsonl(rows))) == rows


def test_hash_is_canonical() -> None:
    assert inputs_sha256({"b": 1, "a": [1.0]}) == inputs_sha256({"a": [1], "b": 1.0})
    assert inputs_sha256({"x": -0.0}) == inputs_sha256({"x": 0})
    assert inputs_sha256({"x": "é"}) == inputs_sha256({"x": "é"})
    assert inputs_sha256((1, 2)) == inputs_sha256([1, 2])
    assert canonical({"x": 2.5}) == {"x": 2.5}


@pytest.mark.parametrize(
    "bad", [{"x": float("nan")}, {1: "a"}, {"x": {1, 2}}, {"x": object()}, {"e\u0301": 1, "\u00e9": 2}]
)
def test_hash_refuses_non_json(bad: object) -> None:
    with pytest.raises(ValueError):
        inputs_sha256(bad)


@pytest.mark.parametrize(
    "changes",
    [
        {"session": "2026-13-01"},
        {"session": "20261117"},
        {"session": "2026-W46-1"},
        {"session": "2026-11-17\n"},
        {"symbol": "nvda"},
        {"symbol": "AAPL\n"},
        {"symbol": 5},
        {"reason": "Top Score"},
        {"reason": "top_score\n"},
        {"inputs_sha256": "abc"},
        {"inputs_sha256": SHA + "\n"},
        {"score": float("inf")},
        {"score": True},
        {"score": "1"},
        {"score": 10**400},
        {"reason": "hit_t1_win"},
        {"reason": "stopped_out"},
        {"context_sha256": "x"},
        {"flags": ["iv_row_missing"]},
        {"flags": ("b_flag", "a_flag")},
        {"flags": ("a_flag", "a_flag")},
        {"flags": ("Bad Flag",)},
        {"flags": ("t1_hit",)},
        {"rank": 0},
        {"rank": 1.0},
        {"rank": True},
        {"stage": "focus_pick"},
        {"verdict": "picked"},
    ],
)
def test_bad_fields_are_refused(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        record(**changes)


def test_duplicate_symbol_is_refused_on_write_and_read() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        dump_jsonl([record(), record()])
    line = record().to_json()
    with pytest.raises(ValueError, match="line 2: duplicate"):
        list(load_jsonl(line + "\n" + line + "\n"))


@pytest.mark.parametrize(
    "text",
    [
        row(pnl_usd=1),
        row(extra=1),
        row(schema_version=True),
        row(schema_version=1.0),
        row(schema_version=2),
        row(schema="other"),
        row(stage="nope"),
        row(stage=1),
        row(flags="iv_row_missing"),
        row(flags=["z_flag", "a_flag"]),
        json.dumps({k: v for k, v in record().to_row().items() if k != "rank"}),
        '{"symbol":"NVDA",' + row()[1:],
        "[1]",
    ],
)
def test_loading_is_strict(text: str) -> None:
    with pytest.raises(ValueError, match="line 1"):
        list(load_jsonl(text))


def test_score_has_one_spelling() -> None:
    assert record(score=2.0).score == 2 and isinstance(record(score=2.0).score, int)
    assert record(score=2.0).to_json() == record(score=2).to_json()


def test_huge_score_in_a_file_reports_its_line() -> None:
    with pytest.raises(ValueError, match="line 1"):
        list(load_jsonl(row(score=10**400)))


def test_flags_round_trip() -> None:
    r = record(flags=("iv_row_missing", "score_tie"))
    assert list(load_jsonl(dump_jsonl([r]))) == [r]
