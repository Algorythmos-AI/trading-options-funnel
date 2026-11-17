from dataclasses import dataclass

import pytest

from options_funnel.blind import BlindRuleError, assert_blind, is_outcome_key, key_words


@pytest.mark.parametrize(
    "key",
    [
        "r",
        "R",
        "avg_r",
        "avgR",
        "outcome_r",
        "r_multiple",
        "pnl",
        "pnl_usd",
        "pnlUsd",
        "realized_pnl",
        "returns_1d",
        "fwd_ret_5d",
        "forwardReturn",
        "hit_t1",
        "t1-hit",
        "hit rate",
        "win_rate",
        "mfe",
        "mae",
        "exit_price",
        "drawdown",
        "profit_factor",
        "Outcome",
    ],
)
def test_outcome_keys(key: str) -> None:
    assert is_outcome_key(key)
    with pytest.raises(BlindRuleError):
        assert_blind({"names": [{"symbol": "AAPL", key: 1}]})


@pytest.mark.parametrize(
    "key",
    ["symbol", "score", "rank", "reason", "move", "move_pct", "label", "fill", "gap_pct", "close", "atr14", "bias"],
)
def test_input_keys_pass(key: str) -> None:
    assert not is_outcome_key(key)
    assert_blind({key: 1})


def test_key_words() -> None:
    assert key_words("avgR") == ["avg", "r"]
    assert key_words("fwd_ret_5d") == ["fwd", "ret", "5", "d"]
    assert key_words("ATRPct") == ["atr", "pct"]


@dataclass
class Hidden:
    pnl: float


@pytest.mark.parametrize(
    "doc",
    [
        {"x": {("pnl", 1)}},
        {"x": ("a",)},
        {"x": Hidden(1.0)},
        {"x": (i for i in range(2))},
        {"inputs": '{"pnl": 1}'},
        {"inputs": " [1, 2]"},
        {1: "x"},
    ],
)
def test_values_the_guard_cannot_see_into_are_refused(doc: object) -> None:
    with pytest.raises(BlindRuleError):
        assert_blind(doc)


def test_plain_values_pass() -> None:
    assert_blind({"a": [1, 2.5, None, True, "text", "a {b}", {"b": "outcome"}]})


@pytest.mark.parametrize(
    "key",
    [
        "PnL",
        "netpnl",
        "hitrate",
        "fwdret",
        "pl",
        "gains",
        "won",
        "lost",
        "winner",
        "stopped_out",
        "t1_reached",
        "max_favorable_excursion",
        "rr",
        "prоfit",
        "exitPlan",
        "pre_r",
    ],
)
def test_more_outcome_keys(key: str) -> None:
    assert is_outcome_key(key)


@pytest.mark.parametrize("key", ["stop", "target_1", "ceiling", "floor", "rs13", "gapPct", "ma20", "close_strength"])
def test_plan_keys_pass(key: str) -> None:
    assert not is_outcome_key(key)


@pytest.mark.parametrize("text", ['"{\\"pnl\\":1}"', "{'pnl': 1}", "  [1]"])
def test_strings_that_could_hold_documents_are_refused(text: str) -> None:
    with pytest.raises(BlindRuleError):
        assert_blind({"x": text})
