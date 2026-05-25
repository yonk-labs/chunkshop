from chunkshop_connectors._adapt import to_chunkshop_document
from chunkshop_connectors._tier import verified, experimental, tier_of
from chunkshop.sources.base import Document


class _RagDoc:  # stand-in for RAGFlow's Document shape
    def __init__(self):
        self.id = "g1"
        self.sections = [
            type("S", (), {"text": "hello "})(),
            type("S", (), {"text": "world"})(),
        ]
        self.semantic_identifier = "Title"
        self.metadata = {"k": "v"}


def test_to_chunkshop_document_concats_sections():
    d = to_chunkshop_document(_RagDoc())
    assert isinstance(d, Document)
    assert d.id == "g1"
    assert d.content == "hello world"
    assert d.title == "Title"
    assert d.metadata == {"k": "v"}


@verified
class _V:
    ...


@experimental
class _E:
    ...


def test_tier_markers():
    assert tier_of(_V) == "verified"
    assert tier_of(_E) == "experimental"
