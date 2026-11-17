import hashlib
import hmac
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from options_funnel.blind import assert_blind
from options_funnel.cli import EXIT_SEND, main
from options_funnel.health import build
from options_funnel.publish import PublishError, send, sign

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
NIGHTS = HERE / "fixtures" / "focus_nights"
SCHEMA = json.loads((ROOT / "contracts" / "funnel.schema.json").read_text())
SESSION = "2026-11-12"


def seal_night(root: Path, session: str = SESSION) -> None:
    doc = json.loads((NIGHTS / "focus" / f"{session}.json").read_text())
    doc["premarket"] = None
    focus = root / f"focus-{session}.json"
    focus.write_text(json.dumps(doc))
    args = ["seal-focus", "--root", str(root), "--levels", str(NIGHTS / "levels" / f"{session}.json")]
    assert main([*args, "--iv-history", str(NIGHTS / "iv_history.csv"), "--focus", str(focus)]) == 0


def seal_radar(root: Path, date: str, lists: list[dict[str, Any]]) -> None:
    edition = root / f"{date}.json"
    edition.write_text(json.dumps({"schema": "stocksdelta/radar", "lists": lists}))
    assert main(["seal-radar", "--root", str(root), "--edition", str(edition)]) == 0


def test_the_contract_copy_is_the_pinned_one() -> None:
    lock = json.loads((ROOT / "contracts" / "funnel.contract.lock.json").read_text())
    data = (ROOT / "contracts" / "funnel.schema.json").read_bytes()
    assert hashlib.sha256(data).hexdigest() == lock["sha256"]
    assert lock["source_path"] == "dashboard/src/lib/funnel.schema.json"


def test_a_sealed_night_gives_a_valid_document(tmp_path: Path) -> None:
    seal_night(tmp_path)
    seal_radar(tmp_path, "2026-11-11", [{"name": "A", "symbols": ["NVDA", "AMD"]}, {"name": "B", "symbols": ["AMD"]}])
    doc = build(tmp_path, SESSION, "2026-11-11T22:20:00Z", "a" * 40)
    jsonschema.validate(doc, SCHEMA)
    assert_blind(doc)
    radar, focus = doc["stages"]
    assert radar == {
        "stage": "radar",
        "evidence": "sealed",
        "sealed_at": radar["sealed_at"],
        "corrections": 0,
        "names_judged": None,
        "names_kept": 2,
        "flags": [],
        "replay": "not_run",
    }
    assert focus["evidence"] == "sealed" and focus["replay"] == "match"
    assert focus["names_judged"] == 12 and focus["names_kept"] == 4
    text = json.dumps(doc)
    for symbol in ("NVDA", "AMD", "MSFT", "SPY"):
        assert symbol not in text


def test_the_same_evidence_gives_the_same_run(tmp_path: Path) -> None:
    seal_night(tmp_path)
    a = build(tmp_path, SESSION, "2026-11-11T22:20:00Z", None)
    b = build(tmp_path, SESSION, "2026-11-11T23:00:00Z", None)
    assert a["run_id"] == b["run_id"] and a["as_of"] != b["as_of"]


def test_nothing_sealed_is_missing_and_still_valid(tmp_path: Path) -> None:
    doc = build(tmp_path, SESSION, "2026-11-11T22:20:00Z", None)
    jsonschema.validate(doc, SCHEMA)
    assert [s["evidence"] for s in doc["stages"]] == ["missing", "missing"]


def test_broken_evidence_says_so(tmp_path: Path) -> None:
    seal_night(tmp_path)
    (tmp_path / "data" / "focus" / SESSION / "focus.json").write_text("{}")
    doc = build(tmp_path, SESSION, "2026-11-11T22:20:00Z", None)
    jsonschema.validate(doc, SCHEMA)
    assert doc["stages"][1]["evidence"] == "broken"


class _Dashboard(BaseHTTPRequestHandler):
    accepts: int | None = 1
    received: list[dict[str, Any]] = []

    def log_message(self, *args: object) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        body = json.dumps({"editions": {"funnel": {"accepts": self.accepts}}} if self.accepts else {"editions": {}})
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body.encode())

    def do_POST(self) -> None:  # noqa: N802
        raw = self.rfile.read(int(self.headers["content-length"]))
        ts = int(self.headers["x-wt-timestamp"])
        ok = hmac.compare_digest(self.headers["x-wt-signature"], sign("s3cret", ts, raw))
        self.received.append({"ok": ok, "key": self.headers["x-wt-key-id"], "doc": json.loads(raw)})
        self.send_response(200 if ok else 401)
        self.end_headers()
        self.wfile.write(json.dumps({"status": "stored"}).encode())


@pytest.fixture
def dashboard(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    _Dashboard.received = []
    _Dashboard.accepts = 1
    server = HTTPServer(("127.0.0.1", 0), _Dashboard)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("RADAR_INGEST_SECRET", "s3cret")
    monkeypatch.delenv("VERCEL_AUTOMATION_BYPASS_SECRET", raising=False)
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_send_signs_with_the_research_key(tmp_path: Path, dashboard: str) -> None:
    seal_night(tmp_path)
    doc = build(tmp_path, SESSION, "2026-11-11T22:20:00Z", None)
    sent = send(doc, dashboard)
    assert sent.status == 200 and sent.outcome == "stored"
    assert _Dashboard.received == [{"ok": True, "key": "radar", "doc": doc}]


def test_send_refuses_a_site_that_does_not_know_the_kind(tmp_path: Path, dashboard: str) -> None:
    _Dashboard.accepts = None
    with pytest.raises(PublishError, match="does not accept"):
        send(build(tmp_path, SESSION, "2026-11-11T22:20:00Z", None), dashboard)
    assert _Dashboard.received == []


def test_send_needs_the_secret(tmp_path: Path, dashboard: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RADAR_INGEST_SECRET")
    with pytest.raises(PublishError, match="RADAR_INGEST_SECRET"):
        send(build(tmp_path, SESSION, "2026-11-11T22:20:00Z", None), dashboard)


def test_cli_sends_and_reports(tmp_path: Path, dashboard: str, capsys: pytest.CaptureFixture[str]) -> None:
    seal_night(tmp_path)
    args = ["funnel-health", "--root", str(tmp_path), "--session", SESSION, "--base", dashboard]
    assert main([*args, "--send"]) == 0
    assert "sent funnel-2026-11-12-" in capsys.readouterr().out
    _Dashboard.accepts = None
    assert main([*args, "--send"]) == EXIT_SEND
    out = tmp_path / "doc.json"
    assert main([*args, "--out", str(out)]) == 0
    jsonschema.validate(json.loads(out.read_text()), SCHEMA)
