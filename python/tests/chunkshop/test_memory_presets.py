"""Both memory presets must load into a valid CellConfig."""
from pathlib import Path
import yaml
from chunkshop.config import CellConfig

BASE = Path(__file__).resolve().parents[2] / "src/chunkshop/configs/memory"


def test_realtime_preset_valid():
    c = CellConfig(**yaml.safe_load((BASE / "realtime.yaml").read_text()))
    assert c.source.type == "session_staging" and c.source.mode == "realtime"
    assert c.framer.type == "identity"
    assert c.target.memory.tier == "provisional"


def test_consolidate_preset_valid():
    c = CellConfig(**yaml.safe_load((BASE / "consolidate.yaml").read_text()))
    assert c.source.mode == "consolidate"
    assert c.framer.type == "session_episode"
    assert c.chunker.type == "consolidation"
    assert c.target.memory.tier == "consolidated"
    assert c.target.memory.supersede is True
    paths = {p.path for p in c.target.promote_metadata}
    assert {"kind", "session_id", "tier", "namespace", "subject", "predicate",
            "object", "support_span", "confidence", "effective_from",
            "effective_to", "retracted", "retracted_at", "recorded_at",
            "source_chunk_seq", "extractor"} <= paths
