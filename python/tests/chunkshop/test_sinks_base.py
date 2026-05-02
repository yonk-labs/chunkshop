from chunkshop.sinks.base import Sink


def test_sink_protocol_lists_required_methods():
    for attr in ("create_table", "write_document", "count_docs"):
        assert hasattr(Sink, attr)
