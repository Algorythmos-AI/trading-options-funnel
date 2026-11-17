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
        "lost": False,
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
        check_directory_file("otherlisted.txt", b"ACT Symbol|CQS Symbol|Test Issue\nX|X|N\nFile Creation Time: 1\n")
    with pytest.raises(WitnessError, match="creation-time"):
        check_directory_file("otherlisted.txt", b"ACT Symbol|CQS Symbol|Test Issue|ETF\nX|X|N|N\nY|Y|N|N\n")
    # Bytes outside ASCII are kept as served, not refused.
    check_directory_file(
        "nasdaqlisted.txt", (DIRECTORY / "nasdaqlisted.txt").read_bytes().replace(b"Apple", b"App\xe9")
    )


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
    lost = listing()
    lost["copies"][0].update({"lost": True, "copy_sha256": None, "edition_sha256": None})
    lost["copies"][0]["path"] = lost["copies"][0]["path"].replace(".json", ".lost.json")
    check_listing(json.dumps(lost).encode(), DATE)
    lost["copies"][0]["copy_sha256"] = "a" * 64
    with pytest.raises(WitnessError, match="lost"):
        check_listing(json.dumps(lost).encode(), DATE)


def test_the_signature_binds_the_date() -> None:
    a = listing_signature("s", 100, DATE)
    assert a.startswith("sha256=") and a != listing_signature("s", 100, "2026-11-19")


def test_the_signature_matches_one_computed_by_node_as_the_dashboard_does() -> None:
    # node: createHmac("sha256", "s3cret")
    #   .update("1795000000.").update("GET /api/research/radar-copies?date=2026-11-18")
    want = "sha256=2d89bb2b17577a1ec1f8bf7465f51f95623a93df504678cd134e45c4bcd7ca40"
    assert listing_signature("s3cret", 1795000000, "2026-11-18") == want


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"received_at": "2026-11-18T11:12:40Z"}, "ISO"),
        ({"run_id": ""}, "run id"),
        ({"copy_sha256": "A" * 64}, "hashes"),
        ({"path": f"radar/copies/{DATE}/x.lost.json"}, "lost flag"),
    ],
)
def test_listing_rows_are_checked_closely(change: dict[str, Any], match: str) -> None:
    doc = listing()
    doc["copies"][0].update(change)
    with pytest.raises(WitnessError, match=match):
        check_listing(json.dumps(doc).encode(), DATE)


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


def test_seal_receipts_records_which_rows_match_the_sealed_edition(
    tmp_path: Path, dashboard: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _after_open(monkeypatch, True)
    edition = tmp_path / f"{DATE}.json"
    edition.write_bytes(b'{"x":1}')
    args = ["seal-radar", "--root", str(tmp_path), "--edition", str(edition), "--skill", str(DIRECTORY / "skill.bin")]
    assert main([*args, "--directory", str(DIRECTORY)]) == 0
    doc = listing()
    doc["copies"][0]["edition_sha256"] = __import__("hashlib").sha256(b'{"x":1}').hexdigest()
    _Dashboard.body = json.dumps(doc).encode()
    assert main(["seal-receipts", "--root", str(tmp_path), "--date", DATE, "--base", dashboard]) == 0
    prov = json.loads((tmp_path / "data" / "radar-receipts" / DATE / "MANIFEST.json").read_text())["provenance"]
    assert prov["rows_matching_sealed_edition"] == ["radar-a"]
    # A later, different listing for the same date is reported, never sealed as a correction.
    doc["copies"].append(
        {**doc["copies"][0], "path": doc["copies"][0]["path"].replace("radar-a", "radar-b"), "run_id": "radar-b"}
    )
    _Dashboard.body = json.dumps(doc).encode()
    assert main(["seal-receipts", "--root", str(tmp_path), "--date", DATE, "--base", dashboard]) == 6
    assert sorted(p.name for p in (tmp_path / "data" / "radar-receipts").iterdir()) == [DATE]


def test_seal_receipts_refuses_a_day_that_is_not_a_session(
    tmp_path: Path, dashboard: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _after_open(monkeypatch, True)
    for day in ("2026-11-26", "2026-11-21"):  # Thanksgiving, a Saturday
        assert main(["seal-receipts", "--root", str(tmp_path), "--date", day, "--base", dashboard]) == EXIT_INPUT
    assert _Dashboard.seen == []


def test_seal_receipts_retries_a_server_error(tmp_path: Path, dashboard: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _after_open(monkeypatch, True)
    monkeypatch.setattr("options_funnel.witness.time.sleep", lambda _s: None)
    _Dashboard.status = 503
    assert main(["seal-receipts", "--root", str(tmp_path), "--date", DATE, "--base", dashboard]) == EXIT_SEND
    assert len(_Dashboard.seen) == 3
    _Dashboard.seen = []
    _Dashboard.status = 404
    assert main(["seal-receipts", "--root", str(tmp_path), "--date", DATE, "--base", dashboard]) == EXIT_SEND
    assert len(_Dashboard.seen) == 1


def test_seal_radar_seals_the_edition_without_a_directory_when_it_cannot_be_fetched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def down() -> dict[str, bytes]:
        raise WitnessError("could not fetch nasdaqlisted.txt: HTTP 403")

    monkeypatch.setattr(cli, "fetch_directory", down)
    edition = tmp_path / f"{DATE}.json"
    edition.write_bytes(b'{"x":1}')
    args = ["seal-radar", "--root", str(tmp_path), "--edition", str(edition), "--skill", str(DIRECTORY / "skill.bin")]
    assert main([*args, "--fetch-directory"]) == 0
    folder = tmp_path / "data" / "radar" / DATE
    assert sorted(p.name for p in folder.iterdir()) == ["MANIFEST.json", "edition.json"]
    assert verify(folder) == []
    prov = json.loads((folder / "MANIFEST.json").read_text())["provenance"]
    assert prov["directory_source"].startswith("unavailable") and prov["directory_fetched_at"] is None


def test_seal_radar_rerun_of_the_same_edition_does_not_fetch_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    def fetch() -> dict[str, bytes]:
        calls.append(1)
        return {n: (DIRECTORY / n).read_bytes() for n in ("nasdaqlisted.txt", "otherlisted.txt")}

    monkeypatch.setattr(cli, "fetch_directory", fetch)
    edition = tmp_path / f"{DATE}.json"
    edition.write_bytes(b'{"x":1}')
    args = ["seal-radar", "--root", str(tmp_path), "--edition", str(edition), "--skill", str(DIRECTORY / "skill.bin")]
    assert main([*args, "--fetch-directory"]) == 0
    assert main([*args, "--fetch-directory"]) == 0
    assert calls == [1]
    assert sorted(p.name for p in (tmp_path / "data" / "radar").iterdir()) == [DATE]


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
