import json
import os
from collections.abc import Mapping
from pathlib import Path

import pytest

from options_funnel.cli import EXIT_INPUT, EXIT_MISMATCH, EXIT_SEALED_DIFFERENT, main
from options_funnel.replay import replay_focus
from options_funnel.seal import SealError, iv_rows_for, seal, verify

NIGHTS = Path(__file__).resolve().parent / "fixtures" / "focus_nights"
SESSIONS = sorted(p.stem for p in (NIGHTS / "levels").glob("*.json"))
IV = NIGHTS / "iv_history.csv"


def after_close(tmp: Path, session: str, change: Mapping[str, object] | None = None) -> Path:
    """The focus file as `focus.py pick` leaves it, before the pre-open pass adds `premarket`."""
    doc = json.loads((NIGHTS / "focus" / f"{session}.json").read_text())
    doc["premarket"] = None
    doc.update(change or {})
    path = tmp / f"focus-{session}.json"
    path.write_text(json.dumps(doc, indent=1))
    return path


def seal_night(root: Path, session: str, focus: Path | None = None, *extra: str) -> int:
    focus = focus or after_close(root, session)
    args = ["seal-focus", "--root", str(root), "--levels", str(NIGHTS / "levels" / f"{session}.json")]
    args += ["--iv-history", str(IV), "--focus", str(focus), "--routine", "trig_test", *extra]
    return main(args)


def folders(root: Path, kind: str = "focus") -> list[str]:
    return sorted(p.name for p in (root / "data" / kind).iterdir())


@pytest.mark.parametrize("session", SESSIONS)
def test_every_real_night_seals_verifies_and_replays(tmp_path: Path, session: str) -> None:
    focus = after_close(tmp_path, session)
    assert seal_night(tmp_path, session, focus) == 0
    folder = tmp_path / "data" / "focus" / session
    assert verify(folder) == []
    assert replay_focus(folder) == []
    manifest = json.loads((folder / "MANIFEST.json").read_text())
    assert (folder / "levels.json").read_bytes() == (NIGHTS / "levels" / f"{session}.json").read_bytes()
    assert (folder / "focus.json").read_bytes() == focus.read_bytes()
    prov = manifest["provenance"]
    assert prov["routine"] == "trig_test" and prov["built_from"] and prov["code_commit"]
    assert set(prov["sources"]) == {"levels", "iv_history", "focus"}


