"""Tests for summarizer config models + SummaryEmbedChunker / HierarchicalSummaryChunker."""
import pytest
from pydantic import ValidationError

from chunkshop.config import (
    ExternalSummarizer,
    CallableSummarizer,
    PassthroughSummarizer,
    SummaryEmbedChunker,
    HierarchicalSummaryChunker,
    FixedNGrouping,
    WordBudgetGrouping,
    SectionAwareGrouping,
)


def test_external_summarizer_requires_field():
    s = ExternalSummarizer(mode="external", field="summary")
    assert s.field == "summary"


def test_external_summarizer_default_field():
    s = ExternalSummarizer(mode="external")
    assert s.field == "summary"


def test_callable_summarizer_requires_module_function():
    s = CallableSummarizer(
        mode="callable",
        module="lede",
        function="summarize",
        kwargs={"max_length": 200},
    )
    assert s.module == "lede"
    assert s.function == "summarize"
    assert s.kwargs["max_length"] == 200


def test_passthrough_summarizer_has_no_fields():
    s = PassthroughSummarizer(mode="passthrough")
    assert s.mode == "passthrough"


def test_summary_embed_chunker_discriminates_summarizer():
    cfg = SummaryEmbedChunker(
        type="summary_embed",
        base={"type": "hierarchy"},
        summarizer={"mode": "passthrough"},
    )
    assert cfg.summarizer.mode == "passthrough"


def test_summary_embed_chunker_with_callable():
    cfg = SummaryEmbedChunker(
        type="summary_embed",
        base={"type": "sentence_aware"},
        summarizer={"mode": "callable", "module": "lede", "function": "summarize"},
    )
    assert cfg.summarizer.module == "lede"


def test_hierarchical_summary_default_grouping_is_fixed_n():
    cfg = HierarchicalSummaryChunker(
        type="hierarchical_summary",
        base={"type": "sentence_aware"},
        summarizer={"mode": "passthrough"},
    )
    assert isinstance(cfg.grouping, FixedNGrouping)
    assert cfg.grouping.n == 5


def test_hierarchical_summary_word_budget_grouping():
    cfg = HierarchicalSummaryChunker(
        type="hierarchical_summary",
        base={"type": "sentence_aware"},
        summarizer={"mode": "passthrough"},
        grouping={"strategy": "word_budget", "max_words": 1500},
    )
    assert isinstance(cfg.grouping, WordBudgetGrouping)
    assert cfg.grouping.max_words == 1500


def test_hierarchical_summary_section_aware_requires_hierarchy_base():
    """section_aware grouping requires base.type='hierarchy' at config-load."""
    with pytest.raises(ValidationError, match="section_aware"):
        HierarchicalSummaryChunker(
            type="hierarchical_summary",
            base={"type": "sentence_aware"},  # NOT hierarchy
            summarizer={"mode": "passthrough"},
            grouping={"strategy": "section_aware"},
        )


def test_hierarchical_summary_section_aware_ok_with_hierarchy_base():
    cfg = HierarchicalSummaryChunker(
        type="hierarchical_summary",
        base={"type": "hierarchy"},
        summarizer={"mode": "passthrough"},
        grouping={"strategy": "section_aware"},
    )
    assert isinstance(cfg.grouping, SectionAwareGrouping)


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        SummaryEmbedChunker(
            type="summary_embed",
            base={"type": "hierarchy"},
            summarizer={"mode": "passthrough"},
            extra_field="oops",
        )
