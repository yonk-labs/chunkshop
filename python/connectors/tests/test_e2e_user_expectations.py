"""End-to-end user-expectation tests.

These are the executable proof that each user-facing ingest path the
chunkshop CLAUDE.md promises actually works through to chunks. One
section per user expectation; mocks throughout (the global loopback
guard in ``conftest.py`` blocks any accidental egress).

Sections:

1. ``TestGoogleDriveExpectation`` — folder → docs → sentence_aware
   chunks; cursor refresh emits only new files.
2. ``TestGitHubExpectation``      — repo → files → code_aware chunks
   on .py; cursor advances on commit SHA.
3. ``TestS3Expectation``          — bucket → objects → sentence_aware
   chunks; ETag cursor skips unchanged objects.
4. ``TestUrlDepthExpectation``    — URL crawl at depth N; ETag cursor
   skips unchanged pages.
5. ``TestDatabaseExpectation``    — pg_table / sqlite_table /
   (optionally) mariadb_table regression smokes.

The DB section reuses the same env-DSN-skipif pattern that the
``python/tests/chunkshop`` integration tests use — see
``test_pg_table_incremental.py``. It only needs network for the
real Postgres / MariaDB / SQLite paths.
"""
from __future__ import annotations

import os
import types
import warnings
from typing import Any

import httpx
import pytest

from chunkshop.chunkers import load_chunker
from chunkshop.config import (
    CodeAwareChunker as CodeAwareCfg,
    SentenceAwareChunker as SentCfg,
)
from chunkshop.sources.base import IncrementalSource
from chunkshop.testing import merge_cursor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sentence_aware():
    return load_chunker(SentCfg(type="sentence_aware", min_chars=20, max_chars=400))


def _code_aware():
    # Tight thresholds so the fixture's tiny functions still emit
    # one chunk each.
    return load_chunker(CodeAwareCfg(type="code_aware", min_chars=20, max_chars=4000))


# ===========================================================================
# 1. Google Drive
# ===========================================================================


class TestGoogleDriveExpectation:
    """User: "sync from google drive (folder or docs). I expect to
    ingest once, if rerun and pass refresh, it only grabs and
    processes changes since the last run.\""""

    def _new_source(self, gdrive_mock):
        from chunkshop_connectors.gdrive import factory

        src = factory(gdrive_mock.valid_config)
        src._transport = gdrive_mock.transport
        src._reset_client()
        return src

    def test_first_sync_ingests_folder(self, gdrive_mock):
        src = self._new_source(gdrive_mock)
        assert isinstance(src, IncrementalSource)
        cursor = src.empty_cursor()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            docs = list(src.iter_changes_since(cursor))
        # Folder seeded with 1 google-doc + 1 text + 1 image (skipped).
        ids = {d.id for d in docs}
        assert ids == {"file-doc-1", "file-txt-1"}, f"first sync should hit both text files: {ids}"

    def test_second_sync_with_cursor_only_emits_changes(self, gdrive_mock):
        src = self._new_source(gdrive_mock)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            first = list(src.iter_changes_since(src.empty_cursor()))
        cursor = merge_cursor(src, src.empty_cursor(), first)
        assert cursor == {"page_token": gdrive_mock.start_page_token}

        # No file added → re-sync emits nothing.
        src2 = self._new_source(gdrive_mock)
        assert list(src2.iter_changes_since(cursor)) == []

        # Add a new file as a Drive change → only that file shows up.
        gdrive_mock.add_file(
            file_id="file-new-1",
            name="late.txt",
            mime_type="text/plain",
            content=b"late arrival content",
        )
        gdrive_mock.add_change("file-new-1", new_start_page_token="TOKEN_2")
        src3 = self._new_source(gdrive_mock)
        delta = list(src3.iter_changes_since(cursor))
        assert {d.id for d in delta} == {"file-new-1"}
        cursor = merge_cursor(src3, cursor, delta)
        assert cursor == {"page_token": "TOKEN_2"}

    def test_chunks_through_sentence_aware(self, gdrive_mock):
        src = self._new_source(gdrive_mock)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            docs = list(src.iter_documents())
        chunker = _sentence_aware()
        for doc in docs:
            chunks = chunker.chunk(doc)
            # Every text document must produce at least one chunk.
            assert len(chunks) >= 1, f"sentence_aware emitted 0 chunks for {doc.id!r}"
            for c in chunks:
                assert c.doc_id == doc.id
                assert c.original_content.strip(), "chunk content must not be empty"


