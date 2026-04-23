"""sumy adapter — wraps parser + algorithm + sentence-list API into one callable.

sumy's native flow is multi-step:
    parser = PlaintextParser.from_string(text, Tokenizer(language))
    summarizer = LexRankSummarizer()  # or TextRank, LSA, Luhn, KL, Edmundson
    sentences = summarizer(parser.document, sentences_count=3)
    # sentences is a list of sentence objects; join str(s) for text.

This shim hides all of that behind the canonical
``summarize(text: str, **kwargs) -> str`` contract so user YAML can reference it
uniformly via ``module: chunkshop.summarizers.sumy``.
"""
from __future__ import annotations


_ALGORITHM_IMPORTS = {
    "lex_rank": "sumy.summarizers.lex_rank.LexRankSummarizer",
    "text_rank": "sumy.summarizers.text_rank.TextRankSummarizer",
    "lsa": "sumy.summarizers.lsa.LsaSummarizer",
    "luhn": "sumy.summarizers.luhn.LuhnSummarizer",
    "kl": "sumy.summarizers.kl.KLSummarizer",
    "edmundson": "sumy.summarizers.edmundson.EdmundsonSummarizer",
}


def _load_algorithm(name: str):
    if name not in _ALGORITHM_IMPORTS:
        raise ValueError(
            f"unknown sumy algorithm {name!r}; choose one of {sorted(_ALGORITHM_IMPORTS)}"
        )
    from importlib import import_module
    module_path, cls_name = _ALGORITHM_IMPORTS[name].rsplit(".", 1)
    return getattr(import_module(module_path), cls_name)


def summarize(
    text: str,
    *,
    algorithm: str = "lex_rank",
    sentences_count: int = 3,
    language: str = "english",
) -> str:
    """Extractive summarization via sumy's pluggable algorithms.

    Args:
        text: Input text to summarize.
        algorithm: One of ``lex_rank`` (default), ``text_rank``, ``lsa``, ``luhn``,
            ``kl``, ``edmundson``. Unknown names raise ``ValueError``.
        sentences_count: Number of sentences to keep in the summary.
        language: NLTK language code (``english``, ``french``, ...).

    Returns:
        Space-joined summary string, or ``""`` for empty input.
    """
    if not text or not text.strip():
        return ""
    from sumy.parsers.plaintext import PlaintextParser
    from sumy.nlp.tokenizers import Tokenizer

    algo_cls = _load_algorithm(algorithm)
    parser = PlaintextParser.from_string(text, Tokenizer(language))
    algo = algo_cls()
    sentences = algo(parser.document, sentences_count=sentences_count)
    return " ".join(str(s) for s in sentences)


__all__ = ["summarize"]
