from chunkshop.chunkers import load_chunker
from chunkshop.config import (
    SentenceAwareChunker,
    FixedOverlapChunker,
    HierarchyChunker,
    NeighborExpandChunker,
)
from chunkshop.sources.base import Document


def _doc(content: str, id: str = "d1", title: str | None = None) -> Document:
    return Document(id=id, content=content, title=title)


def test_sentence_aware_produces_chunks_with_doc_id_and_sequence():
    chunker = load_chunker(SentenceAwareChunker())
    chunks = chunker.chunk(_doc("Hello world.\n\nSecond paragraph here."))
    assert len(chunks) >= 1
    assert all(c.doc_id == "d1" for c in chunks)
    assert chunks[0].seq_num == 0


def test_fixed_overlap_windows():
    words = " ".join([f"w{i}" for i in range(600)])
    chunker = load_chunker(FixedOverlapChunker(type="fixed_overlap", window_words=300, step_words=150))
    chunks = chunker.chunk(_doc(words))
    # 600 words, window=300, step=150 -> starts at 0, 150, 300 -> 3 windows
    assert len(chunks) == 3
    first_words = chunks[0].embedded_content.split()
    assert first_words[0] == "w0" and first_words[-1] == "w299"
    second_words = chunks[1].embedded_content.split()
    assert second_words[0] == "w150"


def test_hierarchy_prefixes_heading():
    # bodies must exceed min_section_chars (default 100); keep them well over to avoid drift
    body_a = "alpha body text that is unambiguously longer than one hundred characters so the min_section_chars filter leaves it intact."
    body_b = "beta body text that is unambiguously longer than one hundred characters so the min_section_chars filter leaves it intact."
    md = f"# Section One\n\n{body_a}\n\n# Section Two\n\n{body_b}"
    assert len(body_a) > 100 and len(body_b) > 100  # self-check: keep future edits honest
    chunker = load_chunker(HierarchyChunker(type="hierarchy"))
    chunks = chunker.chunk(_doc(md))
    assert len(chunks) == 2
    assert chunks[0].embedded_content.startswith("Section One")
    assert "alpha body text" in chunks[0].embedded_content
    assert "alpha body text" in chunks[0].original_content
    assert not chunks[0].original_content.startswith("Section One")


def test_hierarchy_splits_oversized_sections():
    # One H1 with a 10 KB body
    body = ("This is a paragraph that talks about medical conditions. " * 30).strip()
    paragraphs = [body] * 10
    big_section = "\n\n".join(paragraphs)
    assert len(big_section) > 10_000
    md = f"# About Bladder Cancer\n\n{big_section}"
    chunker = load_chunker(HierarchyChunker(type="hierarchy", max_chars=2000))
    chunks = chunker.chunk(_doc(md))
    assert len(chunks) >= 5
    for c in chunks:
        # embedded_content includes the heading prefix (prefix_heading default True)
        # so the effective content limit is max_chars + heading overhead; check original
        assert len(c.original_content) <= 2000
        assert c.metadata["heading"] == "About Bladder Cancer"
    # section_part should be monotonically increasing from 0
    parts = [c.metadata.get("section_part") for c in chunks]
    assert parts == list(range(len(chunks)))


def test_hierarchy_non_oversized_sections_still_work():
    # Same body layout as the classic heading test — none oversized
    body_a = "alpha body text that is unambiguously longer than one hundred characters so the min_section_chars filter leaves it intact."
    body_b = "beta body text that is unambiguously longer than one hundred characters so the min_section_chars filter leaves it intact."
    md = f"# Section One\n\n{body_a}\n\n# Section Two\n\n{body_b}"
    chunker = load_chunker(HierarchyChunker(type="hierarchy"))
    chunks = chunker.chunk(_doc(md))
    assert len(chunks) == 2
    # Sections not split -> section_part is 0 on each
    for c in chunks:
        assert c.metadata.get("section_part") == 0


def test_sentence_aware_respects_configured_max_chars():
    # 10 KB doc with many sentences
    sentences = [f"Sentence number {i} with some filler words here." for i in range(400)]
    doc_text = " ".join(sentences)
    assert len(doc_text) > 10_000
    chunker = load_chunker(SentenceAwareChunker(max_chars=500, min_chars=50))
    chunks = chunker.chunk(_doc(doc_text))
    assert len(chunks) >= 10
    for c in chunks:
        assert len(c.embedded_content) <= 500
        assert len(c.original_content) <= 500


def test_neighbor_expand_wraps_base():
    chunker = load_chunker(
        NeighborExpandChunker(
            type="neighbor_expand",
            base=FixedOverlapChunker(type="fixed_overlap", window_words=50, step_words=50),
            window=1,
        )
    )
    words = " ".join([f"w{i}" for i in range(150)])
    chunks = chunker.chunk(_doc(words))
    assert len(chunks) == 3
    middle = chunks[1].embedded_content
    assert "w0" in middle and "w50" in middle and "w100" in middle
    assert chunks[1].original_content.split()[0] == "w50"
