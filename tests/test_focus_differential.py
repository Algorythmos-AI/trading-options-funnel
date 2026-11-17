"""The port against the cloud script itself, on the 12 real nights mutated many ways.

`tests/reference/focus_original.py` is a byte copy of the cloud routine's `focus.py`, pinned by sha256 below.
"""

import copy
import csv
import hashlib
import importlib.util
import json
import random
import tempfile
from pathlib import Path
from typing import Any

import pytest

from options_funnel.focus_pick import pick

HERE = Path(__file__).resolve().parent
ORIGINAL = HERE / "reference" / "focus_original.py"
ORIGINAL_SHA256 = "76c3797d8cc584edec1432a8ded12077f6ef6e399f0a83f2b370f6a35e37f8dc"
NIGHTS = HERE / "fixtures" / "focus_nights"


def _original() -> Any:
    assert hashlib.sha256(ORIGINAL.read_bytes()).hexdigest() == ORIGINAL_SHA256
    spec = importlib.util.spec_from_file_location("focus_original", ORIGINAL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ORIG = _original()
with open(NIGHTS / "iv_history.csv", newline="") as _fh:
    BASE_IV = list(csv.DictReader(_fh))
FIELDS = list(BASE_IV[0])
BASES = [json.loads(p.read_text()) for p in sorted((NIGHTS / "levels").glob("*.json"))]


def mutate(rng: random.Random, levels: dict[str, Any]) -> dict[str, Any]:
    lv = copy.deepcopy(levels)
    tk = lv["tickers"]
    for _ in range(rng.randint(1, 6)):
        if not tk:
            break
        sym = rng.choice(list(tk))
        t = tk[sym]
        m = rng.randint(0, 12)
        if m == 0:
            t.pop("levels", None)
        elif m == 1:
            t["atr14"] = rng.choice([None, 0, 0.0, 1e-9])
        elif m == 2:
            t.pop("close_strength", None)
        elif m == 3:
            t["close_strength"] = {"chip": rng.choice(["WEAK", "STRONG", None, "MID"])}
        elif m == 4:
            for x in t.get("levels") or []:
                if x["name"] in ("DEMAND", "SUPPLY"):
                    x.pop(rng.choice(["hi", "lo"]), None)
        elif m == 5:
            for x in t.get("levels") or []:
                if rng.random() < 0.3:
                    x["price"] = 0
        elif m == 6:
            t["levels"] = []
        elif m == 7:
            tk.pop("SPY", None)
        elif m == 8:
            keep = rng.sample(list(tk), min(len(tk), rng.randint(1, 3)))
            lv["tickers"] = tk = {k: tk[k] for k in keep}
        elif m == 9:
            for k in rng.sample(list(tk), min(2, len(tk))):
                tk[k] = copy.deepcopy(t)
        elif m == 10:
            for x in t.get("levels") or []:
                if rng.random() < 0.3:
                    x["price"] = round(t["last"]["close"] + rng.uniform(-1, 1) * (t.get("atr14") or 1), 2)
        elif m == 11:
            lv.pop("built_from", None)
        elif m == 12:
            t["atr14"] = -abs(t.get("atr14") or 1)
    return lv


def mutate_iv(rng: random.Random, built: str | None) -> list[dict[str, str]]:
    rows = [dict(r) for r in BASE_IV]
    for _ in range(rng.randint(0, 8)):
        pool = [r for r in rows if r["session"] == built] or rows
        r = rng.choice(pool)
        m = rng.randint(0, 5)
        if m == 0:
            r[rng.choice(["call_vol", "put_vol", "avg_call_vol", "avg_put_vol"])] = ""
        elif m == 1:
            r["avg_call_vol"] = r["avg_put_vol"] = "0"
        elif m == 2:
            rows.append({**r, "call_vol": str(rng.randint(0, 10**6))})
        elif m == 3:
            rows.remove(r)
        elif m == 4:
            r["call_vol"] = rng.choice(["-5", "1e3", " 3 "])
        elif m == 5:
            r[rng.choice(["call_vol", "avg_put_vol"])] = rng.choice(["nan", "inf", "-inf"])
    return rows


def run_original(levels: dict[str, Any], rows: list[dict[str, str]]) -> tuple[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp)
        (data / "levels").mkdir()
        (data / "levels" / f"{levels['session']}.json").write_text(json.dumps(levels))
        with open(data / "iv_history.csv", "w", newline="") as fh:
            writer = csv.DictWriter(fh, FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        try:
            _, doc = ORIG.pick(data=data)
        except Exception as error:  # noqa: BLE001 - the original's failure is the thing compared
            return ("raised", type(error).__name__)
        doc = json.loads(json.dumps(doc))
        doc.pop("picked_at")
        doc.pop("premarket")
        return ("ok", doc)


# Messages of the port's deliberate refusals (see the focus_pick docstring): non-finite numbers.
GUARD_MESSAGES = ("NaN or infinity", "finite number")


def run_port(levels: dict[str, Any], rows: list[dict[str, str]]) -> tuple[str, Any]:
    try:
        return ("ok", json.loads(json.dumps(pick(levels, rows).document)))
    except ValueError as error:
        if any(m in str(error) for m in GUARD_MESSAGES):
            return ("refused", str(error))
        return ("raised", type(error).__name__)
    except Exception as error:  # noqa: BLE001
        return ("raised", type(error).__name__)


@pytest.mark.parametrize("seed", range(4))
def test_port_matches_the_original_on_mutated_nights(seed: int) -> None:
    rng = random.Random(seed)
    refused = 0
    for _ in range(100):
        levels = mutate(rng, rng.choice(BASES))
        rows = mutate_iv(rng, levels.get("built_from"))
        port, original = run_port(levels, rows), run_original(levels, rows)
        if port[0] == "refused":
            # Only where the original published a night with a non-finite number in it.
            assert original[0] == "ok"
            refused += 1
            continue
        assert port == original
    assert refused < 50


GUARDS = [
    ("nan atr", lambda d, s: d["tickers"][s].update({"atr14": float("nan")})),
    ("inf close", lambda d, s: d["tickers"][s]["last"].update({"close": float("inf")})),
    ("nan elsewhere", lambda d, s: d["tickers"][s].update({"expected_move": float("nan")})),
    ("odd symbol", lambda d, s: d["tickers"].update({"brk.b": d["tickers"].pop(s)})),
    ("non-date session", lambda d, s: d.update({"session": "next"})),
]


@pytest.mark.parametrize(("name", "change"), GUARDS, ids=[g[0] for g in GUARDS])
def test_guards_refuse_exactly_where_the_original_would_publish(name: str, change: Any) -> None:
    levels = copy.deepcopy(BASES[-1])
    change(levels, "AMD")
    rows = [dict(r) for r in BASE_IV]
    original = run_original(levels, rows)
    assert original[0] == "ok", f"{name}: the original publishes this night"
    with pytest.raises(ValueError):
        pick(levels, rows)
