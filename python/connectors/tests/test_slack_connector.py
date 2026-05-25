"""Behavioural tests for the verified slack connector.

Hermetic — ``slack_mock`` builds an ``httpx.MockTransport`` that serves
``conversations.list``, ``conversations.history``, ``conversations.replies``,
and ``users.info`` from in-memory state. No live network.

The autouse loopback-only socket guard in ``conftest.py`` is satisfied
because ``MockTransport`` never opens a real socket.
"""
from __future__ import annotations

import json

import pytest

from chunkshop.sources import registry
from chunkshop.sources.base import IncrementalSource

from chunkshop_connectors._tier import tier_of


def test_slack_registered_and_verified():
    registry.clear_cache()
    assert "slack" in registry.available_connectors()
    from chunkshop_connectors.slack import Connector
    assert tier_of(Connector) == "verified"


def test_slack_config_rejects_unknown_keys():
    from chunkshop_connectors.slack import ConfigModel

    # Sparse config is fine.
    ConfigModel.model_validate({})
    # Sensible config OK.
    ConfigModel.model_validate(
        {
            "channels": ["C1", "C2"],
            "oldest": 0.0,
            "oauth_tokens": {
                "access_token": "xoxb-x",
                "refresh_token": None,
                "expires_at": "2099-01-01T00:00:00+00:00",
                "scopes": ["channels:read"],
                "provider": "slack",
                "provider_extras": {},
            },
        }
    )
    # extra='forbid' on typos.
    with pytest.raises(Exception):
        ConfigModel.model_validate({"channels_typo": ["C1"]})


def test_slack_config_redacts_oauth_tokens_in_repr():
    from chunkshop_connectors.slack import ConfigModel

    cfg = ConfigModel.model_validate(
        {
            "oauth_tokens": {
                "access_token": "xoxb-SUPER-SECRET",
                "refresh_token": "xoxe-1-NEVER-LOG-ME",
                "expires_at": "2099-01-01T00:00:00+00:00",
                "scopes": ["channels:read"],
                "provider": "slack",
                "provider_extras": {},
            },
        }
    )
    r = repr(cfg)
    assert "SUPER-SECRET" not in r
    assert "NEVER-LOG-ME" not in r


def test_slack_yields_messages_against_mock(slack_mock):
    """Default fixture has 2 channels with 2 messages each → 4 documents."""
    from chunkshop_connectors.slack import factory

    src = factory(slack_mock.valid_config)
    src._transport = slack_mock.transport
    src._reset_client()

    docs = list(src.iter_documents())

    # 2 channels (C1=general, C2=random), 2 messages each → 4 docs.
    assert len(docs) == 4
    # ID shape: "<channel_id>::<ts>".
    for d in docs:
        assert "::" in d.id
        cid, ts = d.id.split("::", 1)
        assert cid.startswith("C")
        assert d.metadata["channel_id"] == cid
        assert d.metadata["ts"] == ts
        # channel_name carried through.
        assert d.metadata["channel_name"] in {"general", "random"}

    # Bot token plumbed into Authorization header.
    assert "xoxb-fake-bot" in slack_mock.seen_tokens


def test_slack_paginates_channels(slack_mock):
    """When the channel list paginates, all pages are walked."""
    from chunkshop_connectors.slack import factory

    # Force a tiny page size so the 2 default channels paginate.
    slack_mock.set_pagination(1)

    src = factory(slack_mock.valid_config)
    src._transport = slack_mock.transport
    src._reset_client()

    docs = list(src.iter_documents())
    # Still 4 docs (2 channels × 2 messages each).
    assert len(docs) == 4
    channels_seen = {d.metadata["channel_id"] for d in docs}
    assert channels_seen == {"C1", "C2"}


def test_slack_threads_emit_reply_docs(slack_mock):
    """A thread parent with replies yields one doc per reply in addition
    to the parent. ``thread_ts`` is set on every doc in the thread.
    """
    from chunkshop_connectors.slack import factory

    # Add a thread parent with two replies on C1.
    slack_mock.add_message(
        channel_id="C1",
        text="thread parent",
        user="U1",
        ts="2000.000",
        thread_ts="2000.000",
        reply_count=2,
    )
    slack_mock.add_reply(
        channel_id="C1",
        thread_ts="2000.000",
        text="first reply",
        user="U2",
        ts="2001.000",
    )
    slack_mock.add_reply(
        channel_id="C1",
        thread_ts="2000.000",
        text="second reply",
        user="U2",
        ts="2002.000",
    )

    src = factory(slack_mock.valid_config)
    src._transport = slack_mock.transport
    src._reset_client()

    docs = list(src.iter_documents())
    # Default 4 docs + 1 parent + 2 replies = 7.
    assert len(docs) == 7
    thread_docs = [d for d in docs if d.metadata.get("thread_ts") == "2000.000"]
    # Parent + 2 replies — 3 docs in the thread.
    assert len(thread_docs) == 3
    by_ts = {d.metadata["ts"]: d for d in thread_docs}
    assert "first reply" in by_ts["2001.000"].content
    assert "second reply" in by_ts["2002.000"].content


