"""The blind rule (trading-lab-research DEC-0046, DEC-0047, EXP-0032), enforced on everything written or published.

Until EXP-0032's read nobody may compute or show an outcome for a focus name that was not picked. This repository
therefore writes no outcome field at all. `assert_blind` refuses a document when:

- any key is not plain ASCII (look-alike letters could spell an outcome word unseen);
- any key, split into words (on "_", "-", spaces, camelCase and letter/digit boundaries), contains an outcome word,
  or has the word "r" (R-multiples: "r", "avg_r", "r_multiple");
- any key, lower-cased with separators removed, contains an outcome stem ("netpnl", "hitrate", "fwdret");
- any value is not plain JSON (dict with str keys, list, str, int, float, bool, None), so nothing can hide inside a
  set, tuple, object or generator;
- any string value starts with "{", "[" or a quote, so no document (JSON, re-encoded JSON or a literal) hides in it.

Deliberately blocked although they could describe a plan rather than a result: "exit" and anything with the word
"r" (for example "exitPlan", "pre_r"). Plan fields use other words ("stop", "target", "ceiling", "floor").

Each document type should also check its exact key set; this guard is the second line.
"""

from __future__ import annotations

import re

OUTCOME_WORDS = frozenset(
    {
        "pnl",
        "outcome",
        "outcomes",
        "result",
        "results",
        "return",
        "returns",
        "ret",
        "fwd",
        "forward",
        "hit",
        "hits",
        "win",
        "wins",
        "winrate",
        "loss",
        "losses",
        "profit",
        "mfe",
        "mae",
        "drawdown",
        "realized",
        "realised",
        "exit",
        "rmultiple",
        "expectancy",
        "payoff",
        "pl",
        "gain",
        "gains",
        "won",
        "lost",
        "winner",
        "winners",
        "loser",
        "losers",
        "stopped",
        "reached",
        "excursion",
        "rr",
    }
)
OUTCOME_STEMS = ("pnl", "hitrate", "winrate", "fwdret", "forwardret", "profit", "outcome", "drawdown", "excursion")
_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")


class BlindRuleError(ValueError):
    """A document carries a field that would hold an outcome, or a value the guard cannot see into."""


def key_words(key: str) -> list[str]:
    """Lower-case words of a key: 'avgR' -> ['avg', 'r'], 'fwd_ret_5d' -> ['fwd', 'ret', '5', 'd']."""
    return [word.lower() for word in _WORD.findall(key)]


def is_outcome_words(words: list[str]) -> bool:
    return any(word == "r" or word in OUTCOME_WORDS for word in words)


def is_outcome_key(key: str) -> bool:
    if not key.isascii():
        return True
    squashed = re.sub(r"[^a-z0-9]", "", key.lower())
    return is_outcome_words(key_words(key)) or any(stem in squashed for stem in OUTCOME_STEMS)


def _embedded_document(text: str) -> bool:
    stripped = text.lstrip()
    return bool(stripped) and stripped[0] in "[{\"'"


def assert_blind(document: object, path: str = "$") -> None:
    """Raise BlindRuleError if `document` could carry an outcome anywhere (see the module docstring)."""
    if isinstance(document, dict):
        for key, value in document.items():
            if not isinstance(key, str):
                raise BlindRuleError(f"{path}: non-string key {key!r}")
            if is_outcome_key(key):
                raise BlindRuleError(f"{path}.{key}: outcome fields are not allowed before EXP-0032's read")
            assert_blind(value, f"{path}.{key}")
    elif isinstance(document, list):
        for index, item in enumerate(document):
            assert_blind(item, f"{path}[{index}]")
    elif isinstance(document, str):
        if _embedded_document(document):
            raise BlindRuleError(f"{path}: a document inside a string is not allowed")
    elif document is None or isinstance(document, (bool, int, float)):
        return
    else:
        raise BlindRuleError(f"{path}: {type(document).__name__} is not plain JSON")
