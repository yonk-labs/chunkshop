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
