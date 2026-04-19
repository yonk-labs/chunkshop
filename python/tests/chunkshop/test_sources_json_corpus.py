import json
from chunkshop.sources.json_corpus import JsonCorpusSource as Adapter
from chunkshop.config import JsonCorpusSource as Cfg


def test_reads_documents_list(tmp_path):
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps({
        "documents": [
            {"id": "d1", "content": "hello", "title": "H"},
            {"id": "d2", "content": "world", "title": "W"},
        ]
    }))
    adapter = Adapter(Cfg(type="json_corpus", path=str(path)))
    docs = list(adapter.iter_documents())
    assert len(docs) == 2
    assert docs[0].id == "d1"
    assert docs[0].content == "hello"
    assert docs[0].title == "H"
