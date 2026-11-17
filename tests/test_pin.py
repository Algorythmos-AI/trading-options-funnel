from pathlib import Path

import pytest

from options_funnel.pin import read_pin

ROOT = Path(__file__).resolve().parents[1]


def test_repository_pin_is_valid() -> None:
    assert len(read_pin(ROOT / "wt.pin")) == 40


@pytest.mark.parametrize(
    "text", ["", "abc\n", "6bee358cd2b698f48303c64345347dea71862650", "A" * 40 + "\n", "a" * 40 + "\n\n"]
)
def test_bad_pins_are_refused(tmp_path: Path, text: str) -> None:
    pin = tmp_path / "wt.pin"
    pin.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        read_pin(pin)
