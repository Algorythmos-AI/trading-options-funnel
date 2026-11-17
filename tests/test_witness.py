import json
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from options_funnel import cli
from options_funnel.cli import EXIT_INPUT, EXIT_SEND, main
from options_funnel.seal import verify
from options_funnel.witness import WitnessError, after_open, check_directory_file, check_listing, listing_signature

HERE = Path(__file__).resolve().parent
DIRECTORY = HERE / "fixtures" / "directory"
DATE = "2026-11-18"


def listing(date: str = DATE) -> dict[str, Any]:
    row = {
        "path": f"radar/copies/{date}/20261118T111240123Z-radar-a.json",
        "received_at": "2026-11-18T11:12:40.123Z",
        "run_id": "radar-a",
        "copy_sha256": "a" * 64,
        "edition_sha256": "b" * 64,
    }
    return {"schema": "stocksdelta/radar-copies", "version": 1, "date": date, "copies": [row]}


def test_the_directory_files_are_checked() -> None:
    for name in ("nasdaqlisted.txt", "otherlisted.txt"):
        check_directory_file(name, (DIRECTORY / name).read_bytes())
    with pytest.raises(WitnessError, match="header"):
        check_directory_file("nasdaqlisted.txt", (DIRECTORY / "otherlisted.txt").read_bytes())
    with pytest.raises(WitnessError, match="ETF"):
        check_directory_file("otherlisted.txt", b"ACT Symbol|Name\nX|y\nFile Creation Time: 1\n")
    with pytest.raises(WitnessError, match="creation-time"):
        check_directory_file("otherlisted.txt", b"ACT Symbol|ETF\nX|N\nY|N\n")


def test_the_listing_is_sealed_only_after_the_open() -> None:
    assert not after_open(DATE, datetime(2026, 11, 18, 14, 29, tzinfo=UTC))  # 09:29 New York
    assert after_open(DATE, datetime(2026, 11, 18, 14, 30, tzinfo=UTC))
    assert not after_open("2026-07-01", datetime(2026, 7, 1, 13, 29, tzinfo=UTC))  # summer: 09:29 New York


def test_a_listing_must_be_for_its_date_and_well_formed() -> None:
    check_listing(json.dumps(listing()).encode(), DATE)
    with pytest.raises(WitnessError):
        check_listing(json.dumps(listing("2026-11-17")).encode(), DATE)
    bad = listing()
    bad["copies"][0]["extra"] = 1
    with pytest.raises(WitnessError):
        check_listing(json.dumps(bad).encode(), DATE)
    with pytest.raises(WitnessError):
        check_listing(b"[]", DATE)


def test_the_signature_binds_the_date() -> None:
    a = listing_signature("s", 100, DATE)
    assert a.startswith("sha256=") and a != listing_signature("s", 100, "2026-11-19")


class _Dashboard(BaseHTTPRequestHandler):
    body: bytes = b""
    status = 200
    seen: list[dict[str, str]] = []

    def log_message(self, *args: object) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        self.seen.append(
            {
                "path": self.path,
                "key": self.headers.get("x-wt-key-id", ""),
                "sig": self.headers.get("x-wt-signature", ""),
                "ts": self.headers.get("x-wt-timestamp", ""),
            }
        )
        self.send_response(self.status)
        self.end_headers()
        self.wfile.write(self.body)


@pytest.fixture
def dashboard(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    _Dashboard.seen = []
    _Dashboard.status = 200
    _Dashboard.body = json.dumps(listing()).encode()
    server = HTTPServer(("127.0.0.1", 0), _Dashboard)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("RADAR_INGEST_SECRET", "s3cret")
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def _after_open(monkeypatch: pytest.MonkeyPatch, open_: bool) -> None:
    monkeypatch.setattr(cli, "after_open", lambda date, now: open_)


def test_seal_receipts_signs_fetches_and_seals_the_bytes_as_served(
    tmp_path: Path, dashboard: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _after_open(monkeypatch, True)
    assert (
        main(["seal-receipts", "--root", str(tmp_path), "--date", DATE, "--base", dashboard, "--routine", "trig_x"])
        == 0
    )
    seen = _Dashboard.seen[0]
    assert seen["path"] == f"/api/research/radar-copies?date={DATE}" and seen["key"] == "radar"
    assert seen["sig"] == listing_signature("s3cret", int(seen["ts"]), DATE)
    folder = tmp_path / "data" / "radar-receipts" / DATE
    assert (folder / "listing.json").read_bytes() == _Dashboard.body
    assert verify(folder) == []
    assert json.loads((folder / "MANIFEST.json").read_text())["provenance"]["routine"] == "trig_x"
    assert (
        main(["seal-receipts", "--root", str(tmp_path), "--date", DATE, "--base", dashboard]) == 0
    )  # identical: no-op


def test_seal_receipts_refuses_before_the_open_and_on_a_refused_listing(
    tmp_path: Path, dashboard: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _after_open(monkeypatch, False)
    assert main(["seal-receipts", "--root", str(tmp_path), "--date", DATE, "--base", dashboard]) == EXIT_INPUT
    _after_open(monkeypatch, True)
    _Dashboard.status = 401
    assert main(["seal-receipts", "--root", str(tmp_path), "--date", DATE, "--base", dashboard]) == EXIT_SEND
    _Dashboard.status = 200
    _Dashboard.body = json.dumps(listing("2026-11-17")).encode()
    assert main(["seal-receipts", "--root", str(tmp_path), "--date", DATE, "--base", dashboard]) == EXIT_SEND
    assert not (tmp_path / "data").exists()
