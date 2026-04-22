from chunkshop.chunkers._splitting import split_to_max_chars


def test_returns_single_chunk_when_under_max():
    text = "short text"
    out = split_to_max_chars(text, max_chars=100)
    assert out == ["short text"]


def test_splits_on_paragraph_boundaries_when_possible():
    p1 = "First paragraph about alpha." + " alpha" * 50
    p2 = "Second paragraph about beta." + " beta" * 50
    p3 = "Third paragraph about gamma." + " gamma" * 50
    text = f"{p1}\n\n{p2}\n\n{p3}"
    out = split_to_max_chars(text, max_chars=len(p1) + 50)
    assert len(out) >= 2
    # No chunk should straddle a paragraph break
    for chunk in out:
        assert not (p1.strip() in chunk and p3.strip() in chunk)


def test_splits_on_sentence_boundaries_when_paragraph_too_big():
    # One paragraph, multiple sentences, whole paragraph exceeds max
    sentences = [f"This is sentence number {i}." for i in range(20)]
    text = " ".join(sentences)
    out = split_to_max_chars(text, max_chars=150)
    assert len(out) >= 2
    for chunk in out:
        assert len(chunk) <= 150
    # Each chunk should end with sentence punctuation (., !, ?) or be last
    for chunk in out[:-1]:
        assert chunk.rstrip().endswith((".", "!", "?"))


def test_falls_back_to_char_slice_when_no_punctuation():
    text = "a" * 500
    out = split_to_max_chars(text, max_chars=100)
    assert len(out) == 5
    for chunk in out:
        assert len(chunk) <= 100
    assert "".join(out) == text


def test_preserves_content_fully():
    text = "Paragraph one.\n\nParagraph two has more text here. Sentence two. Sentence three."
    out = split_to_max_chars(text, max_chars=40)
    # Whitespace-normalized concatenation must match whitespace-normalized input
    import re
    assert re.sub(r"\s+", " ", "".join(out)).strip() == re.sub(r"\s+", " ", text).strip()


def test_respects_max_strictly():
    # Even with pathological single-word text, no output chunk exceeds max
    text = "word " * 1000
    out = split_to_max_chars(text, max_chars=50)
    for chunk in out:
        assert len(chunk) <= 50
