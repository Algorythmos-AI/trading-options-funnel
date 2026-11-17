"""The Options desk's current picker (`focus.py pick` in the cloud routine), ported with its behaviour unchanged.

Interim: when trading-lab-research DEC-0048 switches the desk to Stage 1 (`wt.options.select`), this port retires.

What it adds over the cloud script: it judges every name, not only the four it keeps, and returns one
`CandidateRecord` per name with the verdict, a reason code and a hash of the inputs that name was judged on.
It computes scores, never outcomes (blind rule, see AGENTS.md).

Inputs (both exactly what the routine reads):
- `levels`: one levels file (`options-tab/data/levels/<session>.json`): `session`, `built_from`, `tickers`.
- `iv_rows`: the rows of `iv_history.csv` (dicts of strings); only rows whose `session` equals `built_from` count.

How a name is scored (the cloud script's docstring, restated):
- a ladder of named levels (PDC and 52WL dropped; a DEMAND zone counts at its top, a SUPPLY zone at its bottom),
  levels within 0.15 ATR of each other merged;
- ceiling = the nearest ladder level more than 0.1 ATR above the close, floor = the nearest more than 0.1 ATR below;
  `d_r` / `d_s` = their distances in ATRs (9 when there is none);
- score = 2 * max(0, 1.5 - min(d_r, d_s)) + min(2, option volume / its average) + min(1.5, 50 * ATR / close),
  where a missing or unusable option-volume row counts as 1.0;
- SPY always takes a slot; the rest go by score, highest first, ties in file order; four names in all.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from options_funnel.candidates import CandidateRecord, Stage, Verdict, inputs_sha256

ALWAYS = "SPY"
N = 4
SKIP = frozenset({"PDC", "52WL"})
METHOD = "Nearest level in ATRs, option activity and range, from the close; SPY always included."

Level = tuple[float, str]


@dataclass(frozen=True, slots=True)
class PickResult:
    """The focus document the cloud script writes (without `picked_at` and `premarket`) and one record per name."""

    document: dict[str, Any]
    candidates: tuple[CandidateRecord, ...]


def ladder(ticker: Mapping[str, Any]) -> list[Level]:
    """Named levels, nearby ones merged (within 0.15 ATR), lowest first: [(price, 'PDH/PMH'), ...]."""
    a = ticker.get("atr14") or 0
    levels = ticker.get("levels") or []
    pts: list[Level] = sorted(
        (lv["price"], lv["name"]) for lv in levels if lv["name"] not in SKIP and lv["name"] not in ("DEMAND", "SUPPLY")
    )
    for lv in levels:
        if lv["name"] == "DEMAND" and lv.get("hi"):
            pts.append((lv["hi"], "DEMAND"))
        if lv["name"] == "SUPPLY" and lv.get("lo"):
            pts.append((lv["lo"], "SUPPLY"))
    pts.sort()
    out: list[Level] = []
    for p, n in pts:
        if out and p - out[-1][0] <= 0.15 * a:
            out[-1] = (out[-1][0], out[-1][1] + "/" + n)
        else:
            out.append((p, n))
    return out


def _lvl(level: Level) -> dict[str, Any]:
    price, name = level
    return {"price": round(price, 2), "name": name[:40]}


def option_volume_ratio(row: Mapping[str, str] | None) -> float | None:
    """(call + put volume) / (their averages), or None when the row is missing or unusable."""
    if not row:
        return None
    try:
        return (float(row["call_vol"]) + float(row["put_vol"])) / (
            float(row["avg_call_vol"]) + float(row["avg_put_vol"])
        )
    except (KeyError, ValueError, ZeroDivisionError):
        return None


def _score_row(sym: str, t: Mapping[str, Any], iv_row: Mapping[str, str] | None) -> dict[str, Any] | None:
    c, a = t["last"]["close"], t.get("atr14") or 0
    if not a:
        return None
    lad = ladder(t)
    up = [x for x in lad if x[0] > c + 0.1 * a]
    dn = [x for x in lad if x[0] < c - 0.1 * a][::-1]
    brk, hold = (up[0][0] if up else None), (dn[0][0] if dn else None)
    d_r = (brk - c) / a if brk else 9
    d_s = (c - hold) / a if hold else 9
    measured = option_volume_ratio(iv_row)
    vr = 1.0 if measured is None else measured
    atr_pct = a / c
    near = min(d_r, d_s)
    score = 2.0 * max(0.0, 1.5 - near) + min(2.0, vr) + min(1.5, atr_pct * 50)
    chip = (t.get("close_strength") or {}).get("chip")
    if d_r <= 1.2 and d_r <= d_s and chip != "WEAK":
        bias = "breakout"
    elif d_s <= 1.2:
        bias = "support"
    else:
        bias = "range"
    nxt_up = next((x for x in up[1:] if brk is not None and x[0] - brk >= 0.5 * a), None) if up else None
    nxt_dn = next((x for x in dn[1:] if hold is not None and hold - x[0] >= 0.5 * a), None) if dn else None
    side = f"{d_r:.1f} ATR under resistance" if d_r <= d_s else f"{d_s:.1f} ATR above support"
    why = f"Closed {side}; close {(chip or 'n/a').lower()}; option volume {vr:.1f}x normal."
    return {
        "symbol": sym,
        "bias": bias,
        "why": why,
        "close": round(c, 2),
        "atr": round(a, 2),
        "resistance": _lvl(up[0]) if up else None,
        "support": _lvl(dn[0]) if dn else None,
        "breakout_above": round(brk, 2) if brk else None,
        "hold_above": round(hold, 2) if hold else None,
        "target_up": _lvl(nxt_up) if nxt_up else None,
        "target_down": _lvl(nxt_dn) if nxt_dn else None,
        "score": round(score, 3),
    }


def pick(levels: Mapping[str, Any], iv_rows: Iterable[Mapping[str, str]]) -> PickResult:
    """Pick the session's focus names exactly as the cloud script does, and record a verdict for every name."""
    session, built = levels["session"], levels.get("built_from")
    iv = {r["ticker"]: r for r in iv_rows if r.get("session") == built}
    rows: list[dict[str, Any]] = []
    not_scored: list[str] = []
    for sym, t in levels["tickers"].items():
        row = _score_row(sym, t, iv.get(sym))
        if row is None:
            not_scored.append(sym)
        else:
            rows.append(row)
    rows.sort(key=lambda r: -r["score"])
    top = [r for r in rows if r["symbol"] == ALWAYS][:1]
    top += [r for r in rows if r["symbol"] != ALWAYS][: N - len(top)]
    top.sort(key=lambda r: -r["score"])
    for i, r in enumerate(top, 1):
        r["rank"] = i
    document = {"session": session, "built_from": built, "method": METHOD, "items": top}

    picked = {r["symbol"]: r for r in top}
    records: list[CandidateRecord] = []
    for sym, t in levels["tickers"].items():
        digest = inputs_sha256({"symbol": sym, "levels": t, "iv": dict(iv[sym]) if sym in iv else None})
        if sym in not_scored:
            records.append(CandidateRecord(session, Stage.FOCUS_PICK, sym, Verdict.NOT_SCORED, "no_atr", digest))
            continue
        row = next(r for r in rows if r["symbol"] == sym)
        if sym in picked:
            reason = "spy_always" if sym == ALWAYS else "top_score"
            records.append(
                CandidateRecord(
                    session, Stage.FOCUS_PICK, sym, Verdict.PICKED, reason, digest, row["score"], picked[sym]["rank"]
                )
            )
        else:
            records.append(
                CandidateRecord(
                    session, Stage.FOCUS_PICK, sym, Verdict.REJECTED, "below_top_four", digest, row["score"]
                )
            )
    return PickResult(document=document, candidates=tuple(records))