# ===========================================================================
# 2. GitHub
# ===========================================================================


class TestGitHubExpectation:
    """User: "provide a GH repo, and have it ingested. I expect the
    chunker to be code aware… AST tree, and more. I expect to ingest
    once, if rerun and pass refresh, it only grabs and processes
    changes since the last run.\""""

    def test_first_sync_ingests_repo(self, github_mock):
        from chunkshop_connectors.github import factory
        src = factory(github_mock.valid_config)
        docs = list(src.iter_documents())
        # default fixture: README.md, src/a.py, docs/b.md (binary skipped)
        paths = sorted(d.id for d in docs)
        assert paths == ["README.md", "docs/b.md", "src/a.py"]

    def test_second_sync_with_cursor_only_emits_changed_files(self, github_mock):
        from chunkshop_connectors.github import factory
        src = factory(github_mock.valid_config)
        cursor = src.empty_cursor()
        docs = list(src.iter_changes_since(cursor))
        cursor = merge_cursor(src, cursor, docs)
        assert cursor == {"after_commit_sha": github_mock.head_sha}

        # No commit → re-sync emits nothing.
        src2 = factory(github_mock.valid_config)
        assert list(src2.iter_changes_since(cursor)) == []

        # Land a commit with a new .py file → only that file emits.
        github_mock.add_commit(
            new_head_sha="HEAD_AFTER_FUNC",
            changed_files=[("lib/util.py", b"def add(a, b):\n    return a + b\n")],
        )
        src3 = factory(github_mock.valid_config)
        delta = list(src3.iter_changes_since(cursor))
        assert {d.id for d in delta} == {"lib/util.py"}
        cursor = merge_cursor(src3, cursor, delta)
        assert cursor == {"after_commit_sha": "HEAD_AFTER_FUNC"}

    def test_python_files_chunk_at_function_boundaries(self, github_mock):
        """Add a .py file with 3 functions; verify code_aware emits
        per-function chunks (or fewer if combined under min_chars)."""
        from chunkshop_connectors.github import factory
        py_body = (
            "import math\n"
            "\n"
            "def alpha(x):\n"
            "    \"\"\"first function — at least 20 chars of body.\"\"\"\n"
            "    return x * 2 + math.pi\n"
            "\n"
            "\n"
            "def beta(y):\n"
            "    \"\"\"second function — also above the min_chars floor.\"\"\"\n"
            "    return y - 1.5 - math.e\n"
            "\n"
            "\n"
            "def gamma(z):\n"
            "    \"\"\"third function — keep it nontrivial too.\"\"\"\n"
            "    return z ** 2 + math.tau\n"
        ).encode()
        github_mock.files["lib/three.py"] = py_body

        src = factory(github_mock.valid_config)
        docs = list(src.iter_documents())
        three = next(d for d in docs if d.id == "lib/three.py")

        chunker = _code_aware()
        chunks = chunker.chunk(three)
        # The 3 functions each clear min_chars; expect one chunk per
        # function plus possibly a module_block chunk for imports/etc.
        strategies = {c.metadata.get("strategy", "") for c in chunks}
        assert strategies == {"code_aware"}, (
            f"code_aware emitted unexpected strategy markers: {strategies}"
        )
        names = {c.metadata.get("node_name") for c in chunks}
        assert {"alpha", "beta", "gamma"}.issubset(names), (
            f"code_aware did not split at function boundaries: names={names}, "
            f"chunks={len(chunks)}"
        )
        node_types = {c.metadata.get("node_type") for c in chunks}
        # function/AsyncFunctionDef chunks should be present.
        assert "function" in node_types or "FunctionDef" in node_types, (
            f"no function-typed chunk: {node_types}"
        )
        # And each function chunk's original_content should hold the function source.
        for c in chunks:
            name = c.metadata.get("node_name")
            if name in {"alpha", "beta", "gamma"}:
                assert f"def {name}" in c.original_content, (
                    f"chunk for {name!r} missing its def line"
                )


