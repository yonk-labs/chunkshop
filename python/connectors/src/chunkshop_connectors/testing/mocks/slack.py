"""Hermetic Slack Web API mock for the slack connector.

Uses ``httpx.MockTransport`` (in-process, no socket) to route every HTTP
call the connector makes to an in-memory state machine. The connector's
``_transport`` attribute is set by tests to the ``transport`` exposed
on this fixture's handle, so all ``httpx.Client`` traffic
short-circuits before reaching the network.

Endpoints stubbed
-----------------
* ``GET /api/conversations.list`` — paginate channels.
* ``GET /api/conversations.history`` — paginate messages in a channel,
  honouring the ``oldest`` floor.
* ``GET /api/conversations.replies`` — paginate replies for a thread.
* ``GET /api/users.info`` — minimal user lookup (not used by the
  connector today, included for parity with the public docs).

JSON shapes match Slack's published reference (api.slack.com/methods/*).
The connector's correctness tests assert against these shapes so a
drift between this mock and Slack's real API would surface as a test
failure on the connector side, not silently.
"""
from __future__ import annotations

from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import httpx
import pytest


class _SlackMockHandle:
    """In-memory Slack state + the ``httpx.MockTransport`` driving it.

    Attributes
    ----------
    valid_config
        Connector config dict ready to feed ``factory()``. Includes
        ``oauth_tokens`` so the connector doesn't fall back to env.
    transport
        ``httpx.MockTransport`` instance — assigned by tests to the
        connector's ``_transport`` attribute.
    channels
        ``[{id, name}, ...]`` — channel directory.
    messages
        ``{channel_id: [message_dict, ...]}`` keyed by channel.
    thread_replies
        ``{(channel_id, thread_ts): [reply_dict, ...]}``.
    seen_tokens
        Set of bearer tokens the connector has sent. Tests assert
        env-token plumbing.
    page_size
        Pagination granularity for list/history/replies. Tests can
        shrink this to force pagination paths.
    """

    def __init__(self) -> None:
        self.channels: list[dict[str, Any]] = []
        self.messages: dict[str, list[dict[str, Any]]] = {}
        self.thread_replies: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self.users: dict[str, dict[str, Any]] = {}
        self.seen_tokens: set[str] = set()
        self.page_size: int = 200

        self.transport = httpx.MockTransport(self._dispatch)

        self.valid_config: dict[str, Any] = {
            "oauth_tokens": {
                "access_token": "xoxb-fake-bot",
                "refresh_token": None,
                "expires_at": "2099-01-01T00:00:00+00:00",
                "scopes": [
                    "channels:history",
                    "channels:read",
                    "users:read",
                    "team:read",
                ],
                "provider": "slack",
                "provider_extras": {},
            },
            # Absolute URL so MockTransport sees the request.
            "slack_base_url": "https://slack.mock/api",
        }

    # ------------------------------------------------------------------
    # Public test helpers
    # ------------------------------------------------------------------
    def add_channel(self, *, channel_id: str, name: str) -> None:
        """Register a channel and an empty message list for it."""
        self.channels.append({"id": channel_id, "name": name})
        self.messages.setdefault(channel_id, [])

    def add_message(
        self,
        *,
        channel_id: str,
        text: str,
        user: str = "U1",
        ts: Optional[str] = None,
        thread_ts: Optional[str] = None,
        reply_count: int = 0,
    ) -> str:
        """Append a top-level message to a channel. Returns its ts.

        ``ts`` auto-generated as a monotonic counter if omitted —
        enough resolution for cursor tests (fixed-width 6-digit µs
        so string sort matches numeric sort).
        """
        if ts is None:
            ts = self._next_ts(channel_id)
        msg: dict[str, Any] = {
            "type": "message",
            "ts": ts,
            "user": user,
            "text": text,
        }
        if thread_ts is not None:
            msg["thread_ts"] = thread_ts
        if reply_count:
            msg["reply_count"] = reply_count
        self.messages.setdefault(channel_id, []).append(msg)
        return ts

    def add_reply(
        self,
        *,
        channel_id: str,
        thread_ts: str,
        text: str,
        user: str = "U1",
        ts: Optional[str] = None,
    ) -> str:
        """Append a reply to a thread. Returns its ts."""
        if ts is None:
            ts = self._next_ts(channel_id)
        reply = {
            "type": "message",
            "ts": ts,
            "user": user,
            "text": text,
            "thread_ts": thread_ts,
        }
        self.thread_replies.setdefault(
            (channel_id, thread_ts), []
        ).append(reply)
        return ts

    def set_pagination(self, page_size: int) -> None:
        """Set the per-page limit for list/history/replies responses.

        Tests use this to force the connector through multi-page
        pagination paths without seeding 200+ items.
        """
        self.page_size = page_size

    # ------------------------------------------------------------------
    # Internal — ts generator
    # ------------------------------------------------------------------
    def _next_ts(self, channel_id: str) -> str:
        """Generate a monotonically-increasing Slack-style ts string.

        Format: ``"<sec>.<6-digit-usec>"``. The seconds component
        starts at a fixed epoch (1000000000) so tests across runs
        produce deterministic ts values when called in the same order.
        """
        prior = self.messages.get(channel_id, []) + [
            r
            for k, replies in self.thread_replies.items()
            if k[0] == channel_id
            for r in replies
        ]
        n = len(prior) + 1
        return f"{1700000000 + n}.{n:06d}"

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------
    def _record_token(self, request: httpx.Request) -> None:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            self.seen_tokens.add(auth[7:])

    def _dispatch(self, request: httpx.Request) -> httpx.Response:
        self._record_token(request)
        url = urlparse(str(request.url))
        path = url.path
        qs = parse_qs(url.query)

        if path.endswith("/conversations.list"):
            return self._handle_conversations_list(qs)
        if path.endswith("/conversations.history"):
            return self._handle_conversations_history(qs)
        if path.endswith("/conversations.replies"):
            return self._handle_conversations_replies(qs)
        if path.endswith("/users.info"):
            return self._handle_users_info(qs)
        return httpx.Response(
            200,
            json={"ok": False, "error": f"unknown_method:{path}"},
        )

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------
    def _paginate(
        self, items: list[Any], qs: dict[str, list[str]]
    ) -> tuple[list[Any], Optional[str]]:
        """Slice ``items`` by the current cursor + page_size.

        Slack-style: the cursor is the offset of the next page encoded
        as a string. Empty string in ``response_metadata.next_cursor``
        means "no more pages" — same as omitting the field.
        """
        cursor = qs.get("cursor", [""])[0]
        try:
            start = int(cursor) if cursor else 0
        except ValueError:
            start = 0
        page = items[start : start + self.page_size]
        if start + self.page_size < len(items):
            next_cursor = str(start + self.page_size)
        else:
            next_cursor = ""
        return page, next_cursor

    def _handle_conversations_list(
        self, qs: dict[str, list[str]]
    ) -> httpx.Response:
        page, next_cursor = self._paginate(self.channels, qs)
        return httpx.Response(
            200,
            json={
                "ok": True,
                "channels": page,
                "response_metadata": {"next_cursor": next_cursor},
            },
        )

    def _handle_conversations_history(
        self, qs: dict[str, list[str]]
    ) -> httpx.Response:
        channel_id = qs.get("channel", [""])[0]
        if not channel_id or channel_id not in self.messages:
            return httpx.Response(
                200,
                json={"ok": False, "error": "channel_not_found"},
            )
        oldest = qs.get("oldest", [""])[0]
        # Slack's history is newest-first. We mirror that. Filter by
        # oldest (exclusive — matches Slack's documented behaviour).
        msgs = list(self.messages[channel_id])
        if oldest:
            try:
                cutoff = float(oldest)
                msgs = [m for m in msgs if float(m["ts"]) > cutoff]
            except (TypeError, ValueError):
                pass
        msgs.sort(key=lambda m: m["ts"], reverse=True)
        page, next_cursor = self._paginate(msgs, qs)
        return httpx.Response(
            200,
            json={
                "ok": True,
                "messages": page,
                "has_more": bool(next_cursor),
                "response_metadata": {"next_cursor": next_cursor},
            },
        )

    def _handle_conversations_replies(
        self, qs: dict[str, list[str]]
    ) -> httpx.Response:
        channel_id = qs.get("channel", [""])[0]
        thread_ts = qs.get("ts", [""])[0]
        if not channel_id or not thread_ts:
            return httpx.Response(
                200,
                json={"ok": False, "error": "invalid_arguments"},
            )
        # Slack's replies endpoint returns the parent as the first
        # element of `messages`, followed by replies in chronological
        # order. Find the parent in the channel's history.
        parent = None
        for m in self.messages.get(channel_id, []):
            if m["ts"] == thread_ts:
                parent = m
                break
        if parent is None:
            return httpx.Response(
                200,
                json={"ok": False, "error": "thread_not_found"},
            )
        replies = list(self.thread_replies.get((channel_id, thread_ts), []))
        replies.sort(key=lambda m: m["ts"])
        items = [parent] + replies
        page, next_cursor = self._paginate(items, qs)
        return httpx.Response(
            200,
            json={
                "ok": True,
                "messages": page,
                "has_more": bool(next_cursor),
                "response_metadata": {"next_cursor": next_cursor},
            },
        )

    def _handle_users_info(self, qs: dict[str, list[str]]) -> httpx.Response:
        user_id = qs.get("user", [""])[0]
        u = self.users.get(user_id)
        if u is None:
            return httpx.Response(
                200, json={"ok": False, "error": "user_not_found"}
            )
        return httpx.Response(200, json={"ok": True, "user": u})


def make_slack_mock() -> _SlackMockHandle:
    """Return a fresh handle seeded with the default fixture set.

    Default seed:
    * Two public channels — C1 (general), C2 (random).
    * Two un-threaded messages in each channel.
    * No threads (tests that need threads add them via ``add_message``
      + ``add_reply``).
    """
    h = _SlackMockHandle()
    h.add_channel(channel_id="C1", name="general")
    h.add_channel(channel_id="C2", name="random")
    h.add_message(channel_id="C1", text="hello from general", user="U1")
    h.add_message(channel_id="C1", text="another general msg", user="U2")
    h.add_message(channel_id="C2", text="random talk one", user="U1")
    h.add_message(channel_id="C2", text="random talk two", user="U3")
    return h


@pytest.fixture
def slack_mock():
    """Provide a Slack mock seeded with two channels x two messages each."""
    return make_slack_mock()


__all__ = ["slack_mock", "make_slack_mock"]
