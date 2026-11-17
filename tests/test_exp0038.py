"""EXP-0038's analysis core on synthetic data only: no real session, bar or edition is used here."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from options_funnel import exp0038 as x

HERE = Path(__file__).resolve().parent
DIRECTORY = HERE / "fixtures" / "directory"


@pytest.mark.parametrize(
    ("seed", "first_three"),
    [
        (20261147, [3209735247, 3386447078, 1777139272]),  # EXP-0032's vector: the same step
        (20261155, [2187981034, 2314792444, 2933325786]),
        (20261156, [1183055867, 1588269641, 1324635511]),
        (20261157, [4272129800, 3721915241, 504612019]),
    ],
)
def test_mulberry32_matches_the_registered_vectors(seed: int, first_three: list[int]) -> None:
    rng = x.Mulberry32(seed)
    assert [rng.next() for _ in range(3)] == first_three


def test_the_registered_seeds_and_constants() -> None:
    assert x.SEEDS == {"HYP-0065": 20261155, "HYP-0066": 20261156, "HYP-0067": 20261157}
    assert (x.RESAMPLES, x.BLOCK, x.MIN_ANALYSED, x.MIN_INCLUDED, x.LEVEL) == (5000, 5, 60, 40, 0.025)
    assert x.BENCHMARKS == {"HYP-0065": "RSP", "HYP-0066": "RSP", "HYP-0067": "IWM"}
    assert len(x.DESK_NAMES) == 12 and x.DESK_PROXIES == {"GOOG"}


@pytest.mark.parametrize(
    ("raw", "want"),
    [(" brk/b ", "BRK.B"), ("nvda", "NVDA"), ("", None), ("1ABC", None), ("TOOLONGSYMBOL", None), (5, None)],
)
def test_symbols_are_normalised(raw: object, want: str | None) -> None:
    assert x.normalise_symbol(raw) == want


def directory() -> x.Directory:
    return x.Directory.parse((DIRECTORY / "nasdaqlisted.txt").read_text(), (DIRECTORY / "otherlisted.txt").read_text())


def test_the_directory_reads_both_files_and_skips_test_issues() -> None:
    d = directory()
    assert d.etf == {"AAPL": False, "QQQ": True, "BRK.B": False, "RSP": True}


def test_scored_names_takes_out_every_frozen_class_with_its_reason() -> None:
    d = x.Directory({**directory().etf, "GOOG": False, "ACME": False, "NEWDESK": False, "TQQQ": True})
    kept, out = x.scored_names(
        ["ACME", "acme", "NVDA", "GOOG", "TQQQ", "ZZZZ", "NEWDESK", "BRK/B", "??"], d, frozenset({"NEWDESK"})
    )
    assert kept == ["ACME", "BRK.B"]
    assert out == [
        ("NVDA", "desk_name"),
        ("GOOG", "desk_proxy"),
        ("TQQQ", "exchange_traded_product"),
        ("ZZZZ", "unknown_symbol"),
        ("NEWDESK", "desk_added"),
        ("??", "unknown_symbol"),
    ]


def test_the_first_list_of_the_exact_name_is_used() -> None:
    edition = {
        "lists": [
            {"name": "daily radar", "symbols": ["A"]},
            {"name": " DAILY RADAR ", "symbols": ["B"]},
            {"name": "DAILY RADAR", "symbols": ["C"]},
        ]
    }
    assert x.find_list(edition, "DAILY RADAR") == ["B"]
    assert x.find_list(edition, "SMALL CAP MOMENTUM") is None


def receipt(at: str, run: str = "r") -> x.Receipt:
    return x.Receipt(f"radar/copies/2026-11-18/{run}.json", datetime.fromisoformat(at), run, "a" * 64, "b" * 64)


PREV_CLOSE = datetime(2026, 11, 17, 21, 0, tzinfo=UTC)  # 16:00 New York on the session before


def test_the_scored_edition_is_the_first_in_the_span() -> None:
    receipts = [
        receipt("2026-11-18T13:07:00+00:00", "refresh"),
        receipt("2026-11-18T11:12:00+00:00", "first"),
        receipt("2026-11-17T20:00:00+00:00", "early"),
    ]
    chosen, reason = x.scored_receipt(receipts, PREV_CLOSE, "2026-11-18")
    assert chosen is not None and chosen.run_id == "first" and reason == "ok"
    assert x.scored_receipt([receipt("2026-11-18T14:31:00+00:00")], PREV_CLOSE, "2026-11-18") == (None, "received_late")
    assert x.scored_receipt([receipt("2026-11-17T20:59:00+00:00")], PREV_CLOSE, "2026-11-18") == (None, "early")
    assert x.scored_receipt([], PREV_CLOSE, "2026-11-18") == (None, "no_edition")
    # 09:29:59 New York in winter is 14:29:59 UTC: in; 09:30:00 is out.
    assert x.scored_receipt([receipt("2026-11-18T14:29:59+00:00")], PREV_CLOSE, "2026-11-18")[1] == "ok"
    assert x.scored_receipt([receipt("2026-11-18T14:30:00+00:00")], PREV_CLOSE, "2026-11-18")[1] == "received_late"


def test_the_daily_counting_rule() -> None:
    ok = [receipt("2026-11-18T11:12:00+00:00")]
    assert x.counted_status(True, ok, True, PREV_CLOSE, "2026-11-18") == (True, "counted")
    assert x.counted_status(False, ok, True, PREV_CLOSE, "2026-11-18") == (False, "no_listing")
    assert x.counted_status(True, ok, False, PREV_CLOSE, "2026-11-18") == (False, "no_directory")
    assert x.counted_status(True, [], True, PREV_CLOSE, "2026-11-18") == (False, "no_edition")


def test_returns_and_the_five_session_window_keep_names_that_went_away() -> None:
    assert x.returns({"A": [(10.0, 11.0)]}, ["A", "B"]) == {"A": pytest.approx(0.1)}
    assert x.five_session_return([(10.0, 10.5), (10.5, 11.0), None, None, None]) == pytest.approx(0.1)
    assert x.five_session_return([None, (1.0, 2.0)]) is None
    assert x.d_s({"A": 0.02, "B": 0.0}, 0.005) == pytest.approx(0.005)
    assert x.d_s({}, 0.01) is None


def test_percentile_is_numpy_linear() -> None:
    assert x.percentile([1.0, 2.0, 3.0, 4.0], 50) == pytest.approx(2.5)
    assert x.percentile([1.0, 2.0, 3.0, 4.0], 2.5) == pytest.approx(1.075)


def test_bootstrap_is_deterministic_by_seed() -> None:
    d = [0.01 * ((i % 7) - 3) for i in range(50)]
    assert x.bootstrap_means(d, 1, 100) == x.bootstrap_means(d, 1, 100)
    assert x.bootstrap_means(d, 1, 100) != x.bootstrap_means(d, 2, 100)


def test_the_block_bootstrap_keeps_exactly_n_included_sessions() -> None:
    counted: list[float | None] = [1.0 if i % 3 else None for i in range(30)]
    n = sum(1 for v in counted if v is not None)
    means = x.block_bootstrap_means(counted, 7, 50)
    assert len(means) == 50 and all(v == pytest.approx(1.0) for v in means)
    assert n == 20
    with pytest.raises(ValueError):
        x.block_bootstrap_means([None, None], 1, 1)


def test_holm_and_halves() -> None:
    assert x.holm({"a": 0.01, "b": 0.02}) == {"a": True, "b": True}
    assert x.holm({"a": 0.02, "b": 0.03}) == {"a": False, "b": False}
    assert x.holm({"a": 0.001, "b": 1.0}) == {"a": True, "b": False}
    assert x.halves_positive([1.0, 1.0, 1.0, 1.0])
    assert not x.halves_positive([1.0, 1.0, -5.0, 1.0])
    assert not x.halves_positive([1.0])


def test_the_verdicts_on_synthetic_series() -> None:
    strong = [0.02 + 0.001 * ((i % 5) - 2) for i in range(60)]
    none = [0.01 * ((i % 2) * 2 - 1) for i in range(60)]
    assert x.decide_primary(strong)[0] == "supported"
    assert x.decide_primary(none)[0] == "falsified"
    assert x.decide_primary(strong[:39])[0] == "inconclusive"
    out = x.decide_secondary(
        {
            "HYP-0066": (strong, lambda: x.block_bootstrap_means(list(strong), x.SEEDS["HYP-0066"], 500)),
            "HYP-0067": (strong[:10], lambda: []),
        }
    )
    assert out["HYP-0066"][0] == "supported" and out["HYP-0067"][0] == "inconclusive"
