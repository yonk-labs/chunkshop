"""Verified Slack connector (OAuth bot-token auth).

Walks the channels the OAuth bot can see and yields one chunkshop
``Document`` per **message** — thread parents and replies each get
their own ``Document``. Backed by raw ``httpx`` against Slack's Web
API — no ``slack-sdk`` dependency (smaller surface, easier to mock
hermetically).

Endpoints consumed
------------------
* ``GET /api/conversations.list?types=public_channel,private_channel&cursor=...``
    Paginate through channels the bot can read.
* ``GET /api/conversations.history?channel=<C>&oldest=<O>&cursor=...``
    Paginate through messages in a channel newer than ``oldest``.
* ``GET /api/conversations.replies?channel=<C>&ts=<T>&cursor=...``
    Walk thread replies hanging off a parent message.

Sync semantics
--------------
``sync_mode = SyncMode.CURSOR``. The cursor shape is a per-channel map
of the highest ``ts`` seen so far::

    {"C0123456789": "1700000000.000100",
     "C0987654321": "1700000005.000200"}

* Empty cursor → full walk from ``config.oldest`` (or beginning of
  time if not set).
* Non-empty cursor → per-channel ``oldest=cursor[channel_id]`` so we
  only fetch messages strictly newer than the last seen ``ts``.

This is **merge-delta** semantics (see
``chunkshop.testing.merge_cursor``): every emitted document
contributes a ``{channel_id: ts}`` entry. Channels not present in the
prior cursor get fresh entries on first emit; channels that did emit
keep their highest ts.

Slack ``ts`` strings sort lexicographically the same as numerically
(fixed-width seconds + microseconds), so we lean on string max
throughout.

Error handling
--------------
Slack returns HTTP 200 even on errors — ``{"ok": false, "error":
"..."}``. We check ``ok`` after every call and raise on false. Rate
limit handling (``ratelimited``) is **not** implemented in v1; the
connector raises immediately. Slack's tier-1 ``conversations.history``
limit is 1 call/min for non-paid workspaces, 50/min for marketplace
apps — see the docs link in this module for the full table.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Iterable, Iterator, Optional

import httpx

from chunkshop.sources.base import Document, SyncMode

from chunkshop_connectors._tier import verified

logger = logging.getLogger(__name__)


class SlackAPIError(Exception):
    """Raised when Slack returns ``{"ok": false}`` from a Web API call.

    The exception message carries the ``error`` slug from the response so
    callers can branch on it (``ratelimited``, ``not_in_channel``,
    ``channel_not_found``, etc.).
    """


@verified
class SlackConnector:
    """Verified-tier Slack connector (cursor sync, OAuth bot-token auth)."""

    sync_mode = SyncMode.CURSOR

    def __init__(self, config: dict[str, Any]) -> None:
        self.channels: Optional[list[str]] = config.get("channels")
        self.oldest: Optional[float] = config.get("oldest")
        self._config_oauth_tokens: Optional[dict] = config.get("oauth_tokens")
        self.base_url: str = config.get(
            "slack_base_url", "https://slack.com/api"
        ).rstrip("/")

        # Test hook — production leaves this None.
        self._transport: Optional[httpx.BaseTransport] = None
        self._client = httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0))

        # Cache of channel id → channel name, populated on first
        # conversations.list call. Stamped onto every emitted doc's
        # metadata so downstream code doesn't have to join.
        self._channel_names: dict[str, str] = {}

    def _reset_client(self) -> None:
        """Recreate the httpx client honouring ``self._transport``.

        Tests assign ``connector._transport`` then call this so the
        ``MockTransport`` starts intercepting requests immediately.
        """
        self._client = httpx.Client(
            transport=self._transport,
            timeout=httpx.Timeout(30.0, connect=10.0),
        )

    def __repr__(self) -> str:  # pragma: no cover - defensive
        return (
            f"SlackConnector(channels={self.channels!r}, "
            f"oldest={self.oldest!r}, oauth_tokens=<redacted>)"
        )

    # ------------------------------------------------------------------
    # OAuth resolution
    # ------------------------------------------------------------------
    def _resolve_access_token(self) -> str:
        """Resolve the bot OAuth access_token lazily.

        Precedence: config ``oauth_tokens`` > ``$SLACK_OAUTH_TOKENS`` env
        var (JSON-encoded). Raises ``ValueError`` if neither is set —
        we don't pre-flight in ``__init__`` so config validation doesn't
        depend on runtime env.
        """
        tokens = self._config_oauth_tokens
        if tokens is None:
            raw = os.environ.get("SLACK_OAUTH_TOKENS")
            if raw is None:
                raise ValueError(
                    "slack: oauth_tokens missing from config and "
                    "$SLACK_OAUTH_TOKENS env var is unset. Pass tokens via "
                    "ConfigModel.oauth_tokens or set the env var."
                )
            try:
                tokens = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"slack: $SLACK_OAUTH_TOKENS is not valid JSON: {exc}"
                ) from None
        access = tokens.get("access_token") if isinstance(tokens, dict) else None
        if not access:
            raise ValueError("slack: oauth_tokens has no access_token")
        return access

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._resolve_access_token()}"}

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------
    def _get_json(self, path: str, **params: Any) -> dict[str, Any]:
        """GET a Slack Web API endpoint and return the parsed body.

        Raises ``SlackAPIError`` when the body's ``ok`` is false. Slack
        always returns HTTP 200 even on errors, so we can't rely on
        ``raise_for_status`` alone.
        """
        url = f"{self.base_url}{path}"
        resp = self._client.get(
            url, headers=self._auth_headers(), params=params
        )
        resp.raise_for_status()
        body = resp.json()
        if not body.get("ok", False):
            raise SlackAPIError(
                f"slack: {path} failed: {body.get('error', 'unknown_error')}"
            )
        return body

    # ------------------------------------------------------------------
    # Channel enumeration
    # ------------------------------------------------------------------
    def _list_channels(self) -> Iterator[dict[str, Any]]:
        """Paginate through conversations.list.

        Slack's response shape::

            {"ok": true,
             "channels": [{"id": "C1", "name": "general", ...}, ...],
             "response_metadata": {"next_cursor": "..." or ""}}

        An empty (or missing) ``next_cursor`` ends pagination.
        """
        cursor: Optional[str] = None
        while True:
            params: dict[str, Any] = {
                "types": "public_channel,private_channel",
                "limit": 200,
            }
            if cursor:
                params["cursor"] = cursor
            data = self._get_json("/conversations.list", **params)
            for ch in data.get("channels", []):
                # Stamp the name cache here so history/replies don't
                # need to re-fetch metadata.
                self._channel_names[ch["id"]] = ch.get("name", ch["id"])
                yield ch
            cursor = (
                data.get("response_metadata", {}).get("next_cursor") or None
            )
            if not cursor:
                return

    def _resolve_channels(self) -> list[dict[str, Any]]:
        """Return the list of channel records to ingest from.

        * ``config.channels == None`` → walk conversations.list and
          ingest every channel returned.
        * Otherwise treat the configured entries as channel IDs and
          synthesise minimal records (name resolved lazily as messages
          flow through).
        """
        if self.channels is None:
            return list(self._list_channels())
        # Configured channel IDs — synthesise records so the message
        # loop can treat both branches uniformly.
        return [
            {"id": cid, "name": self._channel_names.get(cid, cid)}
            for cid in self.channels
        ]

    # ------------------------------------------------------------------
    # Message walking
    # ------------------------------------------------------------------
    def _iter_messages(
        self,
        channel_id: str,
        channel_name: str,
        *,
        oldest: float | str,
    ) -> Iterator[Document]:
        """Paginate conversations.history for a channel + emit thread replies.

        Yields one Document per message (and per reply). ``oldest`` is
        the floor — Slack's API treats ``oldest`` as exclusive, so the
        per-channel cursor's max-ts safely doesn't re-emit prior
        messages.

        Cursor-merge correctness
        ------------------------
        Slack's ``conversations.history`` returns newest-first. To make
        the standard merge-delta pattern (``dict.update`` in iteration
        order — last writer wins) converge on the highest ts for the
        channel, we accumulate messages across pages first, then sort
        them ascending-by-ts and yield in that order. The last doc
        emitted for the channel is therefore the highest-ts message,
        and ``cursor_from`` of it produces the correct delta. Threads
        are emitted interleaved (parent first, replies after) — replies
        always have ``ts > parent.ts`` so the per-channel max stays
        monotonic.
        """
        # Collect all top-level messages first so we can sort
        # ascending-by-ts. Slack history is newest-first; we need
        # oldest-first for the merge-delta cursor to settle correctly.
        # Channels with very large backlogs pay a memory cost here in
        # exchange for cursor correctness — fine for v1; v2 can stream
        # with a running max-ts side-channel if it matters.
        messages: list[dict[str, Any]] = []
        cursor: Optional[str] = None
        while True:
            params: dict[str, Any] = {"channel": channel_id, "limit": 200}
            if oldest:
                params["oldest"] = str(oldest)
            if cursor:
                params["cursor"] = cursor
            data = self._get_json("/conversations.history", **params)
            messages.extend(data.get("messages", []))
            cursor = (
                data.get("response_metadata", {}).get("next_cursor") or None
            )
            if not cursor:
                break

        # Sort ascending by ts so the last yielded doc has the max ts.
        # Slack ts are fixed-width "<sec>.<usec>" strings — string sort
        # matches numeric sort.
        messages.sort(key=lambda m: m.get("ts", ""))

        for msg in messages:
            doc = self._message_to_document(
                msg, channel_id=channel_id, channel_name=channel_name
            )
            if doc is not None:
                yield doc
            ts = msg.get("ts")
            thread_ts = msg.get("thread_ts")
            reply_count = msg.get("reply_count") or 0
            if (
                ts is not None
                and thread_ts == ts
                and reply_count > 0
            ):
                yield from self._iter_thread_replies(
                    channel_id=channel_id,
                    channel_name=channel_name,
                    thread_ts=ts,
                )

    def _iter_thread_replies(
        self,
        *,
        channel_id: str,
        channel_name: str,
        thread_ts: str,
    ) -> Iterator[Document]:
        """Paginate conversations.replies for one thread.

        Slack returns the parent as the first element of ``messages``
        — we skip it (the parent was already yielded by the history
        loop) and emit only the replies. ``thread_ts == ts`` is the
        check Slack docs prescribe for "is this the parent?".
        """
        # Collect across pages then sort ascending-by-ts so the last
        # reply emitted is the latest — same monotonic-cursor
        # discipline as _iter_messages.
        replies: list[dict[str, Any]] = []
        cursor: Optional[str] = None
        while True:
            params: dict[str, Any] = {
                "channel": channel_id,
                "ts": thread_ts,
                "limit": 200,
            }
            if cursor:
                params["cursor"] = cursor
            data = self._get_json("/conversations.replies", **params)
            for msg in data.get("messages", []):
                # Skip the parent — history already emitted it.
                if msg.get("ts") == thread_ts and msg.get("thread_ts") == thread_ts:
                    continue
                replies.append(msg)
            cursor = (
                data.get("response_metadata", {}).get("next_cursor") or None
            )
            if not cursor:
                break

        replies.sort(key=lambda m: m.get("ts", ""))
        for msg in replies:
            doc = self._message_to_document(
                msg, channel_id=channel_id, channel_name=channel_name
            )
            if doc is not None:
                yield doc

    # ------------------------------------------------------------------
    # Doc construction
    # ------------------------------------------------------------------
    def _message_to_document(
        self,
        msg: dict[str, Any],
        *,
        channel_id: str,
        channel_name: str,
    ) -> Optional[Document]:
        """Convert a Slack message into a chunkshop Document.

        Slack messages with no ``text`` (e.g., file-share-only,
        bot-payload-only) get an empty content string — we still emit
        them so downstream code can decide whether to skip. Filtering
        based on content emptiness belongs in the chunker, not here.
        """
        ts = msg.get("ts")
        if ts is None:
            return None
        text = msg.get("text", "") or ""
        metadata: dict[str, Any] = {
            "channel_id": channel_id,
            "channel_name": channel_name,
            "user_id": msg.get("user"),
            "ts": ts,
        }
        # thread_ts is None on un-threaded messages; only set when present.
        thread_ts = msg.get("thread_ts")
        if thread_ts is not None:
            metadata["thread_ts"] = thread_ts
        # Subtype (e.g. "channel_join", "bot_message") helps downstream
        # filter join/leave noise out — carry it through if Slack set it.
        subtype = msg.get("subtype")
        if subtype is not None:
            metadata["subtype"] = subtype
        return Document(
            id=f"{channel_id}::{ts}",
            content=text,
            title=f"{channel_name}:{ts}",
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Public Source / IncrementalSource surface
    # ------------------------------------------------------------------
    def iter_documents(self) -> Iterator[Document]:
        """Full walk — every channel + history + threads from ``oldest``."""
        floor = self.oldest if self.oldest is not None else 0.0
        for ch in self._resolve_channels():
            cid = ch["id"]
            cname = ch.get("name") or self._channel_names.get(cid, cid)
            yield from self._iter_messages(cid, cname, oldest=floor)

    # ---- IncrementalSource -------------------------------------------
    def empty_cursor(self) -> dict:
        return {}

    def iter_changes_since(self, cursor: dict) -> Iterable[Document]:
        """Per-channel oldest = cursor.get(channel_id, config.oldest or 0).

        Channels in the cursor get their last-seen ts as the floor;
        unseen channels use ``config.oldest`` (or 0). Slack's
        ``oldest`` is exclusive so we never re-emit the boundary
        message.
        """
        default_floor = self.oldest if self.oldest is not None else 0.0
        for ch in self._resolve_channels():
            cid = ch["id"]
            cname = ch.get("name") or self._channel_names.get(cid, cid)
            prior_ts = cursor.get(cid)
            if prior_ts is not None:
                # Cursor entries are ts strings (e.g. "1700000000.000100").
                # Slack expects oldest as a string or float; either is fine.
                floor: float | str = prior_ts
            else:
                floor = default_floor
            yield from self._iter_messages(cid, cname, oldest=floor)

    def cursor_from(self, last_document: Document) -> dict:
        """Per-document delta — one ``{channel_id: ts}`` entry.

        Merge-delta semantics: ``chunkshop.testing.merge_cursor`` folds
        these into the running cursor via ``dict.update`` (last writer
        wins). The connector sorts each channel's messages
        ascending-by-ts before emitting (see ``_iter_messages``), so
        the last doc yielded per channel carries the highest ts —
        which is the cursor value we want.

        Slack ``ts`` strings are fixed-width ``"<sec>.<usec>"`` and
        sort lexicographically the same as numerically, so the cursor
        stays correct without any ad-hoc parsing.
        """
        meta = last_document.metadata or {}
        cid = meta.get("channel_id")
        ts = meta.get("ts")
        if cid is None or ts is None:
            return {}
        return {cid: ts}
