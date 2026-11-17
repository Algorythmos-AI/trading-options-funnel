"""The Radar desk file (funnel plan Phase 1): one sealed, display-only file a session for the Options desk's 12 names.

Built each morning, after the radar seal step, from files already sealed or kept in git:

- the session's sealed radar edition (`data/radar/<S>`, the latest folder that verifies);
- the session's sealed focus file (`data/focus/<S>`, sealed by the after-close routine of the session before);
- trading-lab-research's `config/events/earnings.csv`, `config/events/macro_events.csv` and `config/options_desk.yaml`,
  copied into the sealed folder so the file can be rebuilt from the `data` branch alone.

For each desk name it records whether the name is a focus name, what the Radar said about it (its lists as codes, its
grades, its risk tag, its weekly support and resistance), the desk's own levels beside them, earnings and high-impact
macro events up to the next open, and closed flag codes. Nothing in it is free text, and it carries no outcome of any
kind (the blind rule, trading-lab-research DEC-0047 section 8): no price after the edition, no score, no rank.

The flags inform the owner; they change no pick, no board state and no checklist (Phase 2 needs its own HYP, EXP and
DEC). `earnings.csv` stays the board's source; the Radar's calendar is only a cross-check.

Sealing uses `options_funnel.seal` unchanged: this module registers its own kind, `radar-desk`, at import, so no file
of the frozen EXP-0038 analysis commit changes and nothing is added to `data/radar/<S>` (a folder there with an extra
file would not verify, and its session would leave EXP-0038).

    python -m options_funnel.desk seal   --root R --session S --earnings P --macro P --desk-config P --wt-commit SHA
    python -m options_funnel.desk check  --root R --session S     # rebuild from the sealed copies, compare bytes
    python -m options_funnel.desk send   --root R --session S     # the latest sealed desk file, to the dashboard
    python -m options_funnel.desk verify FOLDER...
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from options_funnel import calendar as cal
from options_funnel import publish
from options_funnel.blind import BlindRuleError, assert_blind

# Private names of seal.py and cli.py are used on purpose: both files are part of EXP-0038's frozen analysis commit
# (eaff05f), so they cannot change under this module, and reusing them keeps one sealing code path.
from options_funnel.cli import (
    EXIT_INPUT,
    EXIT_MISMATCH,
    EXIT_SEALED_DIFFERENT,
    EXIT_SEND,
    EXIT_WRITE,
    _base_provenance,
    _now,
)
from options_funnel.exp0038 import normalise_symbol
from options_funnel.exp0038_inputs import InputError as ConfigError
from options_funnel.exp0038_inputs import parse_desk_names
from options_funnel.seal import (
    _FOLDER,
    KINDS,
    MANIFEST,
    AlreadySealedDifferent,
    SealError,
    check_session,
    seal,
    sha256,
    verify,
)

KIND = "radar-desk"
DESK = "desk.json"
EARNINGS = "earnings.csv"
MACRO = "macro_events.csv"
DESK_CONFIG = "options_desk.yaml"
FILES = frozenset({DESK, EARNINGS, MACRO, DESK_CONFIG})

SCHEMA = "options-funnel/radar-desk"
SCHEMA_VERSION = 1

if KINDS.setdefault(KIND, FILES) != FILES:  # pragma: no cover - a second, different registration is a bug
    raise RuntimeError(f"seal kind {KIND!r} is already registered with other files")

# The Radar's list names as closed codes (anything else is "other"), so no list text reaches the file.
LIST_CODES = {
    "DAILY RADAR": "daily_radar",
    "SUPPORT PLAYS": "support_plays",
    "MEGA CAP OPTIONS": "mega_cap_options",
    "INSTITUTIONAL CONVICTION": "institutional_conviction",
    "BREAKOUT WATCH": "breakout_watch",
    "SMALL CAP MOMENTUM": "small_cap_momentum",
}

FLAGS = (
    "EARNINGS_BEFORE_OPEN",  # the name reports before today's open: the gap is already in the open
    "EARNINGS_IN_HOLD",  # the name reports during today's session, after today's close or before the next open
    "EARNINGS_SOURCES_DISAGREE",  # earnings.csv and the Radar's calendar differ over the session and six days after
    "MACRO_HIGH_IN_HOLD",  # a high-severity macro event between today's open and the next open
    "RADAR_RISK_TAG",  # the Radar tagged the name "risk"
    "RADAR_NOT_COVERED",  # the edition has no ticker row for the name
    "LEVELS_DISAGREE",  # the Radar's weekly support or resistance is more than LEVEL_GAP_ATR from the desk's
)
LEVEL_GAP_ATR = 1.0  # display threshold only; it decides nothing
LOOKAHEAD_DAYS = 6  # earnings compared over the session and the six days after it (the Radar's 7-day calendar)
# A desk share class read as the desk name (DEC-0055 `desk_proxy`): the Radar or a file may write either.
ALIASES = {"GOOG": "GOOGL"}
OPEN = "09:30"
MAX_PRICE = 1_000_000.0  # the dashboard schema's bounds (trading-lab-research radar-desk.schema.json)
MAX_GAP_ATR = 1000.0
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_TIME = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")
_CODE = re.compile(r"[a-z_]{1,40}")
WHEN = ("bmo", "during", "amc")


class DeskError(ValueError):
    """An input of the desk file is missing, unreadable or not what it should be."""


# ---------------------------------------------------------------------------------------------------------- inputs


def latest_verified(root: Path, kind: str, session: str) -> Path | None:
    """The latest folder of `session` under data/<kind> (the end of its correction chain), or None. It is not
    checked here: a latest folder that does not verify is refused by the caller, never passed over for an older one."""
    base = root / "data" / kind
    if not base.is_dir():
        return None
    found: list[tuple[int, Path]] = []
    for path in base.glob(f"{session}*"):
        match = _FOLDER.fullmatch(path.name)
        if match and match.group(1) == session and path.is_dir():
            found.append((int(match.group(2) or 1), path))
    return max(found)[1] if found else None


def _text(data: bytes, what: str) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise DeskError(f"{what} is not UTF-8") from error


def _is_date(value: object) -> bool:
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        return False
    try:
        check_session(value)
    except SealError:
        return False
    return True


def _json(data: bytes, what: str) -> dict[str, Any]:
    try:
        doc = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as error:
        raise DeskError(f"{what} is not JSON ({error})") from error
    if not isinstance(doc, dict):
        raise DeskError(f"{what} is not a JSON object")
    return doc


def read_earnings(data: bytes) -> dict[str, list[tuple[str, str]]]:
    """earnings.csv as {symbol: [(date, when), ...]}; comment lines (#) skipped; a bad row is refused."""
    text = _text(data, "earnings.csv")
    rows = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    reader = csv.DictReader(io.StringIO("\n".join(rows)))
    if reader.fieldnames is None or not {"symbol", "date", "when"} <= set(reader.fieldnames):
        raise DeskError("earnings.csv needs the columns symbol, date and when")
    out: dict[str, list[tuple[str, str]]] = {}
    for row in reader:
        symbol = _desk_symbol(row["symbol"])
        when = (row["when"] or "").strip()
        day = (row["date"] or "").strip()
        if symbol is None or when not in WHEN:
            raise DeskError(f"earnings.csv has an unreadable row: {row['symbol']!r}, {row['when']!r}")
        if not _is_date(day):
            raise DeskError(f"earnings.csv has a bad date {day!r}")
        out.setdefault(symbol, []).append((day, when))
    return out


def read_macro(data: bytes) -> list[tuple[str, str, str, str]]:
    """macro_events.csv as [(date, time_et, type, severity)]."""
    reader = csv.DictReader(io.StringIO(_text(data, "macro_events.csv")))
    if reader.fieldnames is None or not {"date", "time_et", "type", "severity"} <= set(reader.fieldnames):
        raise DeskError("macro_events.csv needs the columns date, time_et, type and severity")
    out = []
    for row in reader:
        day, at = (row["date"] or "").strip(), (row["time_et"] or "").strip()
        kind, severity = (row["type"] or "").strip(), (row["severity"] or "").strip()
        if not _is_date(day) or not _TIME.fullmatch(at) or not _CODE.fullmatch(kind) or not _CODE.fullmatch(severity):
            raise DeskError(f"macro_events.csv has an unreadable row for {day!r}")
        out.append((day, at, kind, severity))
    return out


# ---------------------------------------------------------------------------------------------------------- build


def _number(value: object) -> float | None:
    """A finite number, else None (NaN and infinities from the JSON parser are dropped)."""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def _list(value: object) -> list[Any]:
    """A JSON array as a list; anything else (a string, a number, an object, null) as an empty list."""
    return value if isinstance(value, list) else []


def _desk_symbol(raw: object) -> str | None:
    symbol = normalise_symbol(raw)
    return ALIASES.get(symbol, symbol) if symbol else None


def _grade(value: object) -> int | None:
    """The Radar's 1-3 grade (its own schema's range), else None."""
    return value if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 3 else None


def _level(value: object) -> float | None:
    """A price level the dashboard accepts (above 0, at most 1,000,000), rounded to cents; else None."""
    number = _number(value)
    return round(number, 2) if number is not None and 0 < number <= MAX_PRICE else None


def _round(value: float | None, places: int = 2) -> float | None:
    return None if value is None else round(value, places)


def _radar_tickers(edition: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    for row in _list(edition.get("tickers")):
        if isinstance(row, Mapping):
            symbol = _desk_symbol(row.get("symbol"))
            if symbol and symbol not in out:
                out[symbol] = row
    return out


def _radar_lists(edition: Mapping[str, Any]) -> dict[str, set[str]]:
    """{symbol: list codes} from the edition's lists and from each ticker's own `lists`."""
    out: dict[str, set[str]] = {}

    def add(symbol: object, name: object) -> None:
        sym = _desk_symbol(symbol)
        if sym and isinstance(name, str):
            out.setdefault(sym, set()).add(LIST_CODES.get(name.strip().upper(), "other"))

    for entry in _list(edition.get("lists")):
        if isinstance(entry, Mapping):
            for symbol in _list(entry.get("symbols")):
                add(symbol, entry.get("name"))
    for symbol, row in _radar_tickers(edition).items():
        for name in _list(row.get("lists")):
            add(symbol, name)
    return out


def _radar_earnings(edition: Mapping[str, Any], names: frozenset[str]) -> dict[str, set[str]] | None:
    """{desk name: dates} from the edition's calendar rows of kind earnings; None when it has none at all.

    A label's tickers are its whole upper-case words of one to five letters, as written (so "Microsoft" or "MSFT."
    read as MSFT only in the second case); GOOG is read as GOOGL. Rows whose date is not YYYY-MM-DD are skipped."""
    rows = [r for r in _list(edition.get("calendar")) if isinstance(r, Mapping) and r.get("kind") == "earnings"]
    if not rows:
        return None
    out: dict[str, set[str]] = {}
    for row in rows:
        day, label = row.get("date"), row.get("label")
        if not isinstance(day, str) or not _is_date(day) or not isinstance(label, str):
            continue
        for token in re.findall(r"\b[A-Z]{1,5}\b", label):
            symbol = ALIASES.get(token, token)
            if symbol in names:
                out.setdefault(symbol, set()).add(day)
    return out


def _focus_items(focus: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    for item in _list((focus or {}).get("items")):
        if isinstance(item, Mapping):
            symbol = _desk_symbol(item.get("symbol"))
            if symbol:
                out[symbol] = item
    return out


def _price(level: object) -> float | None:
    return _level(level.get("price")) if isinstance(level, Mapping) else None


def build(
    session: str,
    edition: Mapping[str, Any],
    focus: Mapping[str, Any] | None,
    earnings: Mapping[str, Sequence[tuple[str, str]]],
    macro: Sequence[tuple[str, str, str, str]],
    desk_names: frozenset[str],
    inputs: Mapping[str, str | None],
    calendar: cal.Calendar,
) -> dict[str, Any]:
    """The desk document for `session` (a regular session). Deterministic: same inputs, same document."""
    if not calendar.is_session(session):
        raise DeskError(f"{session} is not a regular session")
    if edition.get("edition_date") != session:
        raise DeskError(f"the edition is for {edition.get('edition_date')!r}, not {session}")
    if focus is not None and focus.get("session") != session:
        raise DeskError(f"the focus file is for {focus.get('session')!r}, not {session}")
    following = calendar.session_plus(session, 1)
    horizon = (date.fromisoformat(session) + timedelta(days=LOOKAHEAD_DAYS)).isoformat()
    tickers, lists = _radar_tickers(edition), _radar_lists(edition)
    radar_dates = _radar_earnings(edition, desk_names)
    items = _focus_items(focus)
    held_macro = sorted(
        {
            (day, at, kind, severity)
            for day, at, kind, severity in macro
            if (day == session and at >= OPEN) or session < day < following or (day == following and at < OPEN)
        }
    )
    macro_rows = [{"date": d, "time_et": t, "type": k, "severity": s} for d, t, k, s in held_macro]
    macro_high = any(row["severity"] == "high" for row in macro_rows)
    names = []
    for symbol in sorted(desk_names):
        flags: set[str] = set()
        rows = sorted(earnings.get(symbol, ()))
        before_open = any(d == session and w == "bmo" for d, w in rows)
        in_hold = any(
            (d == session and w in ("during", "amc")) or session < d < following or (d == following and w == "bmo")
            for d, w in rows
        )
        csv_dates = sorted({d for d, _ in rows if session <= d <= horizon})
        radar = sorted(d for d in (radar_dates or {}).get(symbol, ()) if session <= d <= horizon)
        agree = None if radar_dates is None else csv_dates == radar
        upcoming = next(((d, w) for d, w in rows if d >= session), None)
        if before_open:
            flags.add("EARNINGS_BEFORE_OPEN")
        if in_hold:
            flags.add("EARNINGS_IN_HOLD")
        if agree is False:
            flags.add("EARNINGS_SOURCES_DISAGREE")
        if macro_high:
            flags.add("MACRO_HIGH_IN_HOLD")
        row = tickers.get(symbol)
        radar_block: dict[str, Any] | None = None
        if row is None:
            flags.add("RADAR_NOT_COVERED")
        else:
            risk = row.get("tag") == "risk"
            if risk:
                flags.add("RADAR_RISK_TAG")
            radar_block = {
                "lists": sorted(lists.get(symbol, set())),
                "evidence_grade": _grade(row.get("evidence_grade")),
                "chart_grade": _grade(row.get("chart_grade")),
                "risk_tag": risk,
                "support": _level(row.get("support")),
                "resistance": _level(row.get("resistance")),
            }
        item = items.get(symbol)
        desk_block: dict[str, Any] | None = None
        gaps: dict[str, float | None] | None = None
        if item is not None:
            atr = _level(item.get("atr"))
            desk_block = {
                "support": _price(item.get("support")),
                "resistance": _price(item.get("resistance")),
                "atr": atr,
            }
            if radar_block is not None and atr:
                gaps = {}
                for side in ("support", "resistance"):
                    a, b = radar_block[side], desk_block[side]
                    gap = None if a is None or b is None else round((a - b) / atr, 2)
                    gaps[side] = gap if gap is not None and abs(gap) <= MAX_GAP_ATR else None
                if any(g is not None and abs(g) > LEVEL_GAP_ATR for g in gaps.values()):
                    flags.add("LEVELS_DISAGREE")
        names.append(
            {
                "symbol": symbol,
                "focus": None if focus is None else symbol in items,
                "radar": radar_block,
                "desk_levels": desk_block,
                "level_gap_atr": gaps,
                "earnings": {
                    "next": None if upcoming is None else {"date": upcoming[0], "when": upcoming[1]},
                    "csv_dates": csv_dates,
                    "radar_dates": None if radar_dates is None else radar,
                    "sources_agree": agree,
                },
                "flags": [f for f in FLAGS if f in flags],
            }
        )
    doc: dict[str, Any] = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "session": session,
        "next_session": following,
        "inputs": dict(sorted(inputs.items())),
        "coverage": {
            "desk_names": len(desk_names),
            "radar_covered": sum(1 for s in desk_names if s in tickers),
            "focus_names": None if focus is None else len(items),
            "radar_calendar_has_earnings": radar_dates is not None,
        },
        "macro_in_hold": macro_rows,
        "names": names,
    }
    check(doc)
    return doc


# ---------------------------------------------------------------------------------------------------------- check

_TOP = {"schema", "schema_version", "session", "next_session", "inputs", "coverage", "macro_in_hold", "names"}
_INPUTS = {"radar_folder", "edition_sha256", "focus_folder", "focus_sha256", "wt_commit"}
_COVERAGE = {"desk_names", "radar_covered", "focus_names", "radar_calendar_has_earnings"}
_NAME = {"symbol", "focus", "radar", "desk_levels", "level_gap_atr", "earnings", "flags"}
_RADAR = {"lists", "evidence_grade", "chart_grade", "risk_tag", "support", "resistance"}
_DESK_LEVELS = {"support", "resistance", "atr"}
_GAPS = {"support", "resistance"}
_EARNINGS = {"next", "csv_dates", "radar_dates", "sources_agree"}
_MACRO = {"date", "time_et", "type", "severity"}
_SHA = re.compile(r"[0-9a-f]{64}")


def _keys(obj: object, keys: set[str], where: str) -> Mapping[str, Any]:
    if not isinstance(obj, Mapping) or set(obj) != keys:
        raise DeskError(f"{where}: needs exactly {sorted(keys)}")
    return obj


def check(doc: Mapping[str, Any]) -> None:
    """The exact key set of the desk document, closed codes only, then the blind guard."""
    top = _keys(doc, _TOP, "$")
    if top["schema"] != SCHEMA or top["schema_version"] != SCHEMA_VERSION:
        raise DeskError("$: not a radar-desk v1 document")
    inputs = _keys(top["inputs"], _INPUTS, "$.inputs")
    for key in ("edition_sha256", "focus_sha256"):
        if inputs[key] is not None and not _SHA.fullmatch(str(inputs[key])):
            raise DeskError(f"$.inputs.{key}: not a sha256")
    if not _is_date(top["session"]) or not _is_date(top["next_session"]):
        raise DeskError("$: session and next_session must be dates")
    _keys(top["coverage"], _COVERAGE, "$.coverage")
    for i, row in enumerate(top["macro_in_hold"]):
        macro = _keys(row, _MACRO, f"$.macro_in_hold[{i}]")
        if not _CODE.fullmatch(str(macro["type"])) or not _CODE.fullmatch(str(macro["severity"])):
            raise DeskError(f"$.macro_in_hold[{i}]: type and severity must be codes")
        if not _is_date(macro["date"]) or not _TIME.fullmatch(str(macro["time_et"])):
            raise DeskError(f"$.macro_in_hold[{i}]: date and time_et must be YYYY-MM-DD and HH:MM")
    for i, row in enumerate(top["names"]):
        where = f"$.names[{i}]"
        name = _keys(row, _NAME, where)
        if name["radar"] is not None:
            radar = _keys(name["radar"], _RADAR, f"{where}.radar")
            if any(code not in set(LIST_CODES.values()) | {"other"} for code in radar["lists"]):
                raise DeskError(f"{where}.radar.lists: unknown code")
        if name["desk_levels"] is not None:
            _keys(name["desk_levels"], _DESK_LEVELS, f"{where}.desk_levels")
        if name["level_gap_atr"] is not None:
            _keys(name["level_gap_atr"], _GAPS, f"{where}.level_gap_atr")
        earnings = _keys(name["earnings"], _EARNINGS, f"{where}.earnings")
        dates = [*earnings["csv_dates"], *(earnings["radar_dates"] or [])]
        if earnings["next"] is not None:
            dates.append(earnings["next"].get("date") if isinstance(earnings["next"], Mapping) else None)
        if not all(_is_date(d) for d in dates):
            raise DeskError(f"{where}.earnings: every date must be YYYY-MM-DD")
        if earnings["next"] is not None and _keys(earnings["next"], {"date", "when"}, where)["when"] not in WHEN:
            raise DeskError(f"{where}.earnings.next.when: not a code")
        if any(flag not in FLAGS for flag in name["flags"]):
            raise DeskError(f"{where}.flags: unknown flag")
    try:
        assert_blind(dict(doc))
    except BlindRuleError as error:
        raise DeskError(str(error)) from error


def encode(doc: Mapping[str, Any]) -> bytes:
    return (json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n").encode()


# ---------------------------------------------------------------------------------------------------------- run


def from_files(
    root: Path,
    session: str,
    earnings: bytes,
    macro: bytes,
    desk_config: bytes,
    wt_commit: str | None,
    radar_folder: str | None = None,
    focus_folder: str | None = None,
    latest_focus: bool = True,
) -> bytes:
    """desk.json for `session` from the sealed radar and focus folders and the three config files.

    `radar_folder` / `focus_folder` pin the folders (used when rebuilding a sealed desk file); otherwise the latest
    folder of the session that verifies is used, and a session with no focus folder gets `focus: null`.
    `latest_focus=False` with no `focus_folder` builds without a focus file (a rebuild of a file sealed without one).
    """
    check_session(session)
    radar_path = (root / "data" / "radar" / radar_folder) if radar_folder else latest_verified(root, "radar", session)
    if radar_path is None:
        raise DeskError(f"no sealed radar folder for {session}")
    if verify(radar_path):
        raise DeskError(f"the radar folder {radar_path.name} does not verify")
    edition_bytes = (radar_path / "edition.json").read_bytes()
    focus_path: Path | None = None
    if focus_folder:
        focus_path = root / "data" / "focus" / focus_folder
        if verify(focus_path):
            raise DeskError(f"the focus folder {focus_folder} does not verify")
    elif latest_focus:
        focus_path = latest_verified(root, "focus", session)
        if focus_path is not None and verify(focus_path):
            raise DeskError(f"the focus folder {focus_path.name} does not verify")
    focus_bytes = (focus_path / "focus.json").read_bytes() if focus_path else None
    try:
        names = frozenset(ALIASES.get(n, n) for n in parse_desk_names(_text(desk_config, "options_desk.yaml")))
    except ConfigError as error:
        raise DeskError(str(error)) from error
    doc = build(
        session,
        _json(edition_bytes, "edition.json"),
        _json(focus_bytes, "focus.json") if focus_bytes is not None else None,
        read_earnings(earnings),
        read_macro(macro),
        names,
        {
            "radar_folder": radar_path.name,
            "edition_sha256": sha256(edition_bytes),
            "focus_folder": focus_path.name if focus_path else None,
            "focus_sha256": sha256(focus_bytes) if focus_bytes is not None else None,
            "wt_commit": wt_commit,
        },
        cal.load(),
    )
    return encode(doc)


def _seal_cmd(args: argparse.Namespace) -> int:
    try:
        sources = {"earnings": args.earnings, "macro": args.macro, "desk_config": args.desk_config}
        data = {name: path.read_bytes() for name, path in sources.items()}
        if args.wt_commit and not re.fullmatch(r"[0-9a-f]{40}", args.wt_commit):
            raise DeskError("--wt-commit must be a 40-character commit")
        desk = from_files(args.root, args.session, data["earnings"], data["macro"], data["desk_config"], args.wt_commit)
    except (OSError, DeskError, SealError, cal.CalendarError, csv.Error) as error:
        print(f"desk: {type(error).__name__}: {error}", file=sys.stderr)
        return EXIT_INPUT
    files = {DESK: desk, EARNINGS: data["earnings"], MACRO: data["macro"], DESK_CONFIG: data["desk_config"]}
    provenance = _base_provenance(args, sources) | {"wt_commit": args.wt_commit}
    try:
        sealed = seal(args.root, KIND, args.session, files, _now(), provenance, args.correction)
    except AlreadySealedDifferent as error:
        print(str(error), file=sys.stderr)
        return EXIT_SEALED_DIFFERENT
    except SealError as error:
        print(f"desk: {error}", file=sys.stderr)
        return EXIT_INPUT
    except OSError as error:
        print(f"could not write: {error}", file=sys.stderr)
        return EXIT_WRITE
    print(f"{'sealed' if sealed.created else 'already sealed, identical:'} {sealed.folder}")
    return 0


def rebuild(folder: Path) -> list[str]:
    """Problems found rebuilding a sealed desk folder's desk.json from its own copies and the folders it names."""
    problems = verify(folder)
    if problems:
        return problems
    manifest = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    sealed = (folder / DESK).read_bytes()
    inputs = _json(sealed, DESK)["inputs"]
    root = folder.parents[2]
    try:
        again = from_files(
            root,
            manifest["session"],
            (folder / EARNINGS).read_bytes(),
            (folder / MACRO).read_bytes(),
            (folder / DESK_CONFIG).read_bytes(),
            inputs.get("wt_commit"),
            radar_folder=inputs["radar_folder"],
            focus_folder=inputs.get("focus_folder"),
            latest_focus=False,
        )
    except (OSError, DeskError, SealError, cal.CalendarError, csv.Error) as error:
        return [f"{folder}: cannot rebuild ({type(error).__name__}: {error})"]
    return [] if again == sealed else [f"{folder}: desk.json does not rebuild to the same bytes"]


# ---------------------------------------------------------------------------------------------------------- send

DASHBOARD_SCHEMA = "stocksdelta/radar-desk"
_SENT = ("session", "next_session", "coverage", "macro_in_hold", "names")


def dashboard_doc(folder: Path) -> dict[str, Any]:
    """The dashboard's `stocksdelta/radar-desk` v1 document for a sealed desk folder that verifies: the sealed
    desk.json without its `inputs` (folder names and hashes stay on the data branch), with run_id = the sha256 of
    the sealed bytes and as_of = when it was sealed."""
    problems = verify(folder)
    if problems:
        raise DeskError("; ".join(problems))
    sealed = (folder / DESK).read_bytes()
    manifest = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    desk = _json(sealed, DESK)
    check(desk)
    doc: dict[str, Any] = {"schema": DASHBOARD_SCHEMA, "schema_version": SCHEMA_VERSION, "run_id": sha256(sealed)}
    doc["as_of"] = manifest["sealed_at"]
    doc |= {key: desk[key] for key in _SENT}
    assert_blind(doc)
    return doc


def _accepts(base: str, bypass: str | None) -> int | None:
    request = urllib.request.Request(f"{base}/api/health", headers=publish._headers(bypass))
    try:
        with urllib.request.urlopen(request, timeout=publish.TIMEOUT_S) as response:
            health: Any = json.load(response)
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        raise publish.PublishError(f"health check failed: {type(error).__name__}") from error
    editions = health.get("editions") if isinstance(health, dict) else None
    value = ((editions or {}).get("radar_desk") or {}).get("accepts") if isinstance(editions, dict) else None
    return value if isinstance(value, int) else None


def send(doc: Mapping[str, Any], base: str = publish.DEFAULT_BASE, now: float | None = None) -> publish.Sent:
    """POST the document once to the signed ingest with the radar key; 200 stored or duplicate is success."""
    secret = os.environ.get("RADAR_INGEST_SECRET")
    if not secret:
        raise publish.PublishError("RADAR_INGEST_SECRET is not set")
    bypass = os.environ.get("VERCEL_AUTOMATION_BYPASS_SECRET")
    accepted = _accepts(base, bypass)
    if accepted is None or accepted < doc["schema_version"]:
        raise publish.PublishError(
            f"the site does not accept {DASHBOARD_SCHEMA} v{doc['schema_version']} (accepts {accepted})"
        )
    body = json.dumps(doc, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    ts = int(now if now is not None else time.time())
    headers = {
        "content-type": "application/json",
        "x-wt-timestamp": str(ts),
        "x-wt-signature": publish.sign(secret, ts, body),
        "x-wt-key-id": publish.KEY_ID,
        **publish._headers(bypass),
    }
    request = urllib.request.Request(f"{base}/api/ingest", data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=publish.TIMEOUT_S) as response:
            answer: Any = json.load(response)
            status = str(answer.get("status", "stored")) if isinstance(answer, dict) else "stored"
            return publish.Sent(response.status, status)
    except urllib.error.HTTPError as error:
        raise publish.PublishError(f"HTTP {error.code}") from error
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        raise publish.PublishError(f"send failed: {type(error).__name__}") from error


def _send_cmd(args: argparse.Namespace) -> int:
    folder = latest_verified(args.root, KIND, args.session) if _is_date(args.session) else None
    if folder is None:
        print(f"no sealed desk folder for {args.session!r}", file=sys.stderr)
        return EXIT_INPUT
    try:
        doc = dashboard_doc(folder)
    except (OSError, DeskError, BlindRuleError, KeyError) as error:
        print(f"desk: {type(error).__name__}: {error}", file=sys.stderr)
        return EXIT_INPUT
    if args.dry_run:
        print(json.dumps(doc, indent=1, sort_keys=True))
        return 0
    try:
        sent = send(doc, args.base)
    except publish.PublishError as error:
        print(f"desk send: {error}", file=sys.stderr)
        return EXIT_SEND
    print(f"sent {folder.name}: HTTP {sent.status} {sent.outcome}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m options_funnel.desk", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sd = sub.add_parser("seal", help="build and seal the session's desk file")
    sd.add_argument("--root", type=Path, required=True, help="a checkout of the data branch")
    sd.add_argument("--session", required=True)
    sd.add_argument("--earnings", type=Path, required=True, help="trading-lab-research config/events/earnings.csv")
    sd.add_argument("--macro", type=Path, required=True, help="trading-lab-research config/events/macro_events.csv")
    sd.add_argument("--desk-config", type=Path, required=True, help="trading-lab-research config/options_desk.yaml")
    sd.add_argument("--wt-commit", help="the trading-lab-research commit the three files came from")
    sd.add_argument("--routine", help="the routine's trigger id")
    sd.add_argument("--correction", action="store_true")
    ck = sub.add_parser("check", help="rebuild sealed desk files and compare bytes")
    ck.add_argument("--root", type=Path, required=True)
    ck.add_argument("--session", required=True)
    sn = sub.add_parser("send", help="send the session's latest sealed desk file to the dashboard")
    sn.add_argument("--root", type=Path, required=True)
    sn.add_argument("--session", required=True)
    sn.add_argument("--base", default=publish.DEFAULT_BASE)
    sn.add_argument("--dry-run", action="store_true", help="print the document instead of sending it")
    vf = sub.add_parser("verify", help="check sealed desk folders against their manifests")
    vf.add_argument("folders", type=Path, nargs="+")
    args = parser.parse_args(argv)
    if args.command == "seal":
        return _seal_cmd(args)
    if args.command == "send":
        return _send_cmd(args)
    if args.command == "check":
        if not _is_date(args.session):
            print(f"bad session {args.session!r}", file=sys.stderr)
            return EXIT_INPUT
        base = args.root / "data" / KIND
        folders = []
        for path in sorted(base.glob(f"{args.session}*")) if base.is_dir() else []:
            match = _FOLDER.fullmatch(path.name)
            if match and match.group(1) == args.session and path.is_dir():
                folders.append(path)
        if not folders:
            print(f"no sealed desk folder for {args.session}", file=sys.stderr)
            return EXIT_INPUT
        problems = [p for folder in folders for p in rebuild(folder)]
    else:
        problems = [p for folder in args.folders for p in verify(folder)]
    for problem in problems:
        print(problem, file=sys.stderr)
    if not problems:
        print("ok")
    return EXIT_MISMATCH if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