def test_resealing_the_same_bytes_is_a_no_op(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    focus = after_close(tmp_path, SESSIONS[-1])
    assert seal_night(tmp_path, SESSIONS[-1], focus) == 0
    assert seal_night(tmp_path, SESSIONS[-1], focus) == 0
    assert "already sealed, identical" in capsys.readouterr().out
    assert folders(tmp_path) == [SESSIONS[-1]]


def test_corrections_form_one_chain(tmp_path: Path) -> None:
    s = SESSIONS[-1]
    assert seal_night(tmp_path, s) == 0
    first = (tmp_path / "data" / "focus" / s / "focus.json").read_bytes()
    other = after_close(tmp_path, s, {"method": "changed"})
    assert seal_night(tmp_path, s, other) == EXIT_SEALED_DIFFERENT
    assert (tmp_path / "data" / "focus" / s / "focus.json").read_bytes() == first
    assert seal_night(tmp_path, s, other, "--correction") == 0
    third = after_close(tmp_path, s, {"method": "changed again"})
    assert seal_night(tmp_path, s, third, "--correction") == 0
    assert folders(tmp_path) == [s, f"{s}.r2", f"{s}.r3"]
    replaces = [
        json.loads((tmp_path / "data" / "focus" / f / "MANIFEST.json").read_text())["replaces"]
        for f in folders(tmp_path)
    ]
    assert replaces == [None, s, f"{s}.r2"]
    assert seal_night(tmp_path, s, third, "--correction") == 0  # the latest already holds these bytes
    assert folders(tmp_path) == [s, f"{s}.r2", f"{s}.r3"]


def test_a_correction_needs_an_existing_seal(tmp_path: Path) -> None:
    assert seal_night(tmp_path, SESSIONS[-1], None, "--correction") == EXIT_INPUT
    assert not (tmp_path / "data" / "focus").exists() or folders(tmp_path) == []


def test_a_focus_file_already_edited_by_the_pre_open_pass_is_refused(tmp_path: Path) -> None:
    session = SESSIONS[0]
    published = NIGHTS / "focus" / f"{session}.json"
    assert json.loads(published.read_text())["premarket"] is not None
    assert seal_night(tmp_path, session, published) == EXIT_INPUT
    assert not (tmp_path / "data").exists()


def test_mismatched_focus_session_is_refused(tmp_path: Path) -> None:
    assert seal_night(tmp_path, SESSIONS[0], after_close(tmp_path, SESSIONS[1])) == EXIT_INPUT
    assert not (tmp_path / "data").exists()


def test_tampering_and_symlinks_are_caught(tmp_path: Path) -> None:
    assert seal_night(tmp_path, SESSIONS[-1]) == 0
    folder = tmp_path / "data" / "focus" / SESSIONS[-1]
    doc = json.loads((folder / "focus.json").read_text())
    doc["items"][0]["score"] += 1
    (folder / "focus.json").write_text(json.dumps(doc))
    assert any("does not match" in p for p in verify(folder))
    assert main(["verify", str(folder)]) == EXIT_MISMATCH
    assert main(["replay-focus", str(folder)]) == EXIT_MISMATCH
    (folder / "extra.txt").write_text("x")
    assert any("not in the manifest" in p for p in verify(folder))
    (folder / "focus.json").unlink()
    os.symlink(tmp_path / "elsewhere.json", folder / "focus.json")
    assert any("not a regular file" in p for p in verify(folder))


def test_replay_reports_a_different_pick(tmp_path: Path) -> None:
    session = SESSIONS[-1]
    doc = json.loads((NIGHTS / "focus" / f"{session}.json").read_text())
    swapped = {"items": [doc["items"][1], doc["items"][0], *doc["items"][2:]]}
    assert seal_night(tmp_path, session, after_close(tmp_path, session, swapped)) == 0
    diffs = replay_focus(tmp_path / "data" / "focus" / session)
    assert diffs and diffs[0].startswith("names:")


def test_iv_rows_keep_the_session_bytes_unchanged() -> None:
    source = IV.read_bytes()
    rows = iv_rows_for(source, "2026-11-11")
    lines = rows.split(b"\n")
    assert lines[0].startswith(b"session,ticker")
    assert all(line.startswith(b"2026-11-11,") for line in lines[1:] if line)
    assert all(line in source for line in lines)
    with pytest.raises(SealError):
        iv_rows_for(source, "1999-01-01")


def test_iv_rows_edge_cases() -> None:
    header = b"session,ticker,call_vol\r\n"
    body = b'"2026-11-11",SPY,1\r\n2026-11-10,QQQ,2\r\n2026-11-11,QQQ,3 \xe2\x80\xa8 x\r\n'
    out = iv_rows_for(b"\xef\xbb\xbf" + header + body, "2026-11-11")
    assert out == b"\xef\xbb\xbf" + header + b'"2026-11-11",SPY,1\r\n2026-11-11,QQQ,3 \xe2\x80\xa8 x\r\n'
    with pytest.raises(SealError, match="two rows"):
        iv_rows_for(header + b"2026-11-11,SPY,1\n2026-11-11,SPY,2\n", "2026-11-11")
    with pytest.raises(SealError, match="header"):
        iv_rows_for(b"ticker,session\n", "2026-11-11")


def test_radar_edition_seals_verbatim_with_its_file_date(tmp_path: Path) -> None:
    edition = tmp_path / "2026-11-17.json"
    edition.write_bytes(b'{"schema":"stocksdelta/radar","scorecard":[{"hits":2}]}')
    base = ["seal-radar", "--root", str(tmp_path), "--edition", str(edition)]
    assert main([*base, "--http-status", "200", "--sent-at", "2026-11-17T11:12:40Z"]) == 0
    folder = tmp_path / "data" / "radar" / "2026-11-17"
    assert (folder / "edition.json").read_bytes() == edition.read_bytes()
    assert verify(folder) == []
    prov = json.loads((folder / "MANIFEST.json").read_text())["provenance"]
    assert prov["http_status"] == 200 and prov["sent_at"] == "2026-11-17T11:12:40Z"
    assert main([*base, "--session", "2026-11-16"]) == EXIT_INPUT
    bad = tmp_path / "latest.json"
    bad.write_bytes(b"{}")
    assert main(["seal-radar", "--root", str(tmp_path), "--edition", str(bad)]) == EXIT_INPUT


def test_wrong_file_set_and_overflow_are_refused(tmp_path: Path) -> None:
    with pytest.raises(SealError):
        seal(tmp_path, "radar", "2026-11-17", {"other.json": b"{}"}, "t", {})
    with pytest.raises(SealError):
        seal(tmp_path, "nope", "2026-11-17", {}, "t", {})
    seal(tmp_path, "radar", "2026-11-17", {"edition.json": b"0"}, "t", {})
    for n in range(1, 99):
        seal(tmp_path, "radar", "2026-11-17", {"edition.json": str(n).encode()}, "t", {}, correction=True)
    assert len(folders(tmp_path, "radar")) == 99  # the session plus r2..r99
    with pytest.raises(SealError, match="corrections"):
        seal(tmp_path, "radar", "2026-11-17", {"edition.json": b"x"}, "t", {}, correction=True)
    assert all(verify(tmp_path / "data" / "radar" / f) == [] for f in folders(tmp_path, "radar"))
