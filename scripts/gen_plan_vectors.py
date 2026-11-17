"""Plan vectors: the cloud picker's card arithmetic (`focus.py side_plan`) on synthetic inputs, for the dashboard.

trading-lab-research's `dashboard/src/lib/focus-plan.ts` says it does "the same arithmetic" as the card. These vectors
let a test there prove it. The inputs are synthetic (no market data); the outputs come from the sha256-pinned copy of
the original script in tests/reference, never from a re-implementation.

    python scripts/gen_plan_vectors.py OUT.json
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import random
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / "tests" / "reference" / "focus_original.py"
ORIGINAL_SHA256 = "76c3797d8cc584edec1432a8ded12077f6ef6e399f0a83f2b370f6a35e37f8dc"
SEED = 20261117


def _original() -> Any:
    if hashlib.sha256(ORIGINAL.read_bytes()).hexdigest() != ORIGINAL_SHA256:
        raise SystemExit("tests/reference/focus_original.py is not the pinned copy")
    spec = importlib.util.spec_from_file_location("focus_original", ORIGINAL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cases() -> list[dict[str, Any]]:
    rng = random.Random(SEED)
    out: list[dict[str, Any]] = []
    # Hand-picked edges: no ATR, zero, a level without a next target, a next target inside and beyond T1.
    for kind in ("call", "put"):
        d = 1 if kind == "call" else -1
        out += [
            {"kind": kind, "level": 100.0, "atr": None, "next": None},
            {"kind": kind, "level": 100.0, "atr": 0.0, "next": None},
            {"kind": kind, "level": 100.0, "atr": 2.0, "next": None},
            {"kind": kind, "level": 100.0, "atr": 2.0, "next": {"price": 100.0 + d * 0.3, "name": "PDH"}},
            {"kind": kind, "level": 100.0, "atr": 2.0, "next": {"price": 100.0 + d * 5.0, "name": "SUPPLY"}},
            {"kind": kind, "level": 755.02, "atr": 17.49, "next": {"price": 755.02 + d * 12.0, "name": "MA50/PDL"}},
        ]
    # Random levels and ranges across the desk's price span, with rounding ties more likely (2-decimal inputs).
    for _ in range(400):
        kind = rng.choice(["call", "put"])
        d = 1 if kind == "call" else -1
        level = round(rng.uniform(2, 1200), 2)
        atr = round(rng.uniform(0.05, 40), 2)
        nxt = None
        if rng.random() < 0.7:
            nxt = {"price": round(level + d * rng.uniform(0, 4) * atr, 2), "name": rng.choice(["PDH", "PWL", "MA20"])}
        out.append({"kind": kind, "level": level, "atr": atr, "next": nxt})
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: gen_plan_vectors.py OUT.json", file=sys.stderr)
        return 2
    original = _original()
    vectors = []
    for case in cases():
        plan = original.side_plan(case["kind"], case["level"], case["atr"], case["next"])
        vectors.append(
            {
                "input": case,
                "expect": None
                if plan is None
                else {"entry": plan["entry"], "stop": plan["stop"], "t1": plan["t1"], "t2": plan["t2"]},
            }
        )
    doc = {
        "source": "focus.py side_plan (the after-close cloud routine's card), sha256 " + ORIGINAL_SHA256[:16],
        "generator": "trading-options-funnel scripts/gen_plan_vectors.py",
        "seed": SEED,
        "constants": {"ENTRY_ATR": original.ENTRY_ATR, "STOP_ATR": original.STOP_ATR, "ROOM": original.ROOM},
        "vectors": vectors,
    }
    Path(argv[0]).write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {len(vectors)} vectors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
