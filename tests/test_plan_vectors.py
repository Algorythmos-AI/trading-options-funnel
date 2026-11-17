import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("gen_plan_vectors", ROOT / "scripts" / "gen_plan_vectors.py")
assert SPEC and SPEC.loader
gen = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gen)


def test_vectors_are_deterministic_and_come_from_the_pinned_original(tmp_path: Path) -> None:
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    assert gen.main([str(a)]) == 0 and gen.main([str(b)]) == 0
    assert a.read_bytes() == b.read_bytes()
    doc = json.loads(a.read_text())
    assert len(doc["vectors"]) == 412
    assert doc["constants"] == {"ENTRY_ATR": 0.01, "STOP_ATR": 0.1, "ROOM": 2}
    assert doc["source"].endswith(gen.ORIGINAL_SHA256[:16])
    no_atr = [v for v in doc["vectors"] if v["input"]["atr"] in (None, 0.0)]
    assert no_atr and all(v["expect"]["entry"] is None and v["expect"]["t1"] is None for v in no_atr)