# ===========================================================================
# 3. S3 bucket
# ===========================================================================


class _FakeS3:
    """Lifted from python/tests/chunkshop/test_s3_incremental.py."""

    def __init__(self, objs):
        self.objs = objs  # list of (key, etag, body)

    def get_paginator(self, _):
        objs = self.objs

        class _P:
            def paginate(self, **kw):
                yield {
                    "Contents": [
                        {"Key": k, "ETag": e, "Size": len(b)} for k, e, b in objs
                    ]
                }

        return _P()

    def get_object(self, Bucket, Key):
        for k, e, b in self.objs:
            if k == Key:
                return {"Body": types.SimpleNamespace(read=lambda b=b: b), "ETag": e}
        raise KeyError(Key)


@pytest.fixture
def fake_s3_client(monkeypatch):
    """Install a fake ``boto3`` module so ``S3Source._client()`` returns ours.

    The handle has a ``set`` method tests call to swap the active object set.
    """
    holder: dict[str, Any] = {}

    fake = types.ModuleType("boto3")
    fake.client = lambda *a, **k: holder["client"]
    monkeypatch.setitem(__import__("sys").modules, "boto3", fake)

    handle = types.SimpleNamespace(
        set=lambda objs: holder.__setitem__("client", _FakeS3(objs)),
    )
    handle.set(
        [
            ("docs/intro.txt", '"e-intro"', b"Welcome to the bucket.\nIt is a hospitable place."),
            ("docs/guide.md", '"e-guide"', b"# Guide\n\nThis is the guide body, with enough text to chunk into at least one piece."),
        ]
    )
    return handle


class TestS3Expectation:
    """User: "point to an S3 bucket, and have all the files processed
    and chunks. Same scan for changes logic.\""""

    def _new_source(self):
        from chunkshop.config import S3Source as Cfg
        from chunkshop.sources.s3 import S3Source
        return S3Source(Cfg(type="s3", bucket="acme-bucket"))

    def test_first_sync_ingests_bucket(self, fake_s3_client):
        src = self._new_source()
        assert isinstance(src, IncrementalSource)
        docs = list(src.iter_changes_since(src.empty_cursor()))
        ids = {d.id for d in docs}
        assert ids == {
            "s3://acme-bucket/docs/intro.txt",
            "s3://acme-bucket/docs/guide.md",
        }

    def test_etag_cursor_skips_unchanged_objects(self, fake_s3_client):
        src = self._new_source()
        cursor = src.empty_cursor()
        first = list(src.iter_changes_since(cursor))
        cursor = merge_cursor(src, cursor, first)
        assert cursor == {
            "docs/intro.txt": '"e-intro"',
            "docs/guide.md": '"e-guide"',
        }
        # Nothing changed → no docs.
        assert list(src.iter_changes_since(cursor)) == []

        # Mutate one ETag → only that object re-emits.
        fake_s3_client.set(
            [
                ("docs/intro.txt", '"e-intro"', b"unchanged"),
                ("docs/guide.md", '"e-guide-v2"', b"# Guide v2\n\nFresh content here, with at least one sentence."),
            ]
        )
        delta = list(src.iter_changes_since(cursor))
        assert {d.id for d in delta} == {"s3://acme-bucket/docs/guide.md"}
        cursor = merge_cursor(src, cursor, delta)
        # The unchanged object's ETag is preserved.
        assert cursor["docs/intro.txt"] == '"e-intro"'
        assert cursor["docs/guide.md"] == '"e-guide-v2"'

    def test_chunks_through_sentence_aware(self, fake_s3_client):
        src = self._new_source()
        docs = list(src.iter_documents())
        chunker = _sentence_aware()
        for doc in docs:
            chunks = chunker.chunk(doc)
            assert len(chunks) >= 1, f"no chunks for {doc.id!r}"
            for c in chunks:
                assert c.doc_id == doc.id
                assert c.original_content.strip()


