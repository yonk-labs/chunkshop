from chunkshop.summarizers.caveman import summarize


def test_strips_stopwords_keeps_content_words():
    out = summarize("the cat is on the mat and it is happy")
    assert out == "cat mat happy"


def test_empty_and_whitespace_return_empty():
    assert summarize("") == ""
    assert summarize("   \n  ") == ""


def test_idempotent():
    once = summarize("the quick brown fox jumps over the lazy dog")
    twice = summarize(once)
    assert once == twice


def test_preserves_token_order_and_case():
    out = summarize("The Eiffel Tower is in Paris")
    assert out == "Eiffel Tower Paris"


def test_search_command_exposes_compress_flag():
    from chunkshop.cli import search as search_cmd
    names = {p.name for p in search_cmd.params}
    assert "compress" in names
