from chunkshop.framers import IdentityFramer, load_framer
from chunkshop.framers.base import DocFramer
from chunkshop.config import IdentityFramerConfig
from chunkshop.sources.base import Document


def test_identity_framer_passes_through():
    framer = IdentityFramer()
    doc = Document(id="d1", content="hello world", title="t", metadata={"k": "v"})
    result = framer.frame(doc)
    assert len(result) == 1
    assert result[0].id == "d1"
    assert result[0].content == "hello world"
    assert result[0].metadata.get("framer") == "identity"
    assert result[0].metadata.get("frame_seq") == 0
    assert result[0].metadata.get("k") == "v"


def test_identity_framer_satisfies_protocol():
    framer: DocFramer = IdentityFramer()
    assert hasattr(framer, "frame")


def test_load_framer_dispatches_identity():
    framer = load_framer(IdentityFramerConfig())
    assert isinstance(framer, IdentityFramer)


from chunkshop.framers.heading_boundary import HeadingBoundaryFramer
from chunkshop.config import HeadingBoundaryFramerConfig


def test_heading_boundary_splits_on_h2():
    raw = Document(
        id="d1",
        content="# Title\n\nIntro.\n\n## Section A\n\nBody A.\n\n## Section B\n\nBody B.",
        title="Doc",
        metadata={},
    )
    framer = HeadingBoundaryFramer(HeadingBoundaryFramerConfig(
        type="heading_boundary",
        pattern=r"^##\s",
    ))
    out = framer.frame(raw)
    assert len(out) >= 2
    section_frames = [d for d in out if d.title in ("Section A", "Section B")]
    assert len(section_frames) == 2
    for i, d in enumerate(out):
        assert d.metadata["framer"] == "heading_boundary"
        assert d.metadata["frame_seq"] == i


def test_heading_boundary_no_headings_returns_single_frame():
    raw = Document(id="d1", content="No headings here at all.", title="t", metadata={})
    framer = HeadingBoundaryFramer(HeadingBoundaryFramerConfig(type="heading_boundary"))
    out = framer.frame(raw)
    assert len(out) == 1
    assert out[0].content == "No headings here at all."


def test_heading_boundary_preserves_preamble():
    raw = Document(
        id="d1",
        content="Preamble before any heading.\n\n# H1\n\nBody A.\n\n# H2\n\nBody B.",
        title="t", metadata={},
    )
    framer = HeadingBoundaryFramer(HeadingBoundaryFramerConfig(
        type="heading_boundary", pattern=r"^#\s",
    ))
    out = framer.frame(raw)
    # Preamble is frame 0, then H1 and H2 as frames 1 and 2.
    assert len(out) == 3
    assert "Preamble" in out[0].content
    assert out[1].title == "H1"
    assert out[2].title == "H2"


def test_heading_boundary_metadata_isolation():
    """Mutating one framed doc's metadata must not bleed to siblings."""
    raw = Document(id="d1", content="# A\n\nbody A.\n\n# B\n\nbody B.",
                   title="t", metadata={"keep": "me"})
    framer = HeadingBoundaryFramer(HeadingBoundaryFramerConfig(
        type="heading_boundary", pattern=r"^#\s",
    ))
    frames = framer.frame(raw)
    assert len(frames) >= 2
    frames[0].metadata["mutation"] = "test"
    assert "mutation" not in frames[1].metadata
    assert "mutation" not in raw.metadata


from chunkshop.framers.regex_boundary import RegexBoundaryFramer
from chunkshop.config import RegexBoundaryFramerConfig


def test_regex_boundary_medical_topic_split():
    """Simulates the pg-raggraph medical corpus pattern: 'About <topic>' separators."""
    content = (
        "About Lupus. Lupus is an autoimmune disease. It affects joints. "
        "About Diabetes. Diabetes is a metabolic disorder. Insulin management matters. "
        "About Asthma. Asthma narrows airways. Triggers vary by patient."
    )
    raw = Document(id="med", content=content, title="Medical", metadata={})
    framer = RegexBoundaryFramer(RegexBoundaryFramerConfig(
        type="regex_boundary",
        split_pattern=r"(?:^|(?<=[.?!]\s))About\s+",
        title_pattern=r"About\s+([^.?]{3,80})",
    ))
    out = framer.frame(raw)
    assert len(out) == 3
    titles = {d.title for d in out}
    assert any("Lupus" in t for t in titles)
    assert any("Diabetes" in t for t in titles)
    assert any("Asthma" in t for t in titles)
    for i, d in enumerate(out):
        assert d.metadata["framer"] == "regex_boundary"
        assert d.metadata["frame_seq"] == i


def test_regex_boundary_no_match_returns_single_frame():
    raw = Document(id="d1", content="No boundaries here.", title="t", metadata={})
    framer = RegexBoundaryFramer(RegexBoundaryFramerConfig(
        type="regex_boundary", split_pattern=r"SPLIT",
    ))
    out = framer.frame(raw)
    assert len(out) == 1


def test_regex_boundary_invalid_pattern_rejected_at_config_load():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        RegexBoundaryFramerConfig(
            type="regex_boundary",
            split_pattern=r"[unclosed",  # invalid regex
        )


import json as _json
from chunkshop.framers.jsonpath import JSONPathFramer
from chunkshop.config import JSONPathFramerConfig


def test_jsonpath_list_expansion():
    payload = {
        "meta": {"source": "api"},
        "items": [
            {"id": "a", "body": "first doc body"},
            {"id": "b", "body": "second doc body"},
            {"id": "c", "body": "third doc body"},
        ],
    }
    raw = Document(id="bundle", content=_json.dumps(payload), title="Bundle", metadata={})
    framer = JSONPathFramer(JSONPathFramerConfig(
        type="jsonpath",
        row_path="items.*",
        title_path="id",
        body_path="body",
    ))
    out = framer.frame(raw)
    assert len(out) == 3
    assert out[0].title == "a"
    assert "first doc body" in out[0].content
    for i, d in enumerate(out):
        assert d.metadata["framer"] == "jsonpath"
        assert d.metadata["frame_seq"] == i


def test_jsonpath_missing_row_path_returns_empty():
    raw = Document(id="bundle", content='{"other": []}', title="t", metadata={})
    framer = JSONPathFramer(JSONPathFramerConfig(
        type="jsonpath", row_path="items.*", body_path="body",
    ))
    assert framer.frame(raw) == []


def test_jsonpath_invalid_json_raises():
    import pytest
    raw = Document(id="bundle", content="not-json-at-all", title="t", metadata={})
    framer = JSONPathFramer(JSONPathFramerConfig(
        type="jsonpath", row_path="items.*", body_path="body",
    ))
    with pytest.raises(ValueError, match="JSON"):
        framer.frame(raw)
