"""EXP-0038's frozen analysis core (trading-lab-research DEC-0055, EXP-0038 config.yaml): pure functions only.

Nothing here reads real data or computes a statistic on its own: the read is a separate, later step that runs these
functions once, at the read date (DEC-0055 section 5: nobody computes EXP-0038's statistics before the read).

- `Mulberry32`: the generator EXP-0032 defines, checked against EXP-0038's generator_test_vectors.
- `normalise_symbol`, `Directory`: the unit's symbol rules and the sealed Nasdaq Trader directory (DEC-0055 section 2).
- `scored_names`: a list's names after every frozen exclusion, each excluded name with its reason.
- `counted_status`, `counted_for`: the daily counting rule (DEC-0055 section 1), from the sealed receipt listing and
  directory, the previous close from the pinned market calendar (`options_funnel.calendar`).
- `bootstrap_means`, `block_bootstrap_means`, `percentile`, `p_value`, `holm`, `halves_positive`: the statistics.
- `decide`: the three verdicts, with the 60-analysed-session gate first.

The inputs (sealed listings, read-time checks, desk names over time, spin-offs, bars) are in
`options_funnel.exp0038_inputs`; the status, the read's assembly, its guards and the report in
`options_funnel.exp0038_read`.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from options_funnel import calendar

NEW_YORK = ZoneInfo("America/New_York")
DESK_NAMES = frozenset({"SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "PLTR", "AVGO", "AMD"})
DESK_PROXIES = frozenset({"GOOG"})
LISTS = {"HYP-0065": "DAILY RADAR", "HYP-0066": "DAILY RADAR", "HYP-0067": "SMALL CAP MOMENTUM"}
BENCHMARKS = {"HYP-0065": "RSP", "HYP-0066": "RSP", "HYP-0067": "IWM"}
SEEDS = {"HYP-0065": 20261155, "HYP-0066": 20261156, "HYP-0067": 20261157}
RESAMPLES = 5000
BLOCK = 5
MIN_ANALYSED = 60
MIN_INCLUDED = 40
LEVEL = 0.025
_SYMBOL = re.compile(r"[A-Z][A-Z0-9.]{0,9}")


class Mulberry32:
    """The step of EXP-0032's config.yaml; `next()` gives an unsigned 32-bit integer, `u()` that over 2**32."""

    def __init__(self, seed: int) -> None:
        self.a = seed & 0xFFFFFFFF

    def next(self) -> int:
        self.a = (self.a + 0x6D2B79F5) & 0xFFFFFFFF
        a = self.a
        t = ((a ^ (a >> 15)) * (a | 1)) & 0xFFFFFFFF
        t ^= (t + (((t ^ (t >> 7)) * (t | 61)) & 0xFFFFFFFF)) & 0xFFFFFFFF
        return (t ^ (t >> 14)) & 0xFFFFFFFF

    def u(self) -> float:
        return self.next() / 2**32


def normalise_symbol(raw: object) -> str | None:
    """Upper-cased, stripped, '/' read as '.'; None when the result is not a valid symbol."""
    if not isinstance(raw, str):
        return None
    symbol = raw.strip().upper().replace("/", ".")
    return symbol if _SYMBOL.fullmatch(symbol) else None


@dataclass(frozen=True)
class Directory:
    """One session's sealed Nasdaq Trader directory: every listed symbol and whether it is an ETF."""

    etf: Mapping[str, bool]

    @classmethod
    def parse(cls, nasdaqlisted: str, otherlisted: str) -> Directory:
        etf: dict[str, bool] = {}
        for text, columns in ((nasdaqlisted, ("Symbol",)), (otherlisted, ("ACT Symbol", "CQS Symbol"))):
            lines = [line for line in text.splitlines() if line.strip()]
            header = lines[0].split("|")
            index = {name: i for i, name in enumerate(header)}
            for line in lines[1:]:
                if line.startswith("File Creation Time:"):
                    continue
                cells = line.split("|")
                if len(cells) != len(header) or cells[index["Test Issue"]].strip() == "Y":
                    continue
                is_etf = cells[index["ETF"]].strip() == "Y"
                for column in columns:
                    symbol = normalise_symbol(cells[index[column]])
                    if symbol:
                        etf[symbol] = etf.get(symbol, False) or is_etf
        return cls(etf)


def find_list(edition: Mapping[str, Any], name: str) -> list[object] | None:
    """The symbols of the first list whose name, spaces trimmed, is `name` (case as written); None if there is none."""
    for entry in edition.get("lists") or []:
        if isinstance(entry, Mapping) and isinstance(entry.get("name"), str) and entry["name"].strip() == name:
            symbols = entry.get("symbols")
            return list(symbols) if isinstance(symbols, list) else []
    return None


