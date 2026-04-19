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
    md = "# Section One\n\nalpha body text that is long enough to pass min_section_chars threshold of one hundred characters.\n\n# Section Two\n\nbeta body text that is also long enough to pass the min_section_chars threshold of one hundred characters."
    chunker = load_chunker(HierarchyChunker(type="hierarchy"))
    chunks = chunker.chunk(_doc(md))
    assert len(chunks) == 2
    assert chunks[0].embedded_content.startswith("Section One")
    assert "alpha body text" in chunks[0].embedded_content
    assert "alpha body text" in chunks[0].original_content
    assert not chunks[0].original_content.startswith("Section One")


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
