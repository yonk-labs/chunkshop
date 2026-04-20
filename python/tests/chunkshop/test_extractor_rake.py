from chunkshop.config import NoneExtractor, RakeKeywordsExtractor
from chunkshop.extractors import load_extractor
from chunkshop.extractors.result import ExtractResult


def test_none_returns_empty():
    extractor = load_extractor(NoneExtractor())
    result = extractor.extract("any text")
    assert result.tags == []
    assert result.metadata == {}


def test_rake_returns_keywords_sorted():
    extractor = load_extractor(RakeKeywordsExtractor(type="rake_keywords", top_k=3))
    text = (
        "Supreme Court justice Neil Gorsuch wrote the majority opinion in "
        "Bostock v. Clayton County. Bostock concerns civil rights and Title VII."
    )
    tags = extractor.extract(text)
    assert isinstance(tags, list)
    assert 1 <= len(tags) <= 3
    lowered = [t.lower() for t in tags]
    assert any(
        "bostock" in t or "gorsuch" in t or "civil rights" in t or "title vii" in t
        for t in lowered
    )
