import csv
import json
from pathlib import Path

import pytest

from options_funnel.blind import assert_blind
from options_funnel.candidates import Verdict, dump_jsonl, load_jsonl
from options_funnel.focus_pick import ladder, option_volume_ratio, pick

NIGHTS = Path(__file__).resolve().parent / "fixtures" / "focus_nights"
SESSIONS = sorted(p.stem for p in (NIGHTS / "levels").glob("*.json"))


def iv_rows() -> list[dict[str, str]]:
    with open(NIGHTS / "iv_history.csv", newline="") as fh:
        return list(csv.DictReader(fh))


def test_twelve_nights_are_present() -> None:
    # focus.py was last changed on 2026-10-27, so parity is held from the next session on.
    assert SESSIONS[0] == "2026-10-28" and len(SESSIONS) == 12


@pytest.mark.parametrize("session", SESSIONS)
def test_parity_with_the_published_focus_file(session: str) -> None:
    levels = json.loads((NIGHTS / "levels" / f"{session}.json").read_text())
    published = json.loads((NIGHTS / "focus" / f"{session}.json").read_text())
    result = pick(levels, iv_rows())
    for key in ("session", "built_from", "method"):
        assert result.document[key] == published[key]
    assert result.document["items"] == published["items"]


@pytest.mark.parametrize("session", SESSIONS)
def test_every_name_gets_one_record(session: str) -> None:
    levels = json.loads((NIGHTS / "levels" / f"{session}.json").read_text())
    result = pick(levels, iv_rows())
    records = result.candidates
    assert [r.symbol for r in records] == list(levels["tickers"])
    picked = [r for r in records if r.verdict is Verdict.PICKED]
    assert sorted(r.symbol for r in picked) == sorted(i["symbol"] for i in result.document["items"])
    assert sorted(r.rank for r in picked if r.rank is not None) == list(range(1, len(picked) + 1))
    for r in records:
        assert r.session == session
        if r.verdict is Verdict.REJECTED:
            assert r.reason == "below_top_four" and r.score is not None and r.rank is None
    assert list(load_jsonl(dump_jsonl(records))) == list(records)
    assert_blind(result.document)


def test_inputs_hash_changes_with_the_inputs() -> None:
    levels = json.loads((NIGHTS / "levels" / f"{SESSIONS[-1]}.json").read_text())
    before = {r.symbol: r.inputs_sha256 for r in pick(levels, iv_rows()).candidates}
    sym = next(iter(levels["tickers"]))
    levels["tickers"][sym]["atr14"] += 0.01
    after = {r.symbol: r.inputs_sha256 for r in pick(levels, iv_rows()).candidates}
    assert before[sym] != after[sym]
    assert all(before[s] == after[s] for s in before if s != sym)


def test_spy_takes_a_slot_even_with_the_lowest_score() -> None:
    def ticker(close: float, levels: list[dict[str, object]]) -> dict[str, object]:
        return {"last": {"close": close}, "atr14": 1.0, "levels": levels, "close_strength": {"chip": "MID"}}

    far = [{"name": "PDH", "price": 200.0}]
    near = [{"name": "PDH", "price": 100.5}]
    doc = {
        "session": "2026-11-18",
        "built_from": "2026-11-17",
        "tickers": {
            "SPY": ticker(100.0, far),
            "AAA": ticker(100.0, near),
            "BBB": ticker(100.0, near),
            "CCC": ticker(100.0, near),
            "DDD": ticker(100.0, near),
            "ZERO": {"last": {"close": 10.0}, "atr14": 0, "levels": []},
        },
    }
    result = pick(doc, [])
    assert [i["symbol"] for i in result.document["items"]] == ["AAA", "BBB", "CCC", "SPY"]
    verdicts = {r.symbol: (r.verdict, r.reason) for r in result.candidates}
    assert verdicts["SPY"] == (Verdict.PICKED, "spy_always")
    assert verdicts["DDD"] == (Verdict.REJECTED, "below_top_four")
    assert verdicts["ZERO"] == (Verdict.NOT_SCORED, "no_atr")


def test_ladder_merges_and_uses_zone_edges() -> None:
    t = {
        "atr14": 10.0,
        "levels": [
            {"name": "PDH", "price": 101.0},
            {"name": "PWH", "price": 102.0},
            {"name": "PDC", "price": 99.0},
            {"name": "DEMAND", "price": 90.0, "lo": 88.0, "hi": 92.0},
            {"name": "SUPPLY", "price": 120.0, "lo": 118.0, "hi": 122.0},
        ],
    }
    assert ladder(t) == [(92.0, "DEMAND"), (101.0, "PDH/PWH"), (118.0, "SUPPLY")]


def test_option_volume_ratio() -> None:
    row = {"call_vol": "10", "put_vol": "10", "avg_call_vol": "5", "avg_put_vol": "5"}
    assert option_volume_ratio(row) == 2.0
    assert option_volume_ratio(None) is None
    assert option_volume_ratio({**row, "avg_call_vol": "0", "avg_put_vol": "0"}) is None
    assert option_volume_ratio({**row, "call_vol": ""}) is None
