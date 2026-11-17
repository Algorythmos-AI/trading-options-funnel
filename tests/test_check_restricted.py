import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "check_restricted", Path(__file__).resolve().parents[1] / "scripts" / "check_restricted.py"
)
assert SPEC and SPEC.loader
check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check)


@pytest.mark.parametrize(
    "path",
    [".env", "src/.env", ".env.local", "x.PDF", "KILL", "docs/research/x.md", "knowledge/a", ".wt-tree/a", "var/x"],
)
def test_blocked_everywhere(path: str) -> None:
    assert check.blocked(path, "main")
    assert check.blocked(path, "data")


@pytest.mark.parametrize("path", [".env.example", ".envrc", "prod.env.md", "src/options_funnel/x.py", "README.md"])
def test_allowed(path: str) -> None:
    assert check.blocked(path, "main") is None


def test_evidence_only_on_data_branch() -> None:
    assert check.blocked("data/radar/2026-11-17.json", "main")
    assert check.blocked("data/radar/2026-11-17.json", "HEAD")
    assert check.blocked("data/radar/2026-11-17.json", "data") is None