def test_slack_incremental_via_ts_cursor(slack_mock):
    """Cursor is per-channel ``{channel_id: max_ts}``. Subsequent sync
    only fetches messages newer than the prior max.
    """
    from chunkshop_connectors.slack import factory

    src1 = factory(slack_mock.valid_config)
    src1._transport = slack_mock.transport
    src1._reset_client()

    cursor = src1.empty_cursor()
    assert cursor == {}
    docs1 = list(src1.iter_changes_since(cursor))
    assert len(docs1) == 4

    advanced = dict(cursor)
    for d in docs1:
        advanced.update(src1.cursor_from(d))
    # advanced now carries the highest ts per channel.
    assert set(advanced.keys()) == {"C1", "C2"}

    # Add one new message to C1 with a later ts than anything seeded.
    # The default mock seeds ts ~1700000001-ish; use the mock's own
    # generator so ordering is deterministic.
    new_ts = slack_mock.add_message(
        channel_id="C1",
        text="brand new",
        user="U1",
    )

    src2 = factory(slack_mock.valid_config)
    src2._transport = slack_mock.transport
    src2._reset_client()
    new_docs = list(src2.iter_changes_since(advanced))
    # Only the new C1 message comes through.
    assert len(new_docs) == 1
    assert new_docs[0].metadata["channel_id"] == "C1"
    assert new_docs[0].metadata["ts"] == new_ts
    assert "brand new" in new_docs[0].content


def test_slack_satisfies_incremental_helpers(slack_mock):
    """Standard chunkshop testing helpers — cursor advance + idempotent re-emit."""
    from chunkshop.testing import (
        assert_cursor_advances,
        assert_idempotent_on_re_emit,
    )
    from chunkshop_connectors.slack import factory

    src = factory(slack_mock.valid_config)
    src._transport = slack_mock.transport
    src._reset_client()
    assert isinstance(src, IncrementalSource)
    assert_cursor_advances(src)

    src2 = factory(slack_mock.valid_config)
    src2._transport = slack_mock.transport
    src2._reset_client()
    assert_idempotent_on_re_emit(src2)


def test_slack_oauth_tokens_from_env(slack_mock, monkeypatch):
    """If ``oauth_tokens`` is omitted, the connector reads ``SLACK_OAUTH_TOKENS``."""
    from chunkshop_connectors.slack import factory

    cfg = dict(slack_mock.valid_config)
    tokens = cfg.pop("oauth_tokens")
    monkeypatch.setenv("SLACK_OAUTH_TOKENS", json.dumps(tokens))

    src = factory(cfg)
    src._transport = slack_mock.transport
    src._reset_client()

    docs = list(src.iter_documents())
    assert len(docs) == 4
    assert "xoxb-fake-bot" in slack_mock.seen_tokens


def test_slack_empty_channels_lists_all_accessible(slack_mock):
    """``channels: None`` → call ``conversations.list`` and ingest every
    channel the bot can see.
    """
    from chunkshop_connectors.slack import factory

    cfg = dict(slack_mock.valid_config)
    cfg.pop("channels", None)  # not present anyway, but be explicit
    src = factory(cfg)
    src._transport = slack_mock.transport
    src._reset_client()

    docs = list(src.iter_documents())
    channels_seen = {d.metadata["channel_id"] for d in docs}
    # The mock seeds two channels by default.
    assert channels_seen == {"C1", "C2"}


def test_slack_missing_oauth_tokens_raises_on_first_call(slack_mock, monkeypatch):
    """When neither config nor env supplies tokens, ValueError on first API call."""
    from chunkshop_connectors.slack import factory

    cfg = dict(slack_mock.valid_config)
    cfg.pop("oauth_tokens")
    monkeypatch.delenv("SLACK_OAUTH_TOKENS", raising=False)

    src = factory(cfg)
    # Construction does not raise — resolution is lazy.
    with pytest.raises(ValueError, match="oauth_tokens"):
        list(src.iter_documents())