def scored_names(
    symbols: Iterable[object], directory: Directory, desk_added: frozenset[str]
) -> tuple[list[str], list[tuple[str, str]]]:
    """The list's scored names in list order, de-duplicated, and every other name with its reason.

    The frozen exclusions of DEC-0055 section 2, in this order: desk name, desk added, desk proxy, unknown, ETF.
    """
    kept: list[str] = []
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw in symbols:
        symbol = normalise_symbol(raw)
        if symbol is None:
            key = str(raw).strip()
            if key not in seen:
                seen.add(key)
                out.append((key, "unknown_symbol"))
            continue
        if symbol in seen:
            continue
        seen.add(symbol)
        if symbol in DESK_NAMES:
            out.append((symbol, "desk_name"))
        elif symbol in desk_added:
            out.append((symbol, "desk_added"))
        elif symbol in DESK_PROXIES:
            out.append((symbol, "desk_proxy"))
        elif symbol not in directory.etf:
            out.append((symbol, "unknown_symbol"))
        elif directory.etf[symbol]:
            out.append((symbol, "exchange_traded_product"))
        else:
            kept.append(symbol)
    return kept, out


@dataclass(frozen=True)
class Receipt:
    """One row of the sealed receipt listing.

    A lost row (an edition replaced before its copy was made) has no hashes. It still takes its place in receipt
    order, and if it is the scored edition the session is content_lost at the read.
    """

    path: str
    received_at: datetime
    run_id: str
    copy_sha256: str | None
    edition_sha256: str | None
    lost: bool = False


def scored_receipt(receipts: Sequence[Receipt], previous_close: datetime, session: str) -> tuple[Receipt | None, str]:
    """The earliest copy received after the previous session's close and before 09:30 New York on the session."""
    opening = datetime.combine(datetime.fromisoformat(session).date(), time(9, 30), NEW_YORK)
    in_span = sorted((r for r in receipts if previous_close < r.received_at < opening), key=lambda r: r.received_at)
    if in_span:
        return in_span[0], "ok"
    if any(r.received_at >= opening for r in receipts):
        return None, "received_late"
    if receipts:
        return None, "early"
    return None, "no_edition"


def counted_status(
    listing_sealed: bool, receipts: Sequence[Receipt], directory_ok: bool, previous_close: datetime, session: str
) -> tuple[bool, str]:
    """DEC-0055 section 1's daily rule: counted, or the reason it is not (decided from that day's sealed files only)."""
    if not listing_sealed:
        return False, "no_listing"
    receipt, reason = scored_receipt(receipts, previous_close, session)
    if receipt is None:
        return False, reason
    if not directory_ok:
        return False, "no_directory"
    return True, "counted"


def counted_for(
    session: str, listing_sealed: bool, receipts: Sequence[Receipt], directory_ok: bool
) -> tuple[bool, str]:
    """`counted_status` with the previous session's close from the pinned calendar (13:00 on a half day)."""
    return counted_status(listing_sealed, receipts, directory_ok, calendar.load().previous_close(session), session)


def mean(values: Sequence[float]) -> float:
    return math.fsum(values) / len(values)


def bootstrap_means(d: Sequence[float], seed: int, resamples: int = RESAMPLES) -> list[float]:
    """Draw n sessions with replacement, index floor(u x n), `resamples` times; the mean of each draw."""
    n = len(d)
    rng = Mulberry32(seed)
    return [mean([d[math.floor(rng.u() * n)] for _ in range(n)]) for _ in range(resamples)]


def block_bootstrap_means(
    counted: Sequence[float | None], seed: int, resamples: int = RESAMPLES, block: int = BLOCK
) -> list[float]:
    """HYP-0066: over the m counted sessions in date order (None for a counted session that is not included), draw
    block starts floor(u x m), take `block` consecutive counted sessions each, wrapping; keep the included ones in the
    order drawn until n are kept, the last block cut there; the mean of those n."""
    m = len(counted)
    n = sum(1 for v in counted if v is not None)
    if n == 0:
        raise ValueError("no included session")
    rng = Mulberry32(seed)
    out: list[float] = []
    for _ in range(resamples):
        kept: list[float] = []
        while len(kept) < n:
            start = math.floor(rng.u() * m)
            for k in range(block):
                value = counted[(start + k) % m]
                if value is not None and len(kept) < n:
                    kept.append(value)
        out.append(mean(kept))
    return out


def percentile(values: Sequence[float], p: float) -> float:
    """numpy's linear method: h = (N - 1) p / 100, interpolated between the two sorted neighbours."""
    d = sorted(values)
    h = (len(d) - 1) * p / 100
    lo = math.floor(h)
    hi = min(lo + 1, len(d) - 1)
    return d[lo] + (h - lo) * (d[hi] - d[lo])


def p_value(means: Sequence[float]) -> float:
    """One-sided: (the number of bootstrap means at or below 0, plus 1) / (resamples + 1)."""
    return (sum(1 for v in means if v <= 0) + 1) / (len(means) + 1)


