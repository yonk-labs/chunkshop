from chunkshop.search_common import Hit, summarize_hits


def _fake_summarize(text, **kwargs):
    return text


def _hit(text):
    return Hit(doc_id="d", seq_num=0, text=text, score=1.0, metadata={}, legs=("fts",))


def test_compress_fn_applied_to_summary():
    hits = [_hit("the cat is on the mat")]
    out = summarize_hits(hits, _fake_summarize, prepend_headings=False,
                         compress_fn=lambda s: s.replace("the ", ""))
    assert "the " not in out
    assert "cat" in out


def test_compress_fn_default_off():
    hits = [_hit("the cat is on the mat")]
    out = summarize_hits(hits, _fake_summarize, prepend_headings=False)
    assert out == "the cat is on the mat"