# ===========================================================================
# 4. URL with depth
# ===========================================================================


def _build_url_transport(routes, etag_state=None):
    """Build an httpx.MockTransport.

    ``routes`` may map URL → bare ``httpx.Response`` OR URL → callable
    ``(request) -> Response``. ``etag_state`` (when provided) lets a
    callable handler look up the current ETag for a path.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for key in (url, url.rstrip("/")):
            if key in routes:
                v = routes[key]
                return v(request) if callable(v) else v
        return httpx.Response(404, text=f"unknown route {url}")
    return httpx.MockTransport(handler)


def _html_response(body: str, *, etag: str | None = None) -> httpx.Response:
    headers = {"Content-Type": "text/html; charset=utf-8"}
    if etag:
        headers["ETag"] = etag
    return httpx.Response(200, text=body, headers=headers)


def _new_http_source(urls, transport, **kwargs):
    from chunkshop.config import HttpSource as Cfg
    from chunkshop.sources.http import HttpSource

    cfg_kwargs = {"request_delay_seconds": 0.0, "respect_robots": False}
    cfg_kwargs.update(kwargs)
    cfg = Cfg(type="http", urls=urls, **cfg_kwargs)
    return HttpSource(cfg, transport=transport)


class TestUrlDepthExpectation:
    """User: "point to a URL, pass in a depth… which is the number of
    links to follow and pull back and store all the info from that
    url. I expect an easy incremental.\""""

    def test_depth_zero_fetches_only_seed(self):
        routes = {
            "http://a.test/": _html_response(
                "<html><body><a href='/x'>x</a></body></html>"
            ),
            "http://a.test/x": _html_response("<html><body>x page</body></html>"),
        }
        src = _new_http_source(
            ["http://a.test/"], _build_url_transport(routes), crawl_depth=0
        )
        docs = list(src.iter_documents())
        assert [d.id for d in docs] == ["http://a.test/"]

    def test_depth_two_recursive_crawl(self):
        routes = {
            "http://a.test/": _html_response("<a href='/l1'>l1</a>"),
            "http://a.test/l1": _html_response("<a href='/l2'>l2</a>"),
            "http://a.test/l2": _html_response("leaf body content"),
            # depth=2 should NOT reach this:
            "http://a.test/l3": _html_response("should not be reached"),
        }
        src = _new_http_source(
            ["http://a.test/"], _build_url_transport(routes), crawl_depth=2
        )
        docs = list(src.iter_documents())
        ids = {d.id for d in docs}
        assert ids == {"http://a.test/", "http://a.test/l1", "http://a.test/l2"}

    def test_etag_cursor_skips_unchanged_pages(self):
        # /a serves a stable ETag. /b also starts stable. After first sync,
        # we mutate /b's ETag at the "server" → only /b re-emits on resync.
        state = {"etag_a": '"a1"', "etag_b": '"b1"'}

        def handler(req: httpx.Request) -> httpx.Response:
            url = str(req.url)
            key = {"http://a.test/a": "etag_a", "http://a.test/b": "etag_b"}.get(url)
            if key is None:
                return httpx.Response(404)
            current = state[key]
            if req.headers.get("If-None-Match") == current:
                return httpx.Response(304)
            return httpx.Response(
                200,
                text=f"<html><body>{key}={current}</body></html>",
                headers={"Content-Type": "text/html", "ETag": current},
            )

        src = _new_http_source(
            ["http://a.test/a", "http://a.test/b"],
            httpx.MockTransport(handler),
            crawl_depth=0,
        )
        cursor = src.empty_cursor()
        first = list(src.iter_changes_since(cursor))
        assert {d.id for d in first} == {"http://a.test/a", "http://a.test/b"}
        cursor = merge_cursor(src, cursor, first)

        # No mutation → nothing re-emits.
        assert list(src.iter_changes_since(cursor)) == []

        # Mutate /b → only /b re-emits.
        state["etag_b"] = '"b2"'
        delta = list(src.iter_changes_since(cursor))
        assert {d.id for d in delta} == {"http://a.test/b"}


# ===========================================================================
# 5. Database — existing connections continue to function
# ===========================================================================


# Postgres + MariaDB DSNs piggy-back on the same env vars the core test
# suite uses, with the same skipif-DSN-unreachable pattern.

PG_DSN = os.environ.get(
    "CHUNKSHOP_TEST_DSN",
    "postgresql://postgres:postgres@localhost:5434/chunkshop_test",
)
MARIADB_DSN_VAR = "CHUNKSHOP_TEST_DSN_MARIADB"
MARIADB_DSN = os.environ.get(MARIADB_DSN_VAR)


def _pg_reachable() -> bool:
    try:
        import psycopg
    except ImportError:
        return False
    try:
        with psycopg.connect(PG_DSN, connect_timeout=2):
            return True
    except Exception:
        return False


def _mariadb_reachable() -> bool:
    if not MARIADB_DSN:
        return False
    try:
        import pymysql  # noqa: F401
    except ImportError:
        return False
    try:
        from chunkshop.backends.mariadb import MariaDBBackend
        be = MariaDBBackend(dsn_env=MARIADB_DSN_VAR)
        with be.connect():
            return True
    except Exception:
        return False


class TestDatabaseExpectation:
    """User: "continue to function like they had previously." — regression
    smoke that the DB sources still produce documents through their
    Source / IncrementalSource interface.
    """

    @pytest.mark.skipif(not _pg_reachable(), reason="CHUNKSHOP_TEST_DSN unreachable")
    def test_pg_table_source_iter_documents(self):
        import psycopg

        from chunkshop.config import PgTableSource
        from chunkshop.sources.pg_table import PgTableSource as PgSrc

        schema = "public"
        name = "chunkshop_e2e_user_exp"
        with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {schema}.{name}")
            cur.execute(
                f"CREATE TABLE {schema}.{name} "
                f"(id text primary key, body text, updated_at timestamptz)"
            )
            cur.execute(
                f"INSERT INTO {schema}.{name} VALUES "
                f"('p','first body', now() - interval '2 hours'),"
                f"('q','second body', now() - interval '1 hour')"
            )
            conn.commit()
        try:
            src = PgSrc(
                PgTableSource(
                    type="pg_table",
                    dsn=PG_DSN,
                    database=schema,
                    table=name,
                    id_column="id",
                    content_column="body",
                    updated_at_column="updated_at",
                )
            )
            docs = list(src.iter_documents())
            assert {d.id for d in docs} == {"p", "q"}
        finally:
            with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS {schema}.{name}")
                conn.commit()

    @pytest.mark.skipif(not _pg_reachable(), reason="CHUNKSHOP_TEST_DSN unreachable")
    def test_pg_table_source_incremental(self):
        import psycopg

        from chunkshop.config import PgTableSource
        from chunkshop.sources.pg_table import PgTableSource as PgSrc

        schema = "public"
        name = "chunkshop_e2e_user_exp_inc"
        with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {schema}.{name}")
            cur.execute(
                f"CREATE TABLE {schema}.{name} "
                f"(id text primary key, body text, updated_at timestamptz)"
            )
            cur.execute(
                f"INSERT INTO {schema}.{name} VALUES "
                f"('a','aa', now() - interval '2 hours'),"
                f"('b','bb', now() - interval '1 hour')"
            )
            conn.commit()
        try:
            cfg = PgTableSource(
                type="pg_table",
                dsn=PG_DSN,
                database=schema,
                table=name,
                id_column="id",
                content_column="body",
                updated_at_column="updated_at",
            )
            src = PgSrc(cfg)
            first = list(src.iter_changes_since(src.empty_cursor()))
            assert {d.id for d in first} == {"a", "b"}
            cursor = merge_cursor(src, src.empty_cursor(), first)
            # Insert a fresher row → only it shows up on the next sync.
            with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
                cur.execute(f"INSERT INTO {schema}.{name} VALUES ('c','cc', now())")
                conn.commit()
            delta = list(src.iter_changes_since(cursor))
            assert {d.id for d in delta} == {"c"}, (
                "cursor refresh did not narrow to the new row"
            )
        finally:
            with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS {schema}.{name}")
                conn.commit()

    def test_sqlite_table_source(self, tmp_path, monkeypatch):
        sqlite_vec = pytest.importorskip("sqlite_vec")  # noqa: F841

        from chunkshop.backends.sqlite import SQLiteBackend
        from chunkshop.config import SqliteTableSource
        from chunkshop.sources.sqlite_table import SqliteTableSource as Source

        db = tmp_path / "src.db"
        monkeypatch.setenv("CHUNKSHOP_E2E_SQLITE_PATH", str(db))
        be = SQLiteBackend(dsn_env="CHUNKSHOP_E2E_SQLITE_PATH")
        with be.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                'CREATE TABLE "docs" (id TEXT PRIMARY KEY, body TEXT, lang TEXT)'
            )
            cur.executemany(
                'INSERT INTO "docs" VALUES (?, ?, ?)',
                [("x", "alpha body", "en"), ("y", "bravo body", "fr")],
            )
            conn.commit()

        cfg = SqliteTableSource(
            type="sqlite_table",
            dsn_env="CHUNKSHOP_E2E_SQLITE_PATH",
            database="ignored",
            table="docs",
            id_column="id",
            content_column="body",
            metadata_columns=["lang"],
        )
        docs = list(Source(cfg).iter_documents())
        by_id = {d.id: d for d in docs}
        assert set(by_id) == {"x", "y"}
        assert by_id["x"].content == "alpha body"

    @pytest.mark.skipif(
        not _mariadb_reachable(),
        reason=f"{MARIADB_DSN_VAR} not set or unreachable",
    )
    def test_mariadb_table_source(self):
        from chunkshop.backends.mariadb import MariaDBBackend
        from chunkshop.config import MariaDbTableSource
        from chunkshop.sources.mariadb_table import MariaDbTableSource as Source

        db_name = "chunkshop_e2e_user_exp"
        be = MariaDBBackend(dsn_env=MARIADB_DSN_VAR)
        with be.connect() as conn:
            cur = conn.cursor()
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{db_name}`")
            cur.execute(f"DROP TABLE IF EXISTS `{db_name}`.`docs`")
            cur.execute(
                f"CREATE TABLE `{db_name}`.`docs` "
                f"(id VARCHAR(64) PRIMARY KEY, body TEXT NOT NULL, lang VARCHAR(8))"
            )
            cur.execute(
                f"INSERT INTO `{db_name}`.`docs` VALUES (%s, %s, %s), (%s, %s, %s)",
                ("m1", "mariadb body 1", "en", "m2", "mariadb body 2", "fr"),
            )
            conn.commit()
        try:
            cfg = MariaDbTableSource(
                type="mariadb_table",
                dsn_env=MARIADB_DSN_VAR,
                database=db_name,
                table="docs",
                id_column="id",
                content_column="body",
                metadata_columns=["lang"],
            )
            docs = list(Source(cfg).iter_documents())
            assert {d.id for d in docs} == {"m1", "m2"}
        finally:
            with be.connect() as conn:
                cur = conn.cursor()
                cur.execute(f"DROP DATABASE `{db_name}`")
                conn.commit()