def holm(p: Mapping[str, float], level: float = LEVEL) -> dict[str, bool]:
    """Holm's step-down over the trials given (an inconclusive trial's p is passed as 1)."""
    order = sorted(p, key=lambda k: (p[k], k))
    out = {k: False for k in p}
    m = len(order)
    for i, key in enumerate(order):
        if p[key] <= level / (m - i):
            out[key] = True
        else:
            break
    return out


def halves_positive(d: Sequence[float]) -> bool:
    """The mean above 0 in both halves of the included sessions in date order (the first floor(n/2), then the rest)."""
    half = len(d) // 2
    return half > 0 and mean(d[:half]) > 0 and mean(d[half:]) > 0


def returns(bars: Mapping[str, Sequence[tuple[float, float]]], names: Sequence[str]) -> dict[str, float]:
    """One-session return c/o - 1 per name with a bar; `bars[symbol]` is [(open, close)] for S only."""
    out: dict[str, float] = {}
    for name in names:
        rows = bars.get(name)
        if rows:
            o, c = rows[0]
            out[name] = c / o - 1
    return out


def five_session_return(window: Sequence[tuple[float, float] | None]) -> float | None:
    """c(S+4) / o(S) - 1 over the window's five sessions.

    A missing later bar keeps the last close; None when there is no bar on S.
    """
    if not window or window[0] is None:
        return None
    o = window[0][0]
    last_close = window[0][1]
    for bar in window[1:]:
        if bar is not None:
            last_close = bar[1]
    return last_close / o - 1


def d_s(name_returns: Mapping[str, float], benchmark_return: float) -> float | None:
    """The mean of the scored names' returns minus the benchmark's; None with no scored name."""
    if not name_returns:
        return None
    return mean(list(name_returns.values())) - benchmark_return


Verdict = str


def decide_primary(d: Sequence[float], seed: int = SEEDS["HYP-0065"]) -> tuple[Verdict, dict[str, float]]:
    """HYP-0065: supported when n >= 40, the 2.5th percentile of the bootstrap means > 0, and both halves > 0."""
    if len(d) < MIN_INCLUDED:
        return "inconclusive", {"n": float(len(d))}
    means = bootstrap_means(d, seed)
    low, high = percentile(means, 2.5), percentile(means, 97.5)
    ok = low > 0 and halves_positive(d)
    return ("supported" if ok else "falsified"), {"n": float(len(d)), "mean": mean(d), "low": low, "high": high}


def decide_secondary(
    hyp_0066_counted: Sequence[float | None], hyp_0067: Sequence[float]
) -> dict[str, tuple[Verdict, dict[str, float]]]:
    """HYP-0066 (block bootstrap over its counted series, None where a counted session is not included) and HYP-0067
    (session bootstrap): Holm over both, an inconclusive trial's p taken as 1, and both halves > 0. Seeds and methods
    are fixed here, never passed in."""
    d66 = [v for v in hyp_0066_counted if v is not None]
    trials: dict[str, tuple[Sequence[float], Callable[[], list[float]]]] = {
        "HYP-0066": (d66, lambda: block_bootstrap_means(hyp_0066_counted, SEEDS["HYP-0066"])),
        "HYP-0067": (hyp_0067, lambda: bootstrap_means(hyp_0067, SEEDS["HYP-0067"])),
    }
    p: dict[str, float] = {}
    stats: dict[str, dict[str, float]] = {}
    for key, (d, draw) in trials.items():
        if len(d) < MIN_INCLUDED:
            p[key] = 1.0
            stats[key] = {"n": float(len(d))}
            continue
        means = draw()
        p[key] = p_value(means)
        stats[key] = {
            "n": float(len(d)),
            "mean": mean(d),
            "low": percentile(means, 2.5),
            "high": percentile(means, 97.5),
            "p": p[key],
        }
    passed = holm(p)
    out: dict[str, tuple[Verdict, dict[str, float]]] = {}
    for key, (d, _) in trials.items():
        if len(d) < MIN_INCLUDED:
            out[key] = ("inconclusive", stats[key])
        else:
            out[key] = ("supported" if passed[key] and halves_positive(d) else "falsified", stats[key])
    return out


def decide(
    analysed_sessions: int,
    hyp_0065: Sequence[float],
    hyp_0066_counted: Sequence[float | None],
    hyp_0067: Sequence[float],
) -> dict[str, tuple[Verdict, dict[str, float]]]:
    """All three verdicts. Fewer than 60 analysed sessions: every trial inconclusive, and no statistic is worked out
    (DEC-0055 section 1), not even a mean."""
    if analysed_sessions < MIN_ANALYSED:
        return {
            key: ("inconclusive", {"analysed": float(analysed_sessions)})
            for key in ("HYP-0065", "HYP-0066", "HYP-0067")
        }
    out = {"HYP-0065": decide_primary(hyp_0065)}
    out.update(decide_secondary(hyp_0066_counted, hyp_0067))
    return out
