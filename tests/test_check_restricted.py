import importlib.util
import subprocess
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
    [
        ".env",
        "src/.env",
        ".env.local",
        "x.PDF",
        "KILL",
        "docs/research/x.md",
        "research",
        "knowledge/a",
        ".wt-tree",
        ".wt-tree/a",
        "var",
        "var/x",
    ],
)
def test_blocked_everywhere(path: str) -> None:
    assert check.blocked(path, "main")
    assert check.blocked(path, "data")


@pytest.mark.parametrize("path", [".env.example", ".envrc", "prod.env.md", "src/options_funnel/x.py", "variables.md"])
def test_allowed(path: str) -> None:
    assert check.blocked(path, "main") is None


def test_evidence_only_on_data_branch() -> None:
    assert check.blocked("data/radar/2026-11-17.json", "main")
    assert check.blocked("data/radar/2026-11-17.json", "HEAD")
    assert check.blocked("data/radar/2026-11-17.json", "data") is None


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit(repo: Path, files: dict[str, str], message: str) -> str:
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def test_data_branch_may_change_only_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _git(tmp_path, "init", "-q", "-b", "main")
    main = _commit(tmp_path, {"README.md": "a"}, "main")
    _git(tmp_path, "checkout", "-q", "-b", "data")
    good = _commit(tmp_path, {"data/radar/x.json": "{}"}, "evidence")
    bad = _commit(tmp_path, {"README.md": "changed"}, "not evidence")
    monkeypatch.chdir(tmp_path)
    assert check.problems(good, "data", main) == []
    assert any("README.md" in p for p in check.problems(bad, "data", main))
    assert any("data/" in p for p in check.problems(good, "main", main))


def test_a_file_added_then_removed_is_caught_in_a_range(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, {"README.md": "a"}, "base")
    _commit(tmp_path, {"research/x.md": "secret"}, "add")
    (tmp_path / "research" / "x.md").unlink()
    tip = _commit(tmp_path, {}, "remove")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GATE_BRANCH", "main")
    monkeypatch.setenv("GATE_MAIN_REF", base)
    assert check.main(["--range", f"{base}..{tip}"]) == 1
    assert check.main(["--range", f"{tip}^!"]) == 0
