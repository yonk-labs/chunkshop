from __future__ import annotations

from chunkshop.config import RakeKeywordsExtractor as Cfg


class RakeKeywordsExtractor:
    """Local keyword extraction via rake-nltk. No LLM calls.

    nltk corpora (stopwords, punkt) are downloaded on first use and cached.
    """

    def __init__(self, cfg: Cfg):
        import nltk
        for resource in ("corpora/stopwords", "tokenizers/punkt", "tokenizers/punkt_tab"):
            try:
                nltk.data.find(resource)
            except LookupError:
                # resource name for nltk.download() is the basename after the last /
                nltk.download(resource.split("/")[-1], quiet=True)
        from rake_nltk import Rake
        self._rake_cls = Rake
        self.cfg = cfg

    def extract(self, text: str) -> list[str]:
        r = self._rake_cls(min_length=1)
        r.extract_keywords_from_text(text)
        ranked = r.get_ranked_phrases()
        return [p for p in ranked if len(p) >= self.cfg.min_chars][: self.cfg.top_k]
