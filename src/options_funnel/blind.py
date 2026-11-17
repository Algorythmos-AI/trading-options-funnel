"""The blind rule (trading-lab-research DEC-0046, DEC-0047, EXP-0032), enforced on everything written or published.

Until EXP-0032's read nobody may compute or show an outcome for a focus name that was not picked. This repository
therefore writes no outcome field at all. `assert_blind` refuses a document when:

- any key, split into words (on "_", "-", spaces, camelCase and letter/digit boundaries), contains an outcome word,
  or has the word "r" (R-multiples: "r", "avg_r", "r_multiple");
- any value is not plain JSON (dict with str keys, list, str, int, float, bool, None), so nothing can hide inside a
  set, tuple, object or generator;
- any string value is itself a JSON object or array, so nothing can hide inside an embedded document.

Each document type should also check its exact key set; this guard is the second line.
"""

from __future__ import annotations

import json
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
    }
)
_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")


class BlindRuleError(ValueError):
    """A document carries a field that would hold an outcome, or a value the guard cannot see into."""


def key_words(key: str) -> list[str]:
    """Lower-case words of a key: 'avgR' -> ['avg', 'r'], 'fwd_ret_5d' -> ['fwd', 'ret', '5', 'd']."""
    return [word.lower() for word in _WORD.findall(key)]


def is_outcome_key(key: str) -> bool:
    words = key_words(key)
    return any(word == "r" or word in OUTCOME_WORDS for word in words)


def _embedded_json(text: str) -> bool:
    stripped = text.strip()
    if not stripped or stripped[0] not in "[{":
        return False
    try:
        return isinstance(json.loads(stripped), (dict, list))
    except ValueError:
        return False


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
        if _embedded_json(document):
            raise BlindRuleError(f"{path}: a JSON document inside a string is not allowed")
    elif document is None or isinstance(document, (bool, int, float)):
        return
    else:
        raise BlindRuleError(f"{path}: {type(document).__name__} is not plain JSON")
