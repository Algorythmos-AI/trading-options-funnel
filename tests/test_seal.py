import json
from pathlib import Path

import pytest

from options_funnel.cli import EXIT_EXISTS, EXIT_INPUT, EXIT_MISMATCH, main
from options_funnel.replay import replay_focus
from options_funnel.seal import SealError, iv_rows_for, seal, verify

NIGHTS = Path(__file__).resolve().parent / "fixtures" / "focus_nights"
SESSIONS = sorted(p.stem for p in (NIGHTS / "levels").glob("*.json"))


def seal_night(root: Path, session: str, *extra: str) -> int:
    return main(
        [
            "seal-focus",
            "--root",
            str(root),
            "--levels",
            str(NIGHTS / "levels" / f"{session}.json"),
            "--iv-history",
            str(NIGHTS / "iv_history.csv"),
            "--focus",
            str(NIGHTS / "focus" / f"{session}.json"),
            *extra,
        ]
    )


@pytest.mark.parametrize("session", SESSIONS)
def test_every_real_night_seals_verifies_and_replays(tmp_path: Path, session: str) -> None:
    assert seal_night(tmp_path, session) == 0
    folder = tmp_path / "data" / "focus" / session
    assert verify(folder) == []
    assert replay_focus(folder) == []
    manifest = json.loads((folder / "MANIFEST.json").read_text())
    assert manifest["files"]["levels.json"]["size"] == (NIGHTS / "levels" / f"{session}.json").stat().st_size
    assert (folder / "focus.json").read_bytes() == (NIGHTS / "focus" / f"{session}.json").read_bytes()


def test_iv_rows_keep_only_the_built_from_session_byte_for_byte() -> None:
    source = (NIGHTS / "iv_history.csv").read_bytes()
    rows = iv_rows_for(source, "2026-11-11").decode().splitlines()
    assert rows[0].startswith("session,ticker")
    assert len(rows) > 1 and all(r.startswith("2026-11-11,") for r in rows[1:])
    assert all(r.encode() in source for r in rows)
    with pytest.raises(SealError):
        iv_rows_for(source, "1999-01-01")


def test_a_sealed_night_is_never_touched_again(tmp_path: Path) -> None:
    assert seal_night(tmp_path, SESSIONS[-1]) == 0
    folder = tmp_path / "data" / "focus" / SESSIONS[-1]
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    assert seal_night(tmp_path, SESSIONS[-1]) == EXIT_EXISTS
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == before
    assert seal_night(tmp_path, SESSIONS[-1], "--replaces", SESSIONS[-1]) == 0
    second = tmp_path / "data" / "focus" / f"{SESSIONS[-1]}.r2"
    assert json.loads((second / "MANIFEST.json").read_text())["replaces"] == SESSIONS[-1]
    assert seal_night(tmp_path, SESSIONS[-1], "--replaces", "2026-01-01") == EXIT_INPUT
    assert [p.name for p in (tmp_path / "data" / "focus").iterdir() if p.name.startswith(".")] == []


def test_tampering_is_caught(tmp_path: Path) -> None:
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


def test_replay_reports_a_different_pick(tmp_path: Path) -> None:
    session = SESSIONS[-1]
    doc = json.loads((NIGHTS / "focus" / f"{session}.json").read_text())
    doc["items"][0], doc["items"][1] = doc["items"][1], doc["items"][0]
    focus = tmp_path / "focus.json"
    focus.write_text(json.dumps(doc))
    args = ["seal-focus", "--root", str(tmp_path), "--levels", str(NIGHTS / "levels" / f"{session}.json")]
    args += ["--iv-history", str(NIGHTS / "iv_history.csv"), "--focus", str(focus)]
    assert main(args) == 0
    diffs = replay_focus(tmp_path / "data" / "focus" / session)
    assert diffs and diffs[0].startswith("names:")


def test_mismatched_focus_session_is_refused(tmp_path: Path) -> None:
    args = ["seal-focus", "--root", str(tmp_path), "--levels", str(NIGHTS / "levels" / f"{SESSIONS[0]}.json")]
    args += ["--iv-history", str(NIGHTS / "iv_history.csv"), "--focus", str(NIGHTS / "focus" / f"{SESSIONS[1]}.json")]
    assert main(args) == EXIT_INPUT
    assert not (tmp_path / "data").exists()


def test_radar_edition_seals_verbatim(tmp_path: Path) -> None:
    edition = tmp_path / "e.json"
    edition.write_bytes(b'{"schema":"stocksdelta/radar","scorecard":[{"hits":2}]}')
    assert main(["seal-radar", "--root", str(tmp_path), "--session", "2026-11-17", "--edition", str(edition)]) == 0
    folder = tmp_path / "data" / "radar" / "2026-11-17"
    assert (folder / "edition.json").read_bytes() == edition.read_bytes()
    assert verify(folder) == []
    assert main(["seal-radar", "--root", str(tmp_path), "--session", "17-11-2026", "--edition", str(edition)]) == 3


def test_wrong_file_set_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SealError):
        seal(tmp_path, "radar", "2026-11-17", {"other.json": b"{}"}, "t", {})
    with pytest.raises(SealError):
        seal(tmp_path, "nope", "2026-11-17", {}, "t", {})
