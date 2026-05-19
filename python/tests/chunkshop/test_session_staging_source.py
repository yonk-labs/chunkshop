"""Unit tests for SessionStagingSource row→Document logic (no DB)."""
from chunkshop.config import SessionStagingSource as Cfg
from chunkshop.sources.session_staging import SessionStagingSource


def _cfg(mode):
    return Cfg(type="session_staging", dsn="postgresql://x/y",
              staging_table="evt", staging_schema="public", mode=mode,
              min_age_seconds=3600)


def _rows():
    # (event_id, session_id, seq, role, content, tool, outcome, event_ts)
    return [
        ("e1", "s1", 1, "user", "hello", None, None, 1000.0),
        ("e2", "s1", 2, "assistant", "hi back", "bash", "ok", 1005.0),
        ("e3", "s2", 1, "user", "other", None, None, 2000.0),
    ]


def test_groups_one_document_per_session():
    src = SessionStagingSource(_cfg("consolidate"))
    docs = list(src._documents_from_rows(_rows()))
    assert {d.id for d in docs} == {"s1", "s2"}
    s1 = next(d for d in docs if d.id == "s1")
    assert s1.metadata["session_id"] == "s1"
    assert s1.metadata["event_count"] == 2
    assert len(s1.metadata["_session_events"]) == 2
    assert [e["content"] for e in s1.metadata["_session_events"]] == ["hello", "hi back"]
    assert "hello" in s1.content and "hi back" in s1.content


def test_events_sorted_by_seq_then_ts():
    rows = [("e2", "s1", 2, "assistant", "second", None, None, 5.0),
            ("e1", "s1", 1, "user", "first", None, None, 9.0)]
    src = SessionStagingSource(_cfg("realtime"))
    doc = list(src._documents_from_rows(rows))[0]
    assert [e["content"] for e in doc.metadata["_session_events"]] == ["first", "second"]
