import json
from pathlib import Path

import pytest

from options_funnel.candidates import load_jsonl
from options_funnel.cli import main

NIGHTS = Path(__file__).resolve().parent / "fixtures" / "focus_nights"


def test_focus_pick_writes_records_and_document(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out, focus = tmp_path / "c.jsonl", tmp_path / "f.json"
    args = ["focus-pick", str(NIGHTS / "levels" / "2026-11-12.json"), str(NIGHTS / "iv_history.csv")]
    assert main([*args, "--candidates", str(out), "--focus", str(focus)]) == 0
    assert len(list(load_jsonl(out.read_text()))) == 12
    published = json.loads((NIGHTS / "focus" / "2026-11-12.json").read_text())
    assert json.loads(focus.read_text())["items"] == published["items"]
    assert "#1 MSFT" in capsys.readouterr().out
    assert main([*args, "--candidates", str(out)]) == 1
