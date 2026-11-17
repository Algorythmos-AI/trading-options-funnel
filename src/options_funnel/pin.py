"""The trading-lab-research commit this repository reads (`wt.pin`)."""

from __future__ import annotations

import re
from pathlib import Path

REPO = "Algorythmos-AI/trading-lab-research"
_SHA = re.compile(r"^[0-9a-f]{40}$")


def read_pin(path: Path) -> str:
    """The pinned commit: one line of 40 lowercase hex characters, nothing else."""
    text = path.read_text(encoding="utf-8")
    sha = text.strip()
    if text != sha + "\n" or not _SHA.match(sha):
        raise ValueError(f"{path} must hold exactly one 40-character lowercase commit and a newline")
    return sha
