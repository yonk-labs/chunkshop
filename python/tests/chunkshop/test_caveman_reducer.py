from chunkshop.summarizers.caveman import summarize


def test_strips_stopwords_keeps_content_words():
    out = summarize("the cat is on the mat and it is happy")
    assert out == "cat mat happy"


def test_empty_and_whitespace_return_empty():
    assert summarize("") == ""
    assert summarize("   \n  ") == ""


def test_idempotent():
    # Mixed case + retained trailing punctuation exercises the property that
    # makes idempotence non-obvious: kept tokens keep their punctuation.
    once = summarize("The cat, the dog, and the bird ran quickly.")
    twice = summarize(once)
    assert once == twice


def test_drops_orphaned_punctuation_tokens():
    assert summarize("hello , world . the cat") == "hello world cat"


def test_preserves_token_order_and_case():
    out = summarize("The Eiffel Tower is in Paris")
    assert out == "Eiffel Tower Paris"


def test_search_command_exposes_compress_flag():
    from chunkshop.cli import search as search_cmd
    names = {p.name for p in search_cmd.params}
    assert "compress" in names
