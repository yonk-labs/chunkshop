"""Unit tests for the zero-network default extractive consolidator."""
from chunkshop.consolidators.extractive import consolidate


def test_empty_input():
    out = consolidate("")
    assert out == {"summary": "", "facts": []}


def test_summary_is_nonempty_and_facts_are_propositions():
    text = ("[user] We decided to drop Redis and use Postgres for the queue.\n"
            "[assistant] Acknowledged. Postgres LISTEN/NOTIFY will replace it.")
    out = consolidate(text, max_sentences=2)
    assert isinstance(out["summary"], str) and out["summary"]
    assert len(out["facts"]) >= 1
    f = out["facts"][0]
    assert f["support_span"] and isinstance(f["support_span"], str)
    assert "subject" in f and "predicate" in f and "object" in f


def test_indented_tag_is_stripped():
    out = consolidate("   [assistant] The queue now uses Postgres for storage.",
                       min_words=3)
    assert out["facts"], "expected at least one proposition"
    span = out["facts"][0]["support_span"]
    assert "[assistant]" not in span
    assert span.startswith("The queue now uses Postgres")
